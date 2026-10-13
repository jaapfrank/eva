"""Make a private Voice PE candidate config; never modify or flash its source.

Run with the pinned ESPHome environment. Its own YAML loader/dumper preserves
lambda/tag semantics. The caller supplies a NEW destination in a private build
directory; resolved secrets stay in that mode-0600 file and are never printed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re

from esphome import yaml_util
from esphome.core import Lambda


def prepare(source: Path, output: Path, component_id="eva_native_voice"):
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", component_id):
        raise ValueError("invalid component id")
    original = source.read_bytes()
    config = yaml_util.load_yaml(source)
    if config.get("micro_wake_word", {}).get("id") != "mww":
        raise ValueError("candidate requires the verified Voice PE wakeword binding")
    if config.get("voice_assistant", {}).get("id") != "va":
        raise ValueError("candidate requires the verified native HA binding")
    switches = {str(x.get("id")): x for x in config.get("switch", [])}
    sensors = {str(x.get("id")): x for x in config.get("binary_sensor", [])}
    if "master_mute_switch" not in switches or "hardware_mute_switch" not in sensors:
        raise ValueError("candidate has no verified hardware/software mute binding")
    call = f"id({component_id})"
    counts = {"start": 0, "stop": 0, "running": 0}

    def replace_actions(value):
        if isinstance(value, list):
            for child in value:
                replace_actions(child)
        elif isinstance(value, dict):
            for key in list(value):
                if key == "voice_assistant.start":
                    value.pop(key)
                    value["lambda"] = Lambda(f"{call}.eva_local_wake();")
                    counts["start"] += 1
                elif key == "voice_assistant.stop":
                    value.pop(key)
                    value["lambda"] = Lambda(f"{call}.eva_local_stop();")
                    counts["stop"] += 1
                elif key == "voice_assistant.is_running":
                    value.pop(key)
                    value["lambda"] = Lambda(f"return {call}.eva_session_active();")
                    counts["running"] += 1
                elif key == "micro_wake_word.disable_model" and str(value[key]) == "stop":
                    value.pop(key)
                    value["if"] = {"condition": {"lambda": Lambda(f"return !{call}.eva_session_active();")},
                                   "then": [{"micro_wake_word.disable_model": "stop"}]}
                else:
                    replace_actions(value[key])
    replace_actions(config)
    # Replace only the wake handler. Timer-stop behavior remains first priority;
    # the existing button logic, media controls and native HA entities remain.
    config["micro_wake_word"]["on_wake_word_detected"] = [{"if": {
        "condition": {"switch.is_off": "master_mute_switch"}, "then": [{"if": {
            "condition": {"switch.is_on": "timer_ringing"},
            "then": [{"switch.turn_off": "timer_ringing"}],
            "else": [{"if": {
                "condition": {"and": [{"switch.is_on": "wake_sound"},
                    {"lambda": Lambda(f"return !{call}.eva_session_active();")}]},
                "then": [{"script.execute": {"id": "play_sound", "priority": True,
                                               "sound_file": "wake_word_triggered_sound"}}],
            }}, {"lambda": Lambda(
                f'if (wake_word == "Stop" || wake_word == "stop") {call}.eva_local_stop(); '
                f'else {call}.eva_local_wake();')}],
        }}],
    }}]
    hardware = sensors["hardware_mute_switch"]
    hardware.setdefault("on_press", []).insert(0, {"lambda": Lambda(f"{call}.eva_set_muted(true);")})
    master = switches["master_mute_switch"]
    master.setdefault("turn_on_action", []).insert(0, {"lambda": Lambda(f"{call}.eva_set_muted(true);")})
    master.setdefault("on_turn_on", []).insert(0, {"lambda": Lambda(f"{call}.eva_set_muted(true);")})
    state = "id(hardware_mute_switch).state || id(i2s_mics).get_mute_state()"
    master.setdefault("on_turn_off", []).append({"lambda": Lambda(f"{call}.eva_set_muted({state});")})
    config["voice_assistant"]["on_client_connected"].append(
        {"lambda": Lambda(f"{call}.eva_set_muted({state});")})
    config["voice_assistant"]["on_client_disconnected"].insert(
        0, {"lambda": Lambda(f"{call}.eva_set_muted(true);")})
    boots = config["esphome"].setdefault("on_boot", [])
    if isinstance(boots, dict):
        boots = config["esphome"]["on_boot"] = [boots]
    boots.append({"priority": -100, "then": [{"lambda": Lambda(f"{call}.eva_set_muted({state});")}]})
    with open(output, "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as stream:
        stream.write(yaml_util.dump(config, show_secrets=True))
    return {"sourceSha256": hashlib.sha256(original).hexdigest(),
            "candidateSha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "replacedNativeActionCounts": counts,
            "microphoneInitiallyMutedUntilPhysicalStateRead": True,
            "hardwareAndSoftwareMuteBound": True,
            "stopModelMustBeEnabledDuringEvaSession": True,
            "sourceModified": source.read_bytes() != original,
            "deployed": False}


def prepare_overlay(source: Path, output: Path, native_base: Path, components: Path,
                    component_id="eva_native_voice"):
    """Bind the private native base to a compile-only component overlay."""
    config = yaml_util.load_yaml(source)
    included = config["packages"]["original"]
    if not isinstance(included, yaml_util.IncludeFile):
        raise ValueError("overlay must have an original !include package")
    included.file = native_base.resolve()
    config["external_components"][0]["source"] = {"type": "local", "path": str(components.resolve())}
    component = config["va_pipecat"]
    component["id"] = component_id
    component["on_phase"] = [{"then": [
        {"if": {"condition": {"lambda": Lambda('return phase != "idle";')},
                "then": [{"micro_wake_word.enable_model": "stop"},
                         {"mixer_speaker.apply_ducking": {"id": "media_mixing_input", "decibel_reduction": 20, "duration": "0s"}}],
                "else": [{"if": {"condition": {"and": [{"switch.is_off": "timer_ringing"},
                            {"not": {"media_player.is_announcing": {"id": "external_media_player"}}}]},
                                   "then": [{"micro_wake_word.disable_model": "stop"}]}},
                         {"mixer_speaker.apply_ducking": {"id": "media_mixing_input", "decibel_reduction": 0, "duration": "1s"}}]}},
        {"lambda": Lambda('id(voice_assistant_phase) = phase == "speaking" ? ${voice_assist_replying_phase_id} : (phase == "listening" ? ${voice_assist_listening_for_command_phase_id} : ${voice_assist_idle_phase_id});')},
        {"script.execute": "control_leds"},
    ]}]
    with open(output, "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as stream:
        stream.write(yaml_util.dump(config, show_secrets=True))
    return {"candidateSha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "nativeControls": True, "phaseLedAndMediaDucking": True,
            "stopModelEnabledThroughoutConversation": True, "deployed": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--component-id", default="eva_native_voice")
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.output, args.component_id)))
