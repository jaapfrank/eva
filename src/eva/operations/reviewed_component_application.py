"""Apply a host-registered, independently accepted release once, after work is idle.

Pure release, artifact, reviewer-authority, and updater-receipt verification live
in ``eva.operations.reviewed_release_validation``. This module owns only the
host-locked persistent receipt directory and single background dispatcher.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import stat
import threading
import uuid

from eva.operations.component_release_import import canonical, checked_evidence, digest, load_object
from eva.operations.reviewed_release_validation import (
    ARTIFACTS as _ARTIFACTS,
    behavior_proof_confirms as _behavior_confirms,
    evaluate_updater_receipt,
    load_verified_artifact as _artifact,
    validate_application_job_payload,
    validate_host_path as _path,
    validate_identifier as _identifier,
    validate_task_identifier,
    verify_behavior_source as _verified_behavior_source,
    verify_reviewed_release as _verify_release,
)

_WAITING = {"queued", "waiting", "interrupted_before_dispatch"}
_TERMINAL = {"cancelled", "rejected", "completed", "unconfirmed", "recovered"}


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

    def __init__(self, config, *, ready_to_apply, apply_release, inspect_release=None):
        if (
            not isinstance(config, dict)
            or set(config) - {"receipt_root", "releases", "poll_seconds", "review_authorities"}
            or not {"receipt_root", "releases"} <= config.keys()
            or not isinstance(config["releases"], dict)
        ):
            raise ValueError("reviewed application configuration differs")
        authorities = config.get("review_authorities", {})
        if not isinstance(authorities, dict):
            raise ValueError("review authorities must be host configuration")
        self.review_authorities = json.loads(json.dumps(authorities))
        self.releases = json.loads(json.dumps(config["releases"]))
        for review_id, release in self.releases.items():
            _verify_release(
                _identifier(review_id),
                release,
                review_authorities=self.review_authorities,
                allow_legacy=True,
            )
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
        if (
            self.registry.is_symlink()
            or registry_info.st_uid != os.geteuid()
            or registry_info.st_mode & 0o077
        ):
            raise ValueError("accepted release registry must be private")
        for stored in self.registry.glob("*.json"):
            release = load_object(_path(stored))
            review_id = _identifier(stored.stem)
            _verify_release(
                review_id,
                release,
                review_authorities=self.review_authorities,
                allow_legacy=True,
            )
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
        self.inspect_release = inspect_release
        self.condition = threading.Condition()
        self.closed = False
        self.jobs = {}
        allowed_states = _WAITING | _TERMINAL | {"dispatching"}
        try:
            for path in self.root.glob("*.json"):
                job = load_object(_path(path))
                validate_application_job_payload(job, path.name, allowed_states=allowed_states)
                if job["state"] in _WAITING:
                    job["state"] = "interrupted_before_dispatch"
                    _save(path, job)
                self.jobs[job["request_id"]] = job
        except Exception:
            self._lock_file.close()
            raise
        self.thread = threading.Thread(
            target=self._run, name="reviewed-component-application", daemon=True
        )
        self.thread.start()

    @staticmethod
    def _task(task_id):
        validate_task_identifier(task_id)

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
            **({"reason": job["reason"]} if "reason" in job else {}),
        }

    def register(self, review_id, release):
        """Trusted independent final publisher only; not an HTTP/model operation."""
        _verify_release(_identifier(review_id), release, review_authorities=self.review_authorities)
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
                historical = _verify_release(
                    review_id,
                    release,
                    review_authorities=self.review_authorities,
                    allow_legacy=True,
                )
                if historical["accepted_review_sha256"] != existing["accepted_review_sha256"]:
                    raise ValueError("application approval changed during replay")
                if existing["state"] == "interrupted_before_dispatch":
                    _verify_release(review_id, release, review_authorities=self.review_authorities)
                    existing["state"] = "queued"
                    self._persist(existing)
                    self.condition.notify_all()
                return self._result(existing)
            verified = _verify_release(review_id, release, review_authorities=self.review_authorities)
            if any(
                job["accepted_review_sha256"] == verified["accepted_review_sha256"]
                for job in self.jobs.values()
            ):
                raise ValueError("accepted review already has an application request")
            job = {
                "contract": "EvaReviewedComponentApplication/v1",
                "request_id": request_id,
                "review_id": review_id,
                "task_id": task_id,
                "release_sha256": binding,
                "accepted_review_sha256": verified["accepted_review_sha256"],
                "state": "queued",
            }
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
            _verify_release(
                review_id,
                release,
                review_authorities=self.review_authorities,
                allow_legacy=True,
            )
            _, review = _artifact(release["review"])
            accepted_review_sha256 = digest(canonical(review))
            matches = [
                job
                for job in self.jobs.values()
                if job["accepted_review_sha256"] == accepted_review_sha256
            ]
            if not matches:
                if review.get("contract") == "EvaComponentReleaseReview/v1":
                    return {
                        "review_id": review_id,
                        "state": "rejected",
                        "installed": False,
                        "reason": "legacy_review_read_only",
                    }
                return {"review_id": review_id, "state": "ready", "installed": False}
            if len(matches) != 1:
                raise ValueError("accepted review has ambiguous application receipts")
            job = matches[0]
            if job["state"] in {"dispatching", "unconfirmed"} or (
                job["state"] == "completed" and "behavior_verifier" in release
            ):
                self._reconcile(job, inspect=True)
            return self._result(job)

    def status(self, request_id, *, task_id):
        with self.condition:
            job = self._owned(request_id, task_id)
            if job["state"] == "completed":
                release = self.releases.get(job["review_id"])
                if release is None or digest(canonical(release)) != job["release_sha256"]:
                    raise ValueError("application release changed during inspection")
                verified = _verify_release(
                    job["review_id"],
                    release,
                    review_authorities=self.review_authorities,
                    allow_legacy=True,
                )
                if verified["accepted_review_sha256"] != job["accepted_review_sha256"]:
                    raise ValueError("application approval changed during inspection")
            if job["state"] in {"dispatching", "unconfirmed"} or (
                job["state"] == "completed"
                and "behavior_verifier" in self.releases.get(job["review_id"], {})
            ):
                self._reconcile(job, inspect=True)
            return self._result(job)

    def cancel(self, request_id, *, task_id):
        with self.condition:
            job = self._owned(request_id, task_id)
            if job["state"] in _WAITING:
                job["state"] = "cancelled"
                self._persist(job)
                self.condition.notify_all()
            return self._result(job)

    def _reconcile(self, job, *, inspect=False):
        release = self.releases.get(job["review_id"])
        if release is None or digest(canonical(release)) != job["release_sha256"]:
            return
        path = _path(release["update_receipt"], exists=False)
        if not path.exists():
            if "behavior_verifier" in release:
                job.update(state="unconfirmed", reason="behavior_verification_unconfirmed")
                self._persist(job)
            return
        try:
            verified = _verify_release(
                job["review_id"],
                release,
                review_authorities=self.review_authorities,
                allow_legacy=True,
            )
            if (
                inspect
                and job["state"] in {"completed", "unconfirmed"}
                and "behavior_verifier" in release
                and self.inspect_release is not None
            ):
                update = dict(verified, task_id=job["task_id"])
                self.inspect_release(update)
            receipt = load_object(_path(path))
            decision = evaluate_updater_receipt(
                release, receipt, verified=verified, task_id=job["task_id"]
            )
        except (ValueError, OSError, KeyError, TypeError, RuntimeError):
            decision = (
                {"state": "unconfirmed", "reason": "behavior_verification_unconfirmed"}
                if "behavior_verifier" in release
                else None
            )
        if decision is None:
            return
        job["state"] = decision["state"]
        if "saved_artifact" in decision:
            job["saved_artifact"] = decision["saved_artifact"]
        if decision.get("clear_reason"):
            job.pop("reason", None)
        elif "reason" in decision:
            job["reason"] = decision["reason"]
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
                job = next(
                    (j for j in self.jobs.values() if j["state"] in {"queued", "waiting"}), None
                )
                if job is None:
                    self.condition.wait()
                    continue
            try:
                release = self.releases[job["review_id"]]
                if release.get("kind") == "owned_package":
                    if not callable(getattr(self, "owned_package_readiness", None)):
                        readiness = "waiting"
                    else:
                        readiness = self.owned_package_readiness(
                            job["task_id"],
                            _verify_release(
                                job["review_id"], release, review_authorities=self.review_authorities
                            ),
                        )
                else:
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
                    update = _verify_release(
                        job["review_id"], release, review_authorities=self.review_authorities
                    )
                    update["task_id"] = job["task_id"]
                    if update["receipt"].exists():
                        raise ValueError("an updater receipt already exists")
                except Exception:
                    job.update(state="rejected", reason="release_binding_changed")
                    self._persist(job)
                    continue
                job["state"] = "dispatching"
                self._persist(job)
            try:
                self.apply_release(update)
            except Exception:
                pass
            with self.condition:
                job.update(state="unconfirmed", reason="inspect_existing_update_receipt")
                self._persist(job)
                self._reconcile(job)

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()
        self.thread.join(timeout=1)
        if not self.thread.is_alive():
            self._lock_file.close()


__all__ = [
    "ReviewedComponentApplications",
    "_artifact",
    "_behavior_confirms",
    "_identifier",
    "_path",
    "_save",
    "_verified_behavior_source",
    "_verify_release",
    "checked_evidence",
]
