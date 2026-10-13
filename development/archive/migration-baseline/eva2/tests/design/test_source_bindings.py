from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
TOOLS = REPOSITORY_ROOT / "eva2/tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import verify_source_bindings as bindings  # noqa: E402


class SourceBindingTests(unittest.TestCase):
    def _fixture(
        self,
    ) -> tuple[tempfile.TemporaryDirectory[str], Path, dict[str, str], dict[str, str]]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / "eva2/release").mkdir(parents=True)
        payloads = {
            "sources/alpha.txt": b"alpha\n",
            "sources/beta.json": b'{"beta":true}\n',
        }
        expected: dict[str, str] = {}
        expected_ids: dict[str, str] = {}
        rows: list[dict[str, str]] = []
        for index, (relative, payload) in enumerate(payloads.items(), start=1):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()
            identifier = f"source_{index}"
            expected[relative] = digest
            expected_ids[relative] = identifier
            rows.append({"id": identifier, "path": relative, "sha256": digest})
        manifest = {
            "contract": bindings.CONTRACT,
            "pathPolicy": "repository_relative_only",
            "secretsAllowed": False,
            "legacyInputsAllowed": False,
            "bindingCount": len(rows),
            "bindings": rows,
        }
        (root / bindings.DEFAULT_MANIFEST).write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        return temporary, root, expected, expected_ids

    def test_repository_manifest_is_the_exact_sealed_ten_source_set(self) -> None:
        receipt = bindings.verify(REPOSITORY_ROOT)
        self.assertEqual("passed", receipt["status"], receipt["errors"])
        self.assertEqual(10, receipt["expectedCount"])
        self.assertEqual(10, receipt["actualCount"])
        self.assertEqual(
            set(bindings.EXPECTED_BINDINGS),
            {row["path"] for row in receipt["verifiedBindings"]},
        )

    def test_fixture_passes_and_fingerprint_covers_ids_paths_and_digests(self) -> None:
        temporary, root, expected, expected_ids = self._fixture()
        self.addCleanup(temporary.cleanup)
        receipt = bindings.verify(
            root, expected_bindings=expected, expected_ids=expected_ids
        )
        self.assertEqual("passed", receipt["status"], receipt["errors"])
        changed_rows = [dict(row) for row in receipt["verifiedBindings"]]
        changed_rows[0]["id"] = "different"
        self.assertNotEqual(
            receipt["bindingsFingerprint"], bindings.binding_fingerprint(changed_rows)
        )

    def test_changed_source_fails_closed(self) -> None:
        temporary, root, expected, expected_ids = self._fixture()
        self.addCleanup(temporary.cleanup)
        (root / "sources/alpha.txt").write_text("changed\n", encoding="utf-8")
        receipt = bindings.verify(
            root, expected_bindings=expected, expected_ids=expected_ids
        )
        self.assertEqual("failed", receipt["status"])
        self.assertIn("source digest mismatch for sources/alpha.txt", receipt["errors"])

    def test_manifest_extra_field_or_wrong_id_fails_closed(self) -> None:
        temporary, root, expected, expected_ids = self._fixture()
        self.addCleanup(temporary.cleanup)
        manifest_path = root / bindings.DEFAULT_MANIFEST
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["unsealed"] = True
        manifest["bindings"][0]["id"] = "wrong_id"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        receipt = bindings.verify(
            root, expected_bindings=expected, expected_ids=expected_ids
        )
        self.assertEqual("failed", receipt["status"])
        self.assertIn("manifest root fields are not exact", receipt["errors"])
        self.assertTrue(
            any(error.startswith("manifest id mismatch") for error in receipt["errors"])
        )

    def test_duplicate_key_and_repository_escape_fail_closed(self) -> None:
        temporary, root, expected, expected_ids = self._fixture()
        self.addCleanup(temporary.cleanup)
        manifest_path = root / bindings.DEFAULT_MANIFEST
        manifest_path.write_text(
            '{"contract":"Eva2DesignSourceBindings/v1",'
            '"contract":"Eva2DesignSourceBindings/v1",'
            '"pathPolicy":"repository_relative_only",'
            '"secretsAllowed":false,"legacyInputsAllowed":false,'
            '"bindingCount":1,"bindings":[]}',
            encoding="utf-8",
        )
        duplicate = bindings.verify(
            root, expected_bindings=expected, expected_ids=expected_ids
        )
        self.assertEqual("failed", duplicate["status"])
        self.assertTrue(
            any("duplicate JSON key" in error for error in duplicate["errors"])
        )

        rows = [
            {
                "id": "escape",
                "path": "../escape.txt",
                "sha256": "0" * 64,
            }
        ]
        manifest_path.write_text(
            json.dumps(
                {
                    "contract": bindings.CONTRACT,
                    "pathPolicy": "repository_relative_only",
                    "secretsAllowed": False,
                    "legacyInputsAllowed": False,
                    "bindingCount": 1,
                    "bindings": rows,
                }
            ),
            encoding="utf-8",
        )
        escaped = bindings.verify(
            root, expected_bindings=expected, expected_ids=expected_ids
        )
        self.assertEqual("failed", escaped["status"])
        self.assertTrue(
            any("not repository-relative" in error for error in escaped["errors"])
        )

    def test_symlinked_source_fails_closed(self) -> None:
        temporary, root, expected, expected_ids = self._fixture()
        self.addCleanup(temporary.cleanup)
        source = root / "sources/alpha.txt"
        target = root / "actual-alpha.txt"
        source.rename(target)
        try:
            source.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable")
        receipt = bindings.verify(
            root, expected_bindings=expected, expected_ids=expected_ids
        )
        self.assertEqual("failed", receipt["status"])
        self.assertTrue(any("symlink" in error for error in receipt["errors"]))


if __name__ == "__main__":
    unittest.main()
