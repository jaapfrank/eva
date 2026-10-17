"""Prepare exact accepted artifacts for the existing component application queue."""
from __future__ import annotations
import json
from pathlib import Path
import re
import subprocess
from eva.operations.reviewed_component_application import _artifact, _path, _save, _verify_release
from eva.operations.component_release_import import prepare, canonical, digest
from eva.operations.image_update import image_update_services

class ComponentReleasePublisher:
    def __init__(self, config, *, run=subprocess.run):
        if (not isinstance(config, dict)
                or set(config) != {"repository", "integration_commit", "installed", "project", "reviewer"}
                or not re.fullmatch(r"[0-9a-f]{40}", config["integration_commit"])
                or not isinstance(config["reviewer"], str) or not config["reviewer"].strip()):
            raise ValueError("invalid trusted release publisher configuration")
        self.config, self.run = dict(config), run
        self.repository = Path(config["repository"])
        if not self.repository.is_absolute() or self.repository.resolve() != self.repository:
            raise ValueError("release repository must be an exact host path")
        _artifact(config["installed"])

    def _command(self, argv, directory):
        # No candidate commands run on the host. All argv come from this fixed code.
        result = self.run(argv, text=True, capture_output=True)
        if result.returncode != 0:
            # Keep diagnostics private; no configuration or provider secrets in responses.
            log = directory / "publication-error.private.log"
            log.write_text(result.stderr[:65536])
            log.chmod(0o600)
            raise RuntimeError("trusted release publication failed")
        return result.stdout.strip()

    def __call__(self, package, assessment, directory):
        if (assessment["decision"] != "accept"
                or assessment["public"].get("semantic_improvement_verified") is not True):
            raise ValueError("independent measured approval is required")
        # The independent registration and result are host-owned hash-bound inputs.
        _artifact(assessment["evidence"])
        _artifact(assessment["registration"])
        review_id = package["candidate_package_sha256"]
        review = {"contract": "EvaComponentReleaseReview/v1", "decision": "accept",
                  "candidate_package_sha256": review_id, "reviewer": self.config["reviewer"],
                  "measured_gain_evidence": assessment["evidence"],
                  "independent_review_evidence": assessment["registration"]}
        _save(directory / "review.json", review, initial=True)
        if package.get("source_target", {}).get("kind") == "saved_artifact":
            from eva.operations.saved_artifact_application import publication_plan
            _save(directory / "publication.json", publication_plan(package), initial=True)
            files = {"package": "package.json", "review": "review.json", "publication": "publication.json"}
            release = {key: {"path": str(directory / filename),
                            "sha256": digest((directory / filename).read_bytes())}
                       for key, filename in files.items()}
            release.update(kind="saved_artifact", update_receipt=str(directory / "saved-application.private.json"))
            _verify_release(review_id, release)
            return release
        checkout = directory / "source"
        branch = "codex/eva-improvement-" + review_id[:20]
        self._command(["git", "-c", "safe.directory=" + str(self.repository), "-C",
            str(self.repository), "worktree", "add", "-b", branch, str(checkout),
            self.config["integration_commit"]], directory)
        target, before, raw, imported = prepare(directory / "package.json", directory / "review.json", checkout)
        # Importer validates exact diff, independent evidence, AST boundary and baseline.
        if target.read_bytes() != before:
            raise ValueError("source changed before the exact import")
        target.write_bytes(raw)
        imported["state"] = "source_imported_not_installed"
        _save(directory / "source-import.json", imported, initial=True)
        relative = target.relative_to(checkout).as_posix()
        self._command(["git", "-C", str(checkout), "add", "--", relative], directory)
        self._command(["git", "-C", str(checkout), "-c", "user.name=Eva reviewed improvement",
            "-c", "user.email=eva@localhost", "commit", "-m",
            "Apply independently measured component improvement", "--", relative], directory)
        commit = self._command(["git", "-C", str(checkout), "rev-parse", "HEAD"], directory)
        _, installed = _artifact(self.config["installed"])
        before_images = {name: installed["services"][name]["image"]
                         for name in ("core", "delegated-work-worker")}
        images = {}
        for service, image in before_images.items():
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
                raise ValueError("publisher baseline image is not pinned")
            context = directory / ("build-" + service)
            context.mkdir(mode=0o700)
            (context / "source.py").write_bytes(raw)
            (context / "source.py").chmod(0o444)
            destination = "/opt/eva/src/eva/" + package["path"]
            (context / "Dockerfile").write_text(
                "FROM " + image + "\nCOPY source.py " + destination + "\n")
            iid = directory / (service + "-image.txt")
            self._command(["docker", "build", "--pull=false", "--iidfile", str(iid), str(context)], directory)
            candidate_id = iid.read_text().strip()
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", candidate_id):
                raise ValueError("published image ID differs")
            # Verify packaged candidate bytes before registering installation.
            actual = self._command(["docker", "run", "--rm", "--network", "none", "--read-only",
                "--entrypoint", "python", candidate_id, "-I", "-c",
                "import hashlib,pathlib,sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())",
                destination], directory)
            if actual != package["candidate_sha256"]:
                raise ValueError("built image does not contain the accepted source")
            images[service] = candidate_id
        candidate = json.loads(json.dumps(installed))
        for service, image in images.items():
            candidate["services"][service]["image"] = image
        if set(image_update_services(installed, candidate)) != set(images):
            raise ValueError("publication changes more than accepted core and worker images")
        _save(directory / "installed-compose.private.json", installed, initial=True)
        _save(directory / "candidate-compose.private.json", candidate, initial=True)
        build = {"installed": False, "before": before_images, "images": images,
                 "sourceHashes": {package["path"]: package["candidate_sha256"]},
                 "source_commit": commit, "source_branch": branch}
        _save(directory / "build.json", build, initial=True)
        files = {"package": "package.json", "review": "review.json", "source_import": "source-import.json",
                 "build": "build.json", "installed": "installed-compose.private.json",
                 "candidate": "candidate-compose.private.json"}
        release = {key: {"path": str(directory / filename),
                        "sha256": digest((directory / filename).read_bytes())}
                   for key, filename in files.items()}
        release.update(project=self.config["project"], update_receipt=str(directory / "update.private.json"))
        _verify_release(review_id, release)
        return release
