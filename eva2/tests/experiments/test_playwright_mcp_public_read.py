"""Static regression checks for the one-page public browser-read candidate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "experiments" / "playwright-mcp-public-read-v1"


def test_public_read_profile_stays_one_page_and_one_allowlisted_destination() -> None:
    profile = json.loads((CANDIDATE / "profile.json").read_bytes())

    assert profile["contract"] == "EvaPlaywrightMcpPublicReadCandidateProfile/v1"
    assert profile["target"] == {
        "expectedTitle": "Example Domain",
        "requiredVisibleTexts": ["Example Domain"],
        "url": "https://example.com/",
    }
    assert profile["proxy"]["allowedDestination"] == "example.com:443"
    assert profile["tools"]["maximumCalls"] == 2
    assert profile["tools"]["sequence"] == ["browser_navigate", "browser_snapshot"]
    assert all(value is False for value in profile["nonclaims"].values())


def test_proxy_configuration_denies_private_networks_before_its_single_allow() -> None:
    profile = json.loads((CANDIDATE / "profile.json").read_bytes())
    config_path = CANDIDATE / "squid.conf"
    config = config_path.read_text(encoding="utf-8")

    assert hashlib.sha256(config_path.read_bytes()).hexdigest() == profile["proxy"][
        "configSha256"
    ]
    assert "acl allowed_destination dstdomain .example.com" in config
    assert "acl allowed_tls_port port 443" in config
    assert "http_access allow browser_read_method allowed_destination allowed_tls_port" in config
    assert config.index("http_access deny private_destination") < config.index(
        "http_access allow browser_read_method"
    ) < config.index("http_access deny all")
    for private_range in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "224.0.0.0/4",
    ):
        assert private_range in config


def test_runner_keeps_browser_private_and_explicitly_tests_denial() -> None:
    runner = (CANDIDATE / "smoke_candidate.py").read_text(encoding="utf-8")

    assert '"--internal"' in runner
    assert '"com.docker.network.bridge.inhibit_ipv4=true"' in runner
    assert 'set(browser["NetworkSettings"]["Networks"]) == {browser_network}' in runner
    assert '"--read-only"' in runner
    assert '"--cap-drop", "ALL"' in runner
    assert '"no-new-privileges"' in runner
    assert "1.1.1.1" in runner
    assert "www.iana.org" in runner
    assert "403 Forbidden" in runner
    assert '"--network", "host"' not in runner
    assert '"--privileged"' not in runner
