"""Use Hermes's native device login in its own existing comparison profile.

Only the public device URL/code are printed by native Hermes. Tokens remain in
its own store and are never imported, returned to the runner or written to logs.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    args = parser.parse_args()
    source, profile = args.source.resolve(), args.profile.resolve()
    if (source / ".env").exists() or not (profile / "config.yaml").is_file():
        raise RuntimeError("Expected isolated source/profile not available")
    retained = {key: os.environ[key] for key in ("PATH", "HOME", "LANG") if key in os.environ}
    os.environ.clear()
    os.environ.update(retained)
    os.environ["HERMES_HOME"] = str(profile)
    os.environ["CODEX_HOME"] = str(profile / "empty-codex-home")
    os.chdir(profile)
    sys.path.insert(0, str(source))
    from hermes_cli.config import load_config_readonly
    from hermes_cli.auth import _save_codex_tokens, resolve_codex_runtime_credentials
    from hermes_cli.auth_codex import _codex_device_code_login
    if load_config_readonly().get("auth", {}).get("adopt_external_logins") is not False:
        raise RuntimeError("External login adoption must be disabled")
    try:
        login = _codex_device_code_login()
        _save_codex_tokens(login["tokens"], last_refresh=login["last_refresh"], set_active=True)
        del login
        native = resolve_codex_runtime_credentials(read_only=True)
        state = {"status": "native_login_saved", "provider": native["provider"],
                 "source": native.get("source"), "baseUrl": native["base_url"],
                 "externalInferenceRequests": 0, "admission": "not_admitted"}
        del native
        (profile / "login-status.json").write_text(json.dumps(state, indent=2) + "\n")
        print(json.dumps(state), flush=True)
    except Exception as exc:
        state = {"status": "native_login_failed", "errorType": type(exc).__name__,
                 "errorCode": getattr(exc, "code", None)}
        (profile / "login-status.json").write_text(json.dumps(state, indent=2) + "\n")
        print(json.dumps(state), flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
