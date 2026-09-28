"""Real subprocess boundary tests; backend fixtures are not model quality proof."""

from datetime import UTC, datetime
from pathlib import Path
import subprocess
import sys

import pytest

from eva.adapters.connectors.home_assistant_camera import CameraSnapshot
from eva.adapters.connectors.local_vision import LocalVisionAnalyzer


def snapshot():
    return CameraSnapshot("camera.front", "image/png", b"\x89PNG\r\n\x1a\nimage fixture", datetime.now(UTC))


def backend(tmp_path, text):
    script = tmp_path / "backend.py"
    script.write_text(text)
    return (sys.executable, str(script))


def test_existing_sensory_contract_retains_provenance_without_image_bytes(tmp_path):
    marker = tmp_path / "input_path"
    command = backend(tmp_path, f'''import sys,json,hashlib,pathlib
p=pathlib.Path(sys.argv[1]); pathlib.Path({str(marker)!r}).write_text(str(p))
print(json.dumps(dict(image_sha256=hashlib.sha256(p.read_bytes()).hexdigest(),model="fixture",revision="fixture-v1",description="Possibly three bins.")))
''')
    image = snapshot()
    event = LocalVisionAnalyzer(command).analyze(image)
    assert event.event_type == "visual_interpretation"
    assert event.payload["image_sha256"] == image.sha256
    assert event.payload["retrieved_at"] == image.retrieved_at.isoformat()
    assert event.payload["captured_at"] is None
    assert event.payload["evidence_kind"] == "model_interpretation_not_verified_fact"
    assert event.payload["description"] == "Possibly three bins."
    assert not Path(marker.read_text()).exists()


@pytest.mark.parametrize("code,error", [
    ('print("not json")', ValueError),
    ('print("{}")', ValueError),
    ('raise SystemExit(1)', subprocess.CalledProcessError),
    ('import time; time.sleep(10)', subprocess.TimeoutExpired),
])
def test_failure_and_deadline_do_not_create_event(tmp_path, code, error):
    with pytest.raises(error):
        LocalVisionAnalyzer(backend(tmp_path, code), timeout_seconds=1).analyze(snapshot())
