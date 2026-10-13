"""Prepare private device firmware inputs with existing pins; never flash.

Run in the retained ESPHome 2026.6.2 environment. The reviewed Woonkamer
package supplies pinned components, CA and the established audio overlay.
Only a new output directory is accepted. No runtime files are changed.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sys

from esphome import yaml_util


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def private_json(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
    path.chmod(0o600)


def main(args):
    os.umask(0o077)
    sys.dont_write_bytecode = True
    if args.output.exists():
        raise ValueError("Output must be a new directory")
    repo = Path(__file__).resolve().parents[2]
    if args.output.resolve().is_relative_to(repo.parent):
        raise ValueError("Private output must stay outside the product checkout")
    native = repo / "experiments/native-duplex-v1"
    sys.path.insert(0, str(native))
    from prepare_native_controls import prepare, prepare_overlay

    policy = json.loads(args.policy.read_text())
    if policy["devices"].get(args.satellite) != args.device:
        raise ValueError("Device does not match the existing household authority")
    source_hash = sha(args.source)
    original = yaml_util.load_yaml(args.source)
    expected_name = args.satellite.removeprefix("assist_satellite.").removesuffix("_assist_satellite").replace("_", "-")
    if original["esphome"]["name"] != expected_name:
        raise ValueError("Configuration name differs from the provisioned satellite")
    template = args.template.resolve()
    # Current direct drivers must match the independently reviewed firmware.
    for name in ["playback_stop_barrier.h", "eva_input_protocol.h", "eva_output_protocol.h",
                 "eva_microphone_source.h", "eva_input_driver.cpp", "eva_output_driver.cpp"]:
        if sha(native / name) != sha(template / "firmware/components/va_pipecat" / name):
            raise ValueError("Reviewed template driver differs from current source")
    args.output.mkdir(parents=True, mode=0o700)
    build = args.output / "candidate"
    recovery = args.output / "recovery"
    service = args.output / "service"
    for directory in [build, recovery, service]:
        directory.mkdir(mode=0o700)
    for folder in ["components", "vendor"]:
        shutil.copytree(template / "firmware" / folder, build / folder,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    base = build / "native-controls.yaml"
    receipt = prepare(args.source, base)
    config = yaml_util.load_yaml(base)
    pinned = yaml_util.load_yaml(template / "firmware/native-controls.yaml")
    # Preserve the reviewed local Voice Kit vendor instead of resolving a
    # floating external source from the historical device configuration.
    config["external_components"] = copy.deepcopy(pinned["external_components"])
    config["external_components"][0]["source"]["path"] = str(build / "vendor/components")
    base.write_text(yaml_util.dump(config, show_secrets=True))
    overlay = build / "device.yaml"
    prepare_overlay(template / "firmware/woonkamer.yaml", overlay, base, build / "components")
    data = yaml_util.load_yaml(overlay)
    credential = secrets.token_urlsafe(32)
    data["va_pipecat"]["eva_credential"] = credential
    overlay.write_text(yaml_util.dump(data, show_secrets=True))
    # Recovery retains the device's pre-native configuration. It is not a
    # substitute for a physical full-flash backup or verified restoration.
    old = copy.deepcopy(original)
    old["external_components"] = copy.deepcopy(config["external_components"])
    (recovery / "device.yaml").write_text(yaml_util.dump(old, show_secrets=True))
    for name in ["tls.crt", "tls.key", "ca.crt"]:
        shutil.copyfile(template / "service" / name, service / name)
    cfg = json.loads((template / "service/config.json").read_text())
    cfg["certificate"] = str(service / "tls.crt")
    cfg["private_key"] = str(service / "tls.key")
    binding = {"satellite_id": args.satellite, "device_id": args.device,
               "conversation_id": "native-provisioned-" + args.device, "language": "nl-NL"}
    cfg["grants"] = [{"binding": binding,
                      "credential_sha256": hashlib.sha256(credential.encode()).hexdigest()}]
    private_json(service / "config.json", cfg)
    if sha(args.source) != source_hash:
        raise ValueError("Source changed during preparation")
    # Assert preservation of account/network/device identity and house controls.
    # The existing helper intentionally rewires voice stop/start actions inside
    # media callbacks. Network/account identity must remain byte-equivalent;
    # media callbacks retain the helper's already reviewed native arbitration.
    for key in ["wifi", "api", "ota", "network", "timer"]:
        if key in original and yaml_util.dump(original[key], show_secrets=True) != yaml_util.dump(config[key], show_secrets=True):
            raise ValueError("Unexpected change to preserved device configuration: " + key)
    private_json(args.output / "preparation.json", {
        "status": "prepared_not_compiled_or_flashed", "device": args.device,
        "satellite": args.satellite, "name": expected_name,
        "source": str(args.source), "source_sha256": source_hash,
        "candidate_sha256": sha(overlay), "native_controls_sha256": sha(base),
        "recovery_config_sha256": sha(recovery / "device.yaml"),
        "service_config_sha256": sha(service / "config.json"),
        "template": str(template), "existing_controls": receipt,
        "preserved": ["wifi", "api", "ota", "network"],
        "media_controls": "existing reviewed native arbitration helper",
        "compiled": False, "flashed": False, "physical_recovery_verified": False,
    })
    print(json.dumps({"prepared": True, "output": str(args.output), "source_unchanged": True}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["source", "template", "policy", "output"]:
        parser.add_argument("--" + name, required=True, type=Path)
    for name in ["satellite", "device"]:
        parser.add_argument("--" + name, required=True)
    try:
        main(parser.parse_args())
    except Exception as error:
        # YAML/parser exceptions can contain resolved secrets.
        print(json.dumps({"prepared": False, "error_type": type(error).__name__}))
        raise SystemExit(1) from None
