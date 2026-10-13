"""Static regression checks for the one-page public browser-read candidate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "experiments" / "playwright-mcp-public-read-v1"
SIDECAR_CANDIDATE = ROOT / "experiments" / "playwright-mcp-sidecar-v1"
SYSTEM_REFRESH_CANDIDATE = ROOT / "experiments" / "playwright-mcp-system-refresh-v1"


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


def test_sidecar_runner_keeps_the_sealed_image_by_default_but_allows_a_local_candidate() -> None:
    """The remediation image must run through the existing identical test cell.

    Keeping the sealed upstream image as the default prevents a remediation
    experiment from silently changing the normal proof.  An explicit argument
    makes a locally-built, separately-scanned image testable in that same cell.
    """

    runner = (SIDECAR_CANDIDATE / "smoke_candidate.py").read_text(encoding="utf-8")

    assert 'def main(*, browser_image: str = PLAYWRIGHT_IMAGE)' in runner
    assert '"--browser-image"' in runner
    assert "default=PLAYWRIGHT_IMAGE" in runner
    assert "main(browser_image=parser.parse_args().browser_image)" in runner
    assert '"browserImage": browser_image' in runner
    assert 'if browser_image == PLAYWRIGHT_IMAGE:' in runner


def test_system_refresh_candidate_only_updates_the_official_base_and_unused_npm() -> None:
    dockerfile = (SYSTEM_REFRESH_CANDIDATE / "Dockerfile").read_text(encoding="utf-8")

    assert (
        "FROM mcr.microsoft.com/playwright/mcp@sha256:"
        "18c0a9c934004fe9580cc79f1e8e6e6cde7c667348b215335e8a23fd3e509804"
        in dockerfile
    )
    assert "apt-get update" in dockerfile
    assert "apt-get upgrade --yes --no-install-recommends" in dockerfile
    assert "/usr/local/lib/node_modules/npm" in dockerfile
    assert "USER node" in dockerfile
    assert "--no-sandbox" not in dockerfile
    assert "curl " not in dockerfile
