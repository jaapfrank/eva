"""Apply a host-registered, independently accepted release once, after work is idle.

This is an application handoff, not candidate generation, approval, or a builder.
The trusted host supplies readiness and the existing image-only updater callbacks.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import re
import stat
import threading
import uuid

from eva.operations.component_release_import import canonical, checked_evidence, digest, load_object
from eva.operations.image_update import image_update_services


_ID = re.compile(r"[0-9a-f]{64}\Z")
_WAITING = {"queued", "waiting", "interrupted_before_dispatch"}
_TERMINAL = {"cancelled", "rejected", "completed", "unconfirmed"}
_ARTIFACTS = {"package", "review", "source_import", "build", "installed", "candidate"}


def _identifier(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("application identifier must be a SHA-256 hex value")
    return value


def _path(value, *, exists=True):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or path.resolve() != path:
        raise ValueError("application paths must be absolute without symbolic links")
    if exists:
        info = path.stat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) & 0o022):
            raise ValueError("application inputs must be host-owned regular files")
    return path


def _artifact(binding):
    if not isinstance(binding, dict) or set(binding) != {"path", "sha256"}:
        raise ValueError("application artifact binding differs")
    _identifier(binding["sha256"])
    path = _path(binding["path"])
    if digest(path.read_bytes()) != binding["sha256"]:
        raise ValueError("application artifact bytes changed")
    return path, load_object(path)


def _verify_release(review_id, release):
    """Reuse importer attestations and the established overlay/updater formats."""
    if isinstance(release, dict) and release.get("kind") == "saved_artifact":
        from eva.operations.saved_artifact_application import verify_saved_release
        return verify_saved_release(review_id, release)
    if (not isinstance(release, dict)
            or set(release) != _ARTIFACTS | {"project", "update_receipt"}
            or not isinstance(release["project"], str) or not release["project"]):
        raise ValueError("reviewed release configuration differs")
    paths, data = {}, {}
    for name in _ARTIFACTS:
        paths[name], data[name] = _artifact(release[name])
    package, review, imported, build = (data[name] for name in
                                       ("package", "review", "source_import", "build"))
    package_id = package.get("candidate_package_sha256")
    if (package.get("contract") != "EvaComponentReleaseCandidate/v1"
            or package.get("state") != "awaiting_measured_gain_review"
            or package.get("installed") is not False
            or package_id != digest(canonical({k: v for k, v in package.items()
                                                if k != "candidate_package_sha256"}))):
        raise ValueError("approved package binding differs")
    source = package.get("candidate_content")
    if (not isinstance(source, str)
            or digest(source.encode("utf-8")) != package.get("candidate_sha256")
            or digest(canonical(package.get("evaluation"))) != package.get("evaluation_sha256")):
        raise ValueError("approved source binding differs")
    if (review.get("contract") != "EvaComponentReleaseReview/v1"
            or review.get("decision") != "accept"
            or review.get("candidate_package_sha256") != package_id
            or not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip()):
        raise ValueError("independent review does not accept this package")
    evidence = {name: checked_evidence(paths["review"], review.get(name)) for name in
                ("measured_gain_evidence", "independent_review_evidence")}
    if (imported.get("contract") != "EvaComponentSourceImport/v1"
            or imported.get("state") != "source_imported_not_installed"
            or imported.get("installed") is not False
            or imported.get("candidate_package_sha256") != package_id
            or imported.get("evaluation_sha256") != package["evaluation_sha256"]
            or imported.get("baseline_sha256") != package.get("baseline_sha256")
            or imported.get("candidate_sha256") != package["candidate_sha256"]
            or imported.get("path") != "src/eva/" + package["path"]
            or imported.get("review_sha256") != digest(canonical(review))
            or imported.get("reviewer") != review["reviewer"]
            or imported.get("evidence") != evidence):
        raise ValueError("source import is not bound to the accepted review")
    changed = image_update_services(data["installed"], data["candidate"])
    if set(changed) != {"core", "delegated-work-worker"}:
        raise ValueError("reviewed application requires both core and worker images")
    before = {name: data["installed"]["services"][name]["image"] for name in changed}
    after = {name: data["candidate"]["services"][name]["image"] for name in changed}
    for image in (*before.values(), *after.values()):
        if not isinstance(image, str) or not image.startswith("sha256:"):
            raise ValueError("application images must be immutable")
        _identifier(image[7:])
    if (build.get("installed") is not False or build.get("before") != before
            or build.get("images") != after
            or build.get("sourceHashes", {}).get(package["path"]) != package["candidate_sha256"]):
        raise ValueError("build images are not bound to the reviewed source")
    receipt = _path(release["update_receipt"], exists=False)
    # A receipt is host output. It cannot replace any accepted artifact.
    if receipt in paths.values():
        raise ValueError("updater receipt collides with an input artifact")
    parent = receipt.parent
    info = parent.stat()
    if not parent.is_dir() or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022:
        raise ValueError("updater receipt directory must be host-owned")
    return {"review_id": review_id, "project": release["project"],
            "installed": paths["installed"], "candidate": paths["candidate"],
            "receipt": receipt, "candidate_images": after,
            "accepted_review_sha256": digest(canonical(review))}


def _save(path, value, *, initial=False):
    temporary = path if initial else path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        if not initial:
            os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if not initial and temporary.exists():
            temporary.unlink()


class ReviewedComponentApplications:
    """One host dispatcher; request IDs and originating task are adapter-bound."""

    def __init__(self, config, *, ready_to_apply, apply_release):
        if (not isinstance(config, dict)
                or set(config) - {"receipt_root", "releases", "poll_seconds"}
                or not {"receipt_root", "releases"} <= config.keys()
                or not isinstance(config["releases"], dict)):
            raise ValueError("reviewed application configuration differs")
        self.releases = json.loads(json.dumps(config["releases"]))
        for review_id, release in self.releases.items():
            _verify_release(_identifier(review_id), release)
        self.interval = config.get("poll_seconds", 0.5)
        if type(self.interval) not in (int, float) or not 0.01 <= self.interval <= 60:
            raise ValueError("application polling interval differs")
        self.root = _path(config["receipt_root"], exists=False)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.root.stat()
        if not self.root.is_dir() or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError("application receipts require a private host-owned directory")
        self.registry = self.root / "accepted-releases"
        self.registry.mkdir(mode=0o700, exist_ok=True)
        registry_info = self.registry.stat()
        if self.registry.is_symlink() or registry_info.st_uid != os.geteuid() or registry_info.st_mode & 0o077:
            raise ValueError("accepted release registry must be private")
        for stored in self.registry.glob("*.json"):
            release = load_object(_path(stored))
            review_id = _identifier(stored.stem)
            _verify_release(review_id, release)
            if review_id in self.releases and self.releases[review_id] != release:
                raise ValueError("registered accepted release changed")
            self.releases[review_id] = release
        self._lock_file = _path(self.root / ".dispatcher.lock", exists=False).open("a+b")
        try:
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception:
            self._lock_file.close()
            raise ValueError("another application dispatcher owns these receipts") from None
        self.ready_to_apply, self.apply_release = ready_to_apply, apply_release
        self.condition = threading.Condition()
        self.closed = False
        self.jobs = {}
        try:
            for path in self.root.glob("*.json"):
                job = load_object(_path(path))
                self._validate_job(job, path)
                if job["state"] in _WAITING:
                    job["state"] = "interrupted_before_dispatch"
                    _save(path, job)
                self.jobs[job["request_id"]] = job
        except Exception:
            self._lock_file.close()
            raise
        self.thread = threading.Thread(target=self._run, name="reviewed-component-application", daemon=True)
        self.thread.start()

    def _validate_job(self, job, path):
        if (job.get("contract") != "EvaReviewedComponentApplication/v1"
                or path.name != _identifier(job.get("request_id")) + ".json"
                or job.get("state") not in _WAITING | _TERMINAL | {"dispatching"}):
            raise ValueError("application receipt differs")
        self._task(job.get("task_id"))
        _identifier(job.get("review_id"))
        _identifier(job.get("release_sha256"))
        _identifier(job.get("accepted_review_sha256"))

    @staticmethod
    def _task(task_id):
        if (not isinstance(task_id, str) or not 1 <= len(task_id) <= 256
                or any(ord(c) < 33 or ord(c) > 126 for c in task_id)):
            raise ValueError("application requires an exact originating task ID")

    def _persist(self, job):
        _save(self.root / (job["request_id"] + ".json"), job)

    def _owned(self, request_id, task_id):
        _identifier(request_id)
        self._task(task_id)
        job = self.jobs.get(request_id)
        if job is None or job["task_id"] != task_id:
            raise ValueError("application request is unavailable in this task")
        return job

    def _result(self, job):
        return {key: job[key] for key in ("request_id", "review_id", "task_id", "state")} | {
            "installed": job["state"] == "completed",
            **({"saved_artifact": job["saved_artifact"]} if "saved_artifact" in job else {}),
            **({"reason": job["reason"]} if "reason" in job else {})}

    def register(self, review_id, release):
        """Trusted independent final publisher only; not an HTTP/model operation."""
        _verify_release(_identifier(review_id), release)
        with self.condition:
            if self.closed:
                raise ValueError("application dispatcher is closed")
            previous = self.releases.get(review_id)
            if previous is not None and previous != release:
                raise ValueError("accepted release registration changed")
            if previous is None:
                _save(self.registry / (review_id + ".json"), release, initial=True)
                self.releases[review_id] = json.loads(json.dumps(release))

    def request(self, review_id, request_id, task_id):
        _identifier(review_id)
        _identifier(request_id)
        self._task(task_id)
        with self.condition:
            if self.closed:
                raise RuntimeError("application dispatcher is closed")
            release = self.releases.get(review_id)
            if release is None:
                raise ValueError("review is not registered by the host")
            binding = digest(canonical(release))
            existing = self.jobs.get(request_id)
            if existing is not None:
                self._owned(request_id, task_id)
                if existing["review_id"] != review_id or existing["release_sha256"] != binding:
                    raise ValueError("application request changed during replay")
                if existing["state"] == "interrupted_before_dispatch":
                    _verify_release(review_id, release)
                    existing["state"] = "queued"
                    self._persist(existing)
                    self.condition.notify_all()
                return self._result(existing)
            verified = _verify_release(review_id, release)
            if any(job["accepted_review_sha256"] == verified["accepted_review_sha256"]
                   for job in self.jobs.values()):
                raise ValueError("accepted review already has an application request")
            job = {"contract": "EvaReviewedComponentApplication/v1",
                   "request_id": request_id, "review_id": review_id, "task_id": task_id,
                   "release_sha256": binding,
                   "accepted_review_sha256": verified["accepted_review_sha256"], "state": "queued"}
            _save(self.root / (request_id + ".json"), job, initial=True)
            self.jobs[request_id] = job
            self.condition.notify_all()
            return self._result(job)

    def review_status(self, review_id):
        """Read an authorized registered review across originating task boundaries."""
        _identifier(review_id)
        with self.condition:
            release = self.releases.get(review_id)
            if release is None:
                raise ValueError("review is not registered by the host")
            _, review = _artifact(release["review"])
            accepted_review_sha256 = digest(canonical(review))
            matches = [job for job in self.jobs.values()
                       if job["accepted_review_sha256"] == accepted_review_sha256]
            if not matches:
                return {"review_id": review_id, "state": "ready", "installed": False}
            if len(matches) != 1:
                raise ValueError("accepted review has ambiguous application receipts")
            job = matches[0]
            if job["state"] in {"dispatching", "unconfirmed"}:
                self._reconcile(job)
            return self._result(job)

    def status(self, request_id, *, task_id):
        with self.condition:
            job = self._owned(request_id, task_id)
            if job["state"] in {"dispatching", "unconfirmed"}:
                self._reconcile(job)
            return self._result(job)

    def cancel(self, request_id, *, task_id):
        with self.condition:
            job = self._owned(request_id, task_id)
            if job["state"] in _WAITING:
                job["state"] = "cancelled"
                self._persist(job)
                self.condition.notify_all()
            return self._result(job)

    def _reconcile(self, job):
        release = self.releases.get(job["review_id"])
        if release is None or digest(canonical(release)) != job["release_sha256"]:
            return
        path = _path(release["update_receipt"], exists=False)
        if not path.exists():
            return
        try:
            receipt = load_object(_path(path))
            if release.get("kind") == "saved_artifact":
                from eva.operations.saved_artifact_application import receipt_confirms_application
                verified = _verify_release(job["review_id"], release)
                if not receipt_confirms_application(receipt, verified["publication"], job["task_id"]):
                    return
                job["state"] = "completed"
                job["saved_artifact"] = {"reference": receipt["reference"],
                    "publication": receipt["publication"], "old_source_unchanged": True}
                job.pop("reason", None)
                self._persist(job)
                return
            candidate = _artifact(release["candidate"])[1]
            images = {name: candidate["services"][name]["image"]
                      for name in ("core", "delegated-work-worker")}
            services = receipt.get("services", {})
            success = (receipt.get("contract") == "EvaImageUpdate/v1"
                       and receipt.get("project") == release["project"]
                       and receipt.get("status") == "running_with_retained_mounts"
                       and set(services) == set(images)
                       and all(services[name].get("candidate_image") == image
                               and services[name].get("current_container")
                               for name, image in images.items()))
        except (ValueError, OSError, KeyError, TypeError):
            success = False
        if success:
            job["state"] = "completed"
            job.pop("reason", None)
            self._persist(job)

    def _run(self):
        try:
            self._dispatch_loop()
        finally:
            if self.closed:
                self._lock_file.close()

    def _dispatch_loop(self):
        while True:
            with self.condition:
                if self.closed:
                    return
                job = next((j for j in self.jobs.values() if j["state"] in {"queued", "waiting"}), None)
                if job is None:
                    self.condition.wait()
                    continue
            try:
                readiness = self.ready_to_apply(job["task_id"])
                if readiness not in {"waiting", "ready", "cancelled"}:
                    raise ValueError("readiness callback must return a known state")
                error = None
            except Exception:
                readiness, error = "waiting", "readiness_check_failed"
            with self.condition:
                if self.closed:
                    return
                if job["state"] not in {"queued", "waiting"}:
                    continue
                if error:
                    job.update(state="rejected", reason=error)
                    self._persist(job)
                    continue
                if readiness == "cancelled":
                    job["state"] = "cancelled"
                    self._persist(job)
                    continue
                if readiness == "waiting":
                    if job["state"] != "waiting":
                        job["state"] = "waiting"
                        self._persist(job)
                    self.condition.wait(self.interval)
                    continue
                release = self.releases[job["review_id"]]
                try:
                    if digest(canonical(release)) != job["release_sha256"]:
                        raise ValueError("registered release changed")
                    update = _verify_release(job["review_id"], release)
                    if update.get("kind") == "saved_artifact":
                        update["task_id"] = job["task_id"]
                    if update["receipt"].exists():
                        raise ValueError("an updater receipt already exists")
                except Exception:
                    job.update(state="rejected", reason="release_binding_changed")
                    self._persist(job)
                    continue
                job["state"] = "dispatching"
                self._persist(job)  # durable before the one possible updater call
            try:
                self.apply_release(update)
            except Exception:
                pass  # Only the updater's durable, exact receipt can confirm completion.
            with self.condition:
                job.update(state="unconfirmed", reason="inspect_existing_update_receipt")
                self._persist(job)
                self._reconcile(job)

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()
        self.thread.join(timeout=1)
        # Never allow a second dispatcher while an existing update is in flight.
        if not self.thread.is_alive():
            self._lock_file.close()
