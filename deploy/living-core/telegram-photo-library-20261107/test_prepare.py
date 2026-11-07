import json
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("photo_recipe", ROOT / "prepare.py")
recipe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recipe)


class RecipeTests(unittest.TestCase):
    def fixture(self, root):
        manifest = {"sourceFileCount": 11, "files": {}}
        for index in range(11):
            name = f"src/module_{index}.js"
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"export const value = {index};")
            manifest["files"][name] = recipe.sha(path)
        return manifest

    def test_exact_whitelist_excludes_other_source_and_config_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "snapshot"
            source.mkdir()
            manifest = self.fixture(source)
            (source / ".env").write_text("private fixture")
            (source / "agent.js").write_text("unselected entrypoint")
            selected = recipe.source_files(source, manifest)
            output = root / "artifact"
            output.mkdir()
            recipe.copy_sources(selected, output)
            self.assertEqual({str(p.relative_to(output)) for p in output.rglob("*")
                              if p.is_file()}, set(manifest["files"]))
            self.assertTrue(all(recipe.sha(output / name) == digest
                                for name, digest in manifest["files"].items()))

    def test_changed_source_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.fixture(root)
            (root / "src/module_1.js").write_text("different")
            with self.assertRaises(AssertionError):
                recipe.source_files(root, manifest)

    def test_source_symlink_alias_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.fixture(root)
            original = root / "src/module_1.js"
            target = root / "other.js"
            target.write_bytes(original.read_bytes())
            original.unlink()
            original.symlink_to(target)
            with self.assertRaises(AssertionError):
                recipe.source_files(root, manifest)

    def test_source_path_escape_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.fixture(root)
            digest = manifest["files"].pop("src/module_1.js")
            manifest["files"]["../escape.js"] = digest
            with self.assertRaises(AssertionError):
                recipe.source_files(root, manifest)

    def test_vendor_mutation_and_unknown_file_are_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vendor = root / "node_modules"
            vendor.mkdir()
            file = vendor / "known.js"
            file.write_text("original")
            expected = recipe.vendor_files(root)
            file.write_text("changed")
            self.assertNotEqual(recipe.vendor_files(root), expected)
            file.write_text("original")
            (vendor / "extra.js").write_text("extra")
            self.assertNotEqual(recipe.vendor_files(root), expected)


    def test_npm_cache_allows_only_the_exact_recorded_ci_variant(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "artifact"
            (artifact / "node_modules").mkdir(parents=True)
            recorded = {"packages": {"node_modules/better-sqlite3": {"version": "13.0.3"}}}
            (root / "npm-installed-lock.json").write_text(json.dumps(recorded))
            generated = {"packages": {"node_modules/better-sqlite3":
                                     {"version": "13.0.3", "hasInstallScript": True}}}
            cache = artifact / "node_modules/.package-lock.json"
            cache.write_text(json.dumps(generated))
            with patch.object(recipe, "RECIPE", root):
                recipe.restore_recorded_npm_cache(artifact)
            self.assertEqual(cache.read_bytes(), (root / "npm-installed-lock.json").read_bytes())
            generated["packages"]["node_modules/better-sqlite3"]["unexpected"] = "value"
            cache.write_text(json.dumps(generated))
            with patch.object(recipe, "RECIPE", root):
                with self.assertRaises(AssertionError):
                    recipe.restore_recorded_npm_cache(artifact)

if __name__ == "__main__":
    unittest.main()
