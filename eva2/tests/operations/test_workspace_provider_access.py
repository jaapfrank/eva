"""Credential replacement preserves rollback access and refuses unsafe reuse."""
import hashlib
import json
import os

import pytest

from tools import workspace_provider_access as access


def test_private_reads_refuse_links_and_wrong_permissions(tmp_path):
    source = tmp_path / "secret"
    source.write_bytes(b"private")
    source.chmod(0o600)
    assert access.read_private(source, os.getuid(), 0o600) == b"private"
    link = tmp_path / "link"
    link.symlink_to(source)
    with pytest.raises(OSError):
        access.read_private(link, os.getuid(), 0o600)
    link.unlink()
    os.link(source, link)
    with pytest.raises(RuntimeError):
        access.read_private(source, os.getuid(), 0o600)
    link.unlink()
    source.chmod(0o644)
    with pytest.raises(RuntimeError):
        access.read_private(source, os.getuid(), 0o600)


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    uid = os.getuid()
    monkeypatch.setattr(access, "ACCESS_UID", uid)
    original_read = access.read_private
    monkeypatch.setattr(access, "read_private", lambda path, owner, mode: original_read(path, uid, mode))
    original_write = access.provider._write_private
    monkeypatch.setattr(access.provider, "_write_private", lambda path, data, **kwargs: original_write(path, data, uid=uid))
    selected = {n: {"name": n, "integration_type": "MCP", "enabled": True, "reachable": True,
                     "visibility": "public", "request_type": "SSE", "input_schema": {"type": "object"}}
                for n in ("write_file", "read_text_file")}
    catalog = access.provider.catalog_for(selected)
    old = tmp_path / "access-1"
    old.mkdir(mode=0o700)
    for name, data in (("token", b"old-token"), ("catalog.json", json.dumps(catalog).encode())):
        original_write(old / name, data, uid=uid)
    original_write(tmp_path / "admin-password", b"admin", uid=uid)
    content = "Eva's duurzame werkmap is voorbereid.\n"
    digest = hashlib.sha256(content.encode()).hexdigest()
    receipt = {"status": "standalone_prepared_core_disabled", "accessDirectory": str(old),
               "tokenId": "old-id", "tokenSha256": access.provider._digest(old / "token"),
               "catalogSha256": access.provider._digest(old / "catalog.json"), "independentContentSha256": digest}
    original_write(tmp_path / "receipt.json", json.dumps(receipt).encode(), uid=uid)
    class Helper:
        issued = 0
        permissions = ["tools.read", "tools.execute"]
        wrong_readback = False
        calls = []
        def _docker_json(self, *args):
            image = access.provider.GATEWAY_IMAGE if args[1] == access.provider.GATEWAY else access.provider.FILESYSTEM_IMAGE
            return [{"Image": image.rsplit("@", 1)[1], "State": {"Running": True}}]
        def _list_tools(self, *args, **kwargs):
            return list(selected.values())
        def _docker(self, *args):
            return digest
        def _login_contextforge(self, *args):
            return "admin-token"
        def _gateway_request(self, container, method, path, **kwargs):
            self.calls.append((method, path, kwargs["token"]))
            if method == "POST" and path == "/tokens":
                self.issued += 1
                return {"access_token": "new-token", "token": {"id": "new-id", "expires_at": "2026-10-08T12:00:00", "resource_scopes": self.permissions}}
            if method == "GET" and path == "/tokens":
                assert kwargs["expected"] == (403,)
                return {}
            assert method == "POST" and path == "/rpc"
            value = "wrong" if self.wrong_readback and kwargs["token"] == "new-token" else content
            return {"result": {"structuredContent": {"content": value}}}
    helper = Helper()
    monkeypatch.setattr(access.provider, "_helper", lambda: helper)
    return tmp_path, helper


def test_replacement_is_verified_without_revoking_or_replacing_predecessor(prepared):
    root, helper = prepared
    old = (root / "access-1/token").read_bytes()
    result = access._prepare(root, 2)
    assert result["status"] == "replacement_prepared_not_activated"
    assert result["previousTokenStillValid"] is True
    assert result["previousTokenRevoked"] is False
    assert (root / "access-1/token").read_bytes() == old
    assert (root / "access-2/catalog.json").read_bytes() == (root / "access-1/catalog.json").read_bytes()
    assert not any(method == "DELETE" for method, _, _ in helper.calls)
    assert "new-token" not in json.dumps(result)
    with pytest.raises(RuntimeError, match="already exists"):
        access._prepare(root, 2)
    assert helper.issued == 1


@pytest.mark.parametrize("failure", ["permissions", "readback"])
def test_failed_new_credential_retains_recovery_metadata_and_old_access(prepared, failure):
    root, helper = prepared
    if failure == "permissions":
        helper.permissions = ["admin"]
    else:
        helper.wrong_readback = True
    with pytest.raises(RuntimeError):
        access._prepare(root, 2)
    assert (root / "access-1/token").read_bytes() == b"old-token"
    assert (root / "access-2-issued.json").exists()
    assert not (root / "access-2-receipt.json").exists()
    with pytest.raises(RuntimeError, match="already exists"):
        access._prepare(root, 2)
    assert helper.issued == 1


def test_changed_previous_credential_is_rejected_before_issuance(prepared):
    root, helper = prepared
    (root / "access-1/token").write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="binding differs"):
        access._prepare(root, 2)
    assert helper.issued == 0
    assert not (root / "access-2").exists()
