"""Publish the single installed maintenance proof from exact completed evidence."""
from pathlib import Path
import argparse
import hashlib
import json
import os

RECIPE = Path(__file__).resolve().parent
RECEIPT_SHA256 = "4ac1497629cc23c4617bf0534ac68796bf5093a2b03ab6167d5d1fd03b438bea"
REVIEW_SHA256 = "9de4fe565b05702708d26a6b6b4a1be450c619b58766b34963eb70120fa278b6"
TARGET = Path("development/evidence/reassessment/maintenance-updates-20261106/photo-library-installed.json")
PROOF = json.loads(r'''
{
  "contract": "EvaPhotoLibraryDependencyDelivery/v1",
  "date": "2026-11-07",
  "status": "installed_and_effective_dependencies_verified",
  "versions": {
    "node": "22.23.4",
    "dotenv": "18.0.9",
    "betterSqlite": "13.0.3",
    "sqlite": "3.53.4",
    "nodeAddonApi": "8.9.2",
    "grammy": "1.46.0"
  },
  "source": {
    "passiveLibraryFiles": 11,
    "librarySourceChanges": 0,
    "sourceManifestSha256": "185f6706ae932d3fd525e8ba5f14568f9c742372c3d652c74f7436363ec76904",
    "lockedVendorPackages": 3
  },
  "selection": {
    "environmentKey": "EVA_TELEGRAM_PHOTO_PROVIDER_MODULE",
    "configurationFilesAdded": 1,
    "actualNativeMappingVerified": true,
    "existingDatabaseDescriptorVerified": true,
    "eagerDotenvResolutionVerified": true,
    "startupPendingObservedThenVerifiedSameGeneration": true
  },
  "preservation": {
    "protectedTelegramTransportRows": 115,
    "storedFaceRows": 29,
    "activeFaceProfiles": 24,
    "faceRowsAndRevisionPreserved": true,
    "credentialBindingsPreserved": true,
    "originalLibraryRetainedForReturn": true,
    "databaseRestorePerformed": false
  },
  "verification": {
    "isolatedUid1000OldNewOldPassed": true,
    "actualDotenvParserEquivalent": true,
    "independentFocusedTestsPassed": 14,
    "receiptBindingRefusals": 6
  },
  "scope": {
    "actualOwnerPhotoUseVerified": false,
    "testPhotoModelRequests": 0,
    "testGetMeRequests": 0,
    "runtimeUsage": null,
    "credits": null,
    "newProductFunctionCompleted": false,
    "fullE416Completed": false
  },
  "evidence": {
    "initialPendingReceiptSha256": "7d32211088662226b44975f4ed8c722916b9fc27a95a70b67dfdbf242be93198",
    "completionReceiptSha256": "4ac1497629cc23c4617bf0534ac68796bf5093a2b03ab6167d5d1fd03b438bea",
    "installedIndependentReview": "development/reviews/photo-library-installed-independent-20261107.json",
    "installedIndependentReviewSha256": "9de4fe565b05702708d26a6b6b4a1be450c619b58766b34963eb70120fa278b6"
  }
}
''')


def checked_json(path, expected):
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == expected
    return json.loads(raw)


def publish(repository, receipt_path, review_path):
    manifest = json.loads((RECIPE / "artifact-manifest.json").read_text())
    target_recipe = repository / "deploy/living-core/telegram-photo-library-20261107"
    for name, digest in manifest["files"].items():
        checked = target_recipe / name
        assert checked.is_file() and not checked.is_symlink()
        assert hashlib.sha256(checked.read_bytes()).hexdigest() == digest
    assert (target_recipe / "artifact-manifest.json").read_bytes() == (RECIPE / "artifact-manifest.json").read_bytes()
    receipt = checked_json(receipt_path, RECEIPT_SHA256)
    review = checked_json(review_path, REVIEW_SHA256)
    assert receipt["state"] == "photo_library_selected_and_effective_native_verified"
    assert review["approved"] is True and review["activationReceiptSha256"] == RECEIPT_SHA256
    assert review["decision"] == "GO_INSTALLED_TECHNICAL_SCOPE"
    resolution = receipt["readback"]["dependencyResolution"]
    for key, expected in {"node": "22.23.4", "betterSqlite": "13.0.3",
                          "sqlite": "3.53.4", "dotenv": "18.0.9"}.items():
        assert resolution[key] == expected
    assert receipt["readback"]["sourceFiles"] == 11
    assert receipt["readback"]["dataAndCredentialBindingsPreserved"] is True
    assert receipt["readback"]["grammyManifestUnchanged"] is True
    assert receipt["readback"]["face"]["profileRows"] == 29
    source_manifest = target_recipe / "source-files.json"
    assert hashlib.sha256(source_manifest.read_bytes()).hexdigest() == PROOF["source"]["sourceManifestSha256"]
    output = repository / TARGET
    output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "w") as handle:
        json.dump(PROOF, handle, indent=2)
        handle.write(chr(10))
        handle.flush()
        os.fsync(handle.fileno())
    fd = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--installed-review", type=Path, required=True)
    options = parser.parse_args()
    print(publish(options.repository_root, options.receipt, options.installed_review))


if __name__ == "__main__":
    main()
