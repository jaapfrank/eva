"""Offline guards for activation provenance; no Docker or live configuration calls."""
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("speaker_activation", Path(__file__).with_name("activation.py"))
activation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(activation)


class ActivationGuards(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.stage = Path(self.temporary.name)
        self.config = self.stage / "current.compose.json"
        self.config.write_text('{"services":{"core":{}}}')
        self.env = self.stage / "current.env"
        self.env.write_text("PRIVATE_FIXTURE=not-a-real-secret\n")
        self.core = {"Id": "current-core-id", "Image": "sha256:fresh-at-activation",
                     "State": {"Running": True}, "Mounts": [
                         {"Source": "/current/household-voice", "Destination": "/run/eva/household-voice", "RW": False},
                         {"Source": "/current/identity/current.json", "Destination": "/run/eva/identity/current.json", "RW": False}],
                     "Config": {"Env": ["PRIVATE_FIXTURE=not-a-real-secret", "EVA_HOUSEHOLD_VOICE_SESSION_ENABLED=false"],
                                "Labels": {"com.docker.compose.project": "current-project",
                                           "com.docker.compose.service": "core",
                                           "com.docker.compose.project.config_files": str(self.config),
                                           "com.docker.compose.project.environment_file": str(self.env),
                                           "com.docker.compose.project.working_dir": str(self.stage)}}}
        self.metadata = {"core_container": "current-core", "core_state": "/current", "sha256": {}}
        (self.stage / "stage.json").write_text(json.dumps(self.metadata))
        self.protected = {"eva2-owner-voice-media-1": {"Id": "owner-media"},
                          "eva2-owner-voice-livekit-1": {"Id": "owner-sfu"}}
        self.inspect = patch.object(activation, "inspect", side_effect=lambda name: copy.deepcopy(
            self.core if name == "current-core" else self.protected[name]))
        self.inspect.start()
        self.addCleanup(self.inspect.stop)
        self.docker = patch.object(activation, "docker", side_effect=lambda *args:
            json.dumps([{"Config": {"Env": []}}]) if args[0] == "image" else "")
        self.mock_docker = self.docker.start()
        self.addCleanup(self.docker.stop)
        self.render = patch.object(activation.subprocess, "check_output", return_value=json.dumps({
            "services": {"core": {"environment": {
                "PRIVATE_FIXTURE": "not-a-real-secret", "EVA_HOUSEHOLD_VOICE_SESSION_ENABLED": "false"}}}}))
        self.mock_render = self.render.start()
        self.addCleanup(self.render.stop)
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            activation.capture(self.stage)
        self.run = Path(json.loads(output.getvalue())["baseline"])

    def test_capture_freezes_current_image_and_emits_only_core_commands(self):
        commands = json.loads((self.run / "core-commands.json").read_text())
        for name, command in commands.items():
            self.assertEqual(command[-1], "core")
            self.assertIn("--no-deps", command)
            overlay = json.loads((self.run / f"core-{name}.json").read_text())["services"]["core"]
            self.assertEqual(overlay["image"], "sha256:fresh-at-activation")
        self.assertEqual(json.loads((self.run / "core-restore.json").read_text())["services"]["core"]["environment"]["EVA_HOUSEHOLD_VOICE_SESSION_ENABLED"], "false")
        self.assertEqual(self.mock_docker.call_count, 2)  # presence and immutable-image environment reads

    def test_only_expected_flag_delta_is_accepted(self):
        self.core["Config"]["Env"][-1] = "EVA_HOUSEHOLD_VOICE_SESSION_ENABLED=true"
        activation.check(self.stage, self.run)
        self.core["Config"]["Env"].append("UNRELATED_CHANGE=yes")
        with self.assertRaises(ValueError): activation.check(self.stage, self.run)

    def test_new_core_image_cannot_be_rolled_back_by_stale_baseline(self):
        self.core["Image"] = "sha256:later-release"
        with self.assertRaises(ValueError): activation.check(self.stage, self.run)

    def test_changed_core_input_and_mount_are_rejected(self):
        self.config.write_text("changed")
        with self.assertRaises(ValueError): activation.check(self.stage, self.run)
        self.config.write_text('{"services":{"core":{}}}')
        self.core["Mounts"][0]["Source"] = "/wrong-household"
        with self.assertRaises(ValueError): activation.check(self.stage, self.run)

    def test_owner_voice_change_is_detected(self):
        self.protected["eva2-owner-voice-media-1"]["Id"] = "replacement"
        with self.assertRaises(ValueError): activation.check(self.stage, self.run)

    def test_already_installed_speaker_is_not_adopted(self):
        self.mock_docker.side_effect = None
        self.mock_docker.return_value = "some-container"
        with self.assertRaises(ValueError): activation.capture(self.stage)

    def test_prepared_file_mutation_is_rejected(self):
        self.metadata["sha256"] = {self.env.name: hashlib.sha256(self.env.read_bytes()).hexdigest()}
        (self.stage / "stage.json").write_text(json.dumps(self.metadata))
        self.env.write_text("changed")
        with self.assertRaises(ValueError): activation.check(self.stage, self.run)

    def test_pending_compose_environment_is_not_a_rollback_baseline(self):
        self.mock_render.return_value = json.dumps({"services": {"core": {"environment": {"CHANGED": "yes"}}}})
        with self.assertRaises(ValueError): activation.capture(self.stage)


if __name__ == "__main__":
    unittest.main()
