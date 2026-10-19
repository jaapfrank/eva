"""Synthetic local-contract checks; these do not measure recognition accuracy."""

from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
from http.server import HTTPServer
import json
import math
import sqlite3
import struct
import subprocess
import sys
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from eva.adapters.connectors.home_assistant_camera import CameraSnapshot
from eva.adapters.connectors.local_face_identity import (
    EnrolledFaceProfile, LocalFaceIdentityHttpAnalyzer, face_observation,
    local_http_endpoint, match_face, read_enrolled_profiles, validate_face_observation,
)
from eva.operations.local_face_identity_bridge import FaceIdentityBridge, make_handler


TOKEN = "synthetic-private-token-" + "a" * 32


def vector(first=1.0, second=0.0, third=0.0):
    return (first, second, third) + (0.0,) * 509


def image():
    return CameraSnapshot("camera.fixture", "image/png", b"\x89PNG\r\n\x1a\nfixture", datetime.now(UTC))


def detection(embedding, left=0, area=1600):
    return {"bbox": [left, 0, left + 40, 40], "oppervlak": area,
            "det_score": 0.99, "embedding": list(embedding)}


def test_multiple_detections_match_distinct_enrolled_people_without_embeddings():
    profiles = (EnrolledFaceProfile("Fixture A", vector(), 0.9),
                EnrolledFaceProfile("Fixture B", vector(0, 1), 0.9))
    result = face_observation(image(), {"gezichten": [detection(vector()), detection(vector(0, 1), 60)]},
                              profiles, profile_revision="a" * 64, backend_revision="fixture-v1")
    assert [face["person_label"] for face in result["faces"]] == ["Fixture A", "Fixture B"]
    assert "embedding" not in json.dumps(result)
    assert "image_b64" not in json.dumps(result)
    assert result["image_sha256"] == image().sha256
    assert "not_authentication" in result["evidence_kind"]
    assert all(type(face["similarity_ppm"]) is int and type(face["margin_ppm"]) is int
               for face in result["faces"])


def test_unknown_low_similarity_small_face_and_empty_profiles_disclose_no_candidate():
    profiles = (EnrolledFaceProfile("Fixture A", vector(), None),)
    assert match_face(vector(0, 1), profiles, area=1600) == {"status": "unknown"}
    assert match_face(vector(), profiles, area=699) == {"status": "unknown"}
    assert match_face(vector(), (), area=1600) == {"status": "unknown"}


def test_legacy_margin_uses_different_person_not_same_person_profile():
    profiles = (
        EnrolledFaceProfile("Fixture A", vector(0.4, math.sqrt(0.84)), 0.9),
        EnrolledFaceProfile("Fixture A", vector(0.39, 0, math.sqrt(1 - 0.39**2)), 0.9),
        EnrolledFaceProfile("Fixture B", vector(0.38, 0, math.sqrt(1 - 0.38**2)), 0.9),
    )
    assert match_face(vector(), profiles, area=1600) == {"status": "unknown"}
    accepted = match_face(vector(), profiles[:2], area=1600)
    assert accepted["person_label"] == "Fixture A"
    assert accepted["margin_ppm"] == 1_000_000


def test_read_only_reader_ignores_inactive_and_other_backends_and_preserves_db(tmp_path):
    path = tmp_path / "profiles.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE faces (id INTEGER PRIMARY KEY,person TEXT,vec BLOB,quality REAL,backend TEXT,active INTEGER)")
        conn.executemany("INSERT INTO faces VALUES (?,?,?,?,?,?)", [
            (1, "Fixture A", struct.pack("<512f", *vector()), .9, "arcface", 1),
            (2, "Not active", b"invalid", .9, "arcface", 0),
            (3, "Other backend", b"invalid", .9, "different", 1),
        ])
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    profiles, revision = read_enrolled_profiles(path)
    assert len(profiles) == 1
    assert profiles[0].person_label == "Fixture A"
    assert len(revision) == 64
    assert before == hashlib.sha256(path.read_bytes()).hexdigest()
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE faces SET person='Corrected fixture' WHERE id=1")
    assert read_enrolled_profiles(path)[1] != revision


def test_reader_fails_closed_on_incompatible_profile_without_creating_or_migrating_db(tmp_path):
    missing = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        read_enrolled_profiles(missing)
    assert not missing.exists()
    path = tmp_path / "invalid.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE faces (id INTEGER PRIMARY KEY,person TEXT,vec BLOB,quality REAL,backend TEXT,active INTEGER)")
        conn.execute("INSERT INTO faces VALUES (1,'Fixture A',?,0.9,'arcface',1)", (b"invalid",))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="encoding"):
        read_enrolled_profiles(path)
    assert path.read_bytes() == before


def test_crowd_beyond_bound_is_not_silently_truncated_or_misrepresented():
    with pytest.raises(ValueError, match="bound"):
        face_observation(image(), {"gezichten": [detection(vector())] * 9}, (),
                         profile_revision="a" * 64, backend_revision="fixture-v1")


@pytest.mark.parametrize("bad", [(0.0,) * 512, (float("nan"),) * 512, (1.0,) * 511])
def test_incompatible_or_invalid_embeddings_are_rejected(bad):
    with pytest.raises(ValueError):
        EnrolledFaceProfile("Fixture A", bad, 0.9)


def test_bounded_response_rejects_extra_fields_hash_and_unknown_candidate_label():
    result = face_observation(image(), {"gezichten": [detection(vector())]}, (),
                              profile_revision="a" * 64, backend_revision="fixture-v1")
    for changed in ({**result, "embedding": []}, {**result, "image_sha256": "b" * 64},
                    {**result, "faces": [{"region": [0, 0, 40, 40], "status": "unknown", "person_label": "Rejected"}]}):
        with pytest.raises(ValueError):
            validate_face_observation(changed, image().sha256, "fixture-v1")


@pytest.mark.parametrize("endpoint", ["http://0.0.0.0:3220", "https://127.0.0.1:3220", "http://8.8.8.8", "http://127.0.0.1/private", "http://user:pass@127.0.0.1"])
def test_endpoint_cannot_route_biometrics_to_public_or_unexpected_service(endpoint):
    with pytest.raises(ValueError):
        local_http_endpoint(endpoint)


class StubBridge:
    calls = 0

    def health(self):
        return {"ok": True}

    def analyze(self, snapshot):
        self.calls += 1
        return face_observation(snapshot, {"gezichten": []}, (),
                                profile_revision="a" * 64, backend_revision="fixture-v1")


@contextmanager
def server():
    bridge = StubBridge()
    instance = HTTPServer(("127.0.0.1", 0), make_handler(bridge, TOKEN))
    thread = threading.Thread(target=instance.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield f"http://127.0.0.1:{instance.server_port}", bridge
    finally:
        instance.shutdown()
        thread.join(2)
        instance.server_close()


def test_real_http_boundary_rejects_auth_hash_and_oversize_before_analysis():
    with server() as (endpoint, bridge):
        for headers, expected in [({}, 401),
                ({"Authorization": "Bearer " + TOKEN, "Content-Type": "image/png", "X-Image-SHA256": "b" * 64}, 400),
                ({"Authorization": "Bearer " + TOKEN, "Content-Type": "image/png", "Content-Length": "2097153"}, 413)]:
            request = Request(endpoint + "/analyze", data=image().data, headers=headers, method="POST")
            with pytest.raises(HTTPError) as error:
                urlopen(request, timeout=2)
            assert error.value.code == expected
        assert bridge.calls == 0
        analyzer = LocalFaceIdentityHttpAnalyzer(endpoint=endpoint, token=TOKEN, revision="fixture-v1")
        result = analyzer.analyze(image())
        assert result["faces"] == []
        assert bridge.calls == 1


def test_cpu_mode_is_explicit_and_requires_paired_configuration_and_revision(tmp_path):
    database = tmp_path / "profiles.db"
    database.touch()
    common = dict(profiles_db=str(database), backend_endpoint="http://127.0.0.1:3220")
    with pytest.raises(ValueError, match="both"):
        FaceIdentityBridge(**common, backend_revision="fixture:cpu", cpu_python=sys.executable)
    with pytest.raises(ValueError, match=":cpu"):
        FaceIdentityBridge(**common, backend_revision="fixture", cpu_python=sys.executable,
                           cpu_model_root=str(tmp_path))


def test_explicit_cpu_call_is_single_bounded_isolated_process_not_gpu_fallback(tmp_path, monkeypatch):
    database = tmp_path / "profiles.db"
    database.touch()
    bridge = FaceIdentityBridge(profiles_db=str(database), backend_endpoint="http://127.0.0.1:3220",
        backend_revision="fixture:cpu", cpu_python=sys.executable, cpu_model_root=str(tmp_path))
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        assert command[1] == "-I"
        assert command[2].endswith("local_face_cpu.py")
        assert kwargs["input"] == image().data
        assert kwargs["timeout"] == 12
        assert kwargs["env"]["CUDA_VISIBLE_DEVICES"] == ""
        assert kwargs["env"]["OMP_NUM_THREADS"] == "1"
        return subprocess.CompletedProcess(command, 0, stdout=b'{"aantal":0,"gezichten":[]}')

    monkeypatch.setattr(subprocess, "run", run)
    assert bridge._cpu_result(image().data) == {"aantal": 0, "gezichten": []}
    assert len(calls) == 1

    def failed(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", failed)
    with pytest.raises(RuntimeError, match="did not complete"):
        bridge._cpu_result(image().data)


def test_cpu_helper_requires_exact_existing_model_files_before_loading(tmp_path, monkeypatch):
    from eva.operations import local_face_cpu
    directory = tmp_path / "models" / "buffalo_l"
    directory.mkdir(parents=True)
    first = directory / "det_10g.onnx"
    second = directory / "w600k_r50.onnx"
    first.write_bytes(b"synthetic detector")
    second.write_bytes(b"synthetic recognizer")
    monkeypatch.setattr(local_face_cpu, "MODEL_HASHES", {
        first.name: hashlib.sha256(first.read_bytes()).hexdigest(),
        second.name: hashlib.sha256(second.read_bytes()).hexdigest(),
    })
    assert set(local_face_cpu.pinned_models(tmp_path)) == {first.name, second.name}
    first.write_bytes(b"changed")
    with pytest.raises(ValueError, match="differs"):
        local_face_cpu.pinned_models(tmp_path)
    first.unlink()
    with pytest.raises(ValueError, match="missing"):
        local_face_cpu.pinned_models(tmp_path)
