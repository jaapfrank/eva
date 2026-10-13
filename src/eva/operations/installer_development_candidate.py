"""Local owner-authorized installation inputs, never public release authority.

The installer can validate a development target before the complete release
gate. This receipt deliberately cannot authorize an A/B release slot. Its
authority is the locally selected package and actual immutable image inventory,
not a fabricated production signer or an earlier platform-support claim.
"""
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re

from eva.contracts.canonical_json import canonical_sha256


_SEAL = object()


@dataclass(frozen=True, slots=True)
class InstallerDevelopmentCandidate:
    manifest_sha256: str
    image_set_sha256: str
    verification_receipt_sha256: str
    package_root: Path = field(repr=False)
    source_files: tuple = field(repr=False)
    _seal: object = field(repr=False)

    def require_verified(self):
        if self._seal is not _SEAL:
            raise ValueError("local development candidate must be inspected")
        for relative, expected in self.source_files:
            path = self.package_root / relative
            if (path.resolve() != path or not path.is_file()
                    or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
                raise ValueError("inspected development package changed")


def verify_installer_development_candidate(package_root, *, inspect_image):
    """Verify actual local inputs; inspect_image must use the real Docker engine."""
    root = Path(package_root)
    if not root.is_absolute() or root.resolve() != root:
        raise ValueError("canonical local package required")
    staging = json.loads((root / "package-staging.json").read_bytes())
    manifest = staging.get("development_candidate")
    if (staging.get("contract") != "EvaInstallerPackageStaging/v1"
            or staging.get("status") != "staged_not_validated"
            or not isinstance(manifest, dict)
            or set(manifest) != {"contract", "imageDigests", "public_release", "sourceCommit"}
            or manifest.get("contract") != "EvaLocalInstallerCandidate/v1"
            or manifest.get("public_release") is not False
            or manifest.get("sourceCommit") != staging.get("base_commit")):
        raise ValueError("explicit local development installation candidate required")
    images = manifest["imageDigests"]
    if (not isinstance(images, dict) or not {"living-core", "postgres", "hindsight"} <= set(images)
            or set(images) - {"living-core", "postgres", "hindsight", "browser", "browser-mcp", "mcp-gateway", "voice-media", "livekit"}):
        raise ValueError("complete local installer image inventory required")
    for digest in images.values():
        if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError("immutable local installer images required")
        actual = inspect_image(digest)
        if (actual.get("Id") != digest or actual.get("Os") != "linux"
                or actual.get("Architecture") != "amd64"):
            raise ValueError("actual local installer image differs")
    files = staging.get("source_files")
    if not isinstance(files, dict) or not files:
        raise ValueError("actual packaged source inventory required")
    for relative, digest in files.items():
        selected = Path(relative)
        if (selected.is_absolute() or ".." in selected.parts
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise ValueError("local source inventory differs")
    receipt = InstallerDevelopmentCandidate(
        canonical_sha256(manifest), canonical_sha256(images),
        canonical_sha256({"authority": "owner_local_development", "manifest": manifest,
                          "source_inventory_sha256": hashlib.sha256(json.dumps(
                              files, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                          "platform_support_claim": False}),
        root, tuple(sorted(files.items())), _SEAL)
    receipt.require_verified()
    return manifest, receipt


def development_preflight(receipt, candidate):
    """Admit matching local development, retaining the blocked public support check."""
    candidate.require_verified()
    result = dict(receipt)
    checks = [dict(item) for item in receipt["checks"]]
    required = {"python", "host", "container_engine"}
    if {x["id"] for x in checks if x["state"] == "passed"} >= required:
        result.update(status="ready", next=(
            "Lokale ontwikkelinstallatie: pakket en computer passen. "
            "Publieke releasesupport is nog niet bewezen."))
        result["non_claims"] = [*receipt["non_claims"],
            "Deze eigenaarinstallatie is een lokale ontwikkelkandidaat, geen geautoriseerde publieke release."]
        checks.append({"id": "local_development_candidate", "state": "passed",
                       "detail": "Werkelijke lokale pakketbestanden en gepinde images gecontroleerd; geen publieke release."})
    result["checks"] = checks
    return result
