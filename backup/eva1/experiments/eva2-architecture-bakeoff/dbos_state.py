from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote


STATE_CONTRACT = "EvaDBOSPostgresState/v2"
CREDENTIALS_CONTRACT = "EvaDBOSPostgresCredentials/v1"
POSTGRES_IMAGE_DIGEST = (
    "sha256:4e6e670bb069649261c9c18031f0aded7bb249a5b6664ddec29c013a89310d50"
)
POSTGRES_IMAGE = f"docker.io/library/postgres@{POSTGRES_IMAGE_DIGEST}"
POSTGRES_USER = "eva2"
POSTGRES_DATABASE = "eva2"
POSTGRES_HOST = "127.0.0.1"
SYSTEM_SCHEMA = "eva2_dbos_candidate"
MANIFEST_FILENAME = "dbos-postgres.json"
CREDENTIALS_FILENAME = "postgres-credentials.json"
PASSWORD_FILENAME = "postgres-password"
DATA_DIRNAME = "postgres-data"


@dataclass(frozen=True)
class DBOSState:
    state_dir: Path
    container_name: str
    state_id: str
    postgres_image: str
    postgres_image_digest: str
    postgres_image_id: str
    postgres_host: str
    postgres_port: int
    postgres_user: str
    postgres_database: str
    postgres_password: str
    postgres_data_dir: Path
    password_file: Path
    credentials_sha256: str
    binding_sha256: str
    system_schema: str

    @property
    def database_url(self) -> str:
        user = quote(self.postgres_user, safe="")
        password = quote(self.postgres_password, safe="")
        database = quote(self.postgres_database, safe="")
        return (
            f"postgresql://{user}:{password}@{self.postgres_host}:"
            f"{self.postgres_port}/{database}"
        )


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _state_directory(value: str | Path, *, create: bool = False) -> Path:
    raw = Path(value)
    if create:
        if (
            not raw.is_absolute()
            or re.fullmatch(
                r"/tmp/[A-Za-z0-9._/-]+",
                str(raw),
            )
            is None
        ):
            raise ValueError("DBOS state-dir moet vooraf onder /tmp liggen")
        raw.mkdir(parents=True, exist_ok=True)
    directory = raw.resolve(strict=True)
    if not directory.is_dir() or directory == Path("/tmp"):
        raise ValueError("DBOS state-dir is geen eigen directory")
    if re.fullmatch(r"/tmp/[A-Za-z0-9._/-]+", str(directory)) is None:
        raise ValueError("DBOS state-dir moet een eenvoudig pad onder /tmp zijn")
    return directory


def _read_secure_json(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"statebestand is geen regulier bestand: {path.name}")
    file_stat = path.stat()
    if file_stat.st_uid != os.geteuid() or stat.S_IMODE(file_stat.st_mode) & 0o077:
        raise ValueError(f"statebestand is niet eigenaar-only: {path.name}")
    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        raise ValueError(f"statebestand bevat geen object: {path.name}")
    return body


def _write_secure_json(path: Path, body: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(_canonical_json(body) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_password(path: Path, password: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="ascii") as output:
        output.write(password + "\n")
        output.flush()
        os.fsync(output.fileno())


def _credentials_body(password_sha256: str) -> dict[str, object]:
    return {
        "contract": CREDENTIALS_CONTRACT,
        "database": POSTGRES_DATABASE,
        "password_file": PASSWORD_FILENAME,
        "password_sha256": password_sha256,
        "user": POSTGRES_USER,
    }


def _sealed_binding(manifest: dict[str, object]) -> str:
    return _sha256_json(
        {
            key: manifest[key]
            for key in (
                "container_name",
                "credentials_file",
                "credentials_sha256",
                "postgres_data_dir",
                "postgres_host",
                "postgres_image",
                "postgres_image_digest",
                "postgres_image_id",
                "state_id",
                "system_schema",
            )
        }
    )


def initialize_state(state_dir: str | Path, postgres_image_id: str) -> DBOSState:
    directory = _state_directory(state_dir, create=True)
    manifest_path = directory / MANIFEST_FILENAME
    credentials_path = directory / CREDENTIALS_FILENAME
    password_path = directory / PASSWORD_FILENAME
    data_dir = directory / DATA_DIRNAME
    if manifest_path.exists() or credentials_path.exists() or password_path.exists():
        raise ValueError("statebinding bestaat al")
    if data_dir.exists() and any(data_dir.iterdir()):
        raise ValueError("weiger bestaande PostgreSQL-data zonder statebinding")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", postgres_image_id):
        raise ValueError("ongeldig lokaal PostgreSQL-image-id")
    data_dir.mkdir(mode=0o700, exist_ok=True)
    password = secrets.token_hex(32)
    password_sha256 = hashlib.sha256(password.encode("ascii")).hexdigest()
    credentials = _credentials_body(password_sha256)
    credentials_sha256 = _sha256_json(credentials)
    token = secrets.token_hex(12)
    manifest: dict[str, object] = {
        "binding_sha256": "",
        "container_name": f"eva2-dbos-{token}",
        "contract": STATE_CONTRACT,
        "credentials_file": CREDENTIALS_FILENAME,
        "credentials_sha256": credentials_sha256,
        "postgres_data_dir": DATA_DIRNAME,
        "postgres_host": POSTGRES_HOST,
        "postgres_image": POSTGRES_IMAGE,
        "postgres_image_digest": POSTGRES_IMAGE_DIGEST,
        "postgres_image_id": postgres_image_id,
        "postgres_port": 0,
        "state_id": f"eva2-dbos-state-{token}",
        "system_schema": SYSTEM_SCHEMA,
    }
    manifest["binding_sha256"] = _sealed_binding(manifest)
    try:
        _write_password(password_path, password)
        _write_secure_json(credentials_path, credentials)
        _write_secure_json(manifest_path, manifest)
    except BaseException:
        for path in (manifest_path, credentials_path, password_path):
            if path.exists() and not path.is_symlink():
                path.unlink()
        raise
    return load_state(
        directory, expected_image_id=postgres_image_id, require_port=False
    )


def load_state(
    state_dir: str | Path,
    *,
    expected_image_id: str | None = None,
    require_port: bool = True,
) -> DBOSState:
    directory = _state_directory(state_dir)
    manifest = _read_secure_json(directory / MANIFEST_FILENAME)
    manifest_keys = {
        "binding_sha256",
        "container_name",
        "contract",
        "credentials_file",
        "credentials_sha256",
        "postgres_data_dir",
        "postgres_host",
        "postgres_image",
        "postgres_image_digest",
        "postgres_image_id",
        "postgres_port",
        "state_id",
        "system_schema",
    }
    if set(manifest) != manifest_keys or manifest.get("contract") != STATE_CONTRACT:
        raise ValueError("ongeldig DBOS statecontract")
    if manifest.get("postgres_image") != POSTGRES_IMAGE:
        raise ValueError("PostgreSQL-image wijkt af van de sealed digestreferentie")
    if manifest.get("postgres_image_digest") != POSTGRES_IMAGE_DIGEST:
        raise ValueError("PostgreSQL-digest wijkt af")
    image_id = manifest.get("postgres_image_id")
    if (
        not isinstance(image_id, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None
    ):
        raise ValueError("ongeldig PostgreSQL-image-id in state")
    if expected_image_id is not None and image_id != expected_image_id:
        raise ValueError("lokale PostgreSQL-image wijkt af van state")
    if manifest.get("credentials_file") != CREDENTIALS_FILENAME:
        raise ValueError("onverwacht credentialspad")
    if manifest.get("postgres_data_dir") != DATA_DIRNAME:
        raise ValueError("onverwacht PostgreSQL-datadirpad")
    if manifest.get("postgres_host") != POSTGRES_HOST:
        raise ValueError("PostgreSQL is niet aan localhost gebonden")
    if manifest.get("system_schema") != SYSTEM_SCHEMA:
        raise ValueError("onverwacht DBOS system schema")
    container_name = manifest.get("container_name")
    state_id = manifest.get("state_id")
    if (
        not isinstance(container_name, str)
        or re.fullmatch(r"eva2-dbos-[0-9a-f]{24}", container_name) is None
    ):
        raise ValueError("ongeldige kandidaatcontainernaam")
    if (
        not isinstance(state_id, str)
        or re.fullmatch(r"eva2-dbos-state-[0-9a-f]{24}", state_id) is None
    ):
        raise ValueError("ongeldige kandidaat-state-id")
    port = manifest.get("postgres_port")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65_535:
        raise ValueError("ongeldige PostgreSQL-poort")
    if require_port and port == 0:
        raise ValueError("PostgreSQL-container heeft nog geen gebonden poort")
    credentials = _read_secure_json(directory / CREDENTIALS_FILENAME)
    if set(credentials) != set(_credentials_body("")):
        raise ValueError("ongeldige credentialvelden")
    if credentials.get("contract") != CREDENTIALS_CONTRACT:
        raise ValueError("ongeldig credentialscontract")
    if (
        credentials.get("user") != POSTGRES_USER
        or credentials.get("database") != POSTGRES_DATABASE
    ):
        raise ValueError("onverwachte PostgreSQL-principal")
    if credentials.get("password_file") != PASSWORD_FILENAME:
        raise ValueError("onverwacht passwordpad")
    password_path = directory / PASSWORD_FILENAME
    if password_path.is_symlink() or not password_path.is_file():
        raise ValueError("passwordbestand is niet regulier")
    password_stat = password_path.stat()
    if (
        password_stat.st_uid != os.geteuid()
        or stat.S_IMODE(password_stat.st_mode) & 0o077
    ):
        raise ValueError("passwordbestand is niet eigenaar-only")
    password_raw = password_path.read_text(encoding="ascii")
    if re.fullmatch(r"[0-9a-f]{64}\n", password_raw) is None:
        raise ValueError("passwordbestand heeft ongeldige vorm")
    password = password_raw[:-1]
    password_sha256 = hashlib.sha256(password.encode("ascii")).hexdigest()
    if credentials.get("password_sha256") != password_sha256:
        raise ValueError("password wijkt af van credentialsbinding")
    credentials_sha256 = _sha256_json(credentials)
    if manifest.get("credentials_sha256") != credentials_sha256:
        raise ValueError("credentials wijken af van statebinding")
    binding_sha256 = manifest.get("binding_sha256")
    if not isinstance(binding_sha256, str) or binding_sha256 != _sealed_binding(
        manifest
    ):
        raise ValueError("sealed statebinding wijkt af")
    data_dir = directory / DATA_DIRNAME
    if data_dir.is_symlink() or not data_dir.is_dir() or data_dir.resolve() != data_dir:
        raise ValueError("PostgreSQL-datadir is niet lokaal en regulier")
    return DBOSState(
        state_dir=directory,
        container_name=container_name,
        state_id=state_id,
        postgres_image=POSTGRES_IMAGE,
        postgres_image_digest=POSTGRES_IMAGE_DIGEST,
        postgres_image_id=image_id,
        postgres_host=POSTGRES_HOST,
        postgres_port=port,
        postgres_user=POSTGRES_USER,
        postgres_database=POSTGRES_DATABASE,
        postgres_password=password,
        postgres_data_dir=data_dir,
        password_file=password_path,
        credentials_sha256=credentials_sha256,
        binding_sha256=binding_sha256,
        system_schema=SYSTEM_SCHEMA,
    )


def update_port(state_dir: str | Path, port: int, postgres_image_id: str) -> DBOSState:
    state = load_state(
        state_dir,
        expected_image_id=postgres_image_id,
        require_port=False,
    )
    if isinstance(port, bool) or not 1 <= port <= 65_535:
        raise ValueError("ongeldige nieuwe PostgreSQL-poort")
    path = state.state_dir / MANIFEST_FILENAME
    manifest = _read_secure_json(path)
    manifest["postgres_port"] = port
    _write_secure_json(path, manifest)
    return load_state(state.state_dir, expected_image_id=postgres_image_id)


def forget_binding(state_dir: str | Path, postgres_image_id: str) -> None:
    state = load_state(state_dir, expected_image_id=postgres_image_id)
    if any(state.postgres_data_dir.iterdir()):
        raise ValueError("PostgreSQL-data moet vóór de statebinding worden gewist")
    for name in (MANIFEST_FILENAME, CREDENTIALS_FILENAME, PASSWORD_FILENAME):
        path = state.state_dir / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("statebinding veranderde tijdens cleanup")
        path.unlink()
    state.postgres_data_dir.rmdir()


def _emit_values(state: DBOSState) -> None:
    for value in (
        state.container_name,
        state.state_id,
        state.postgres_image,
        state.postgres_image_digest,
        state.postgres_image_id,
        state.credentials_sha256,
        state.binding_sha256,
        str(state.postgres_data_dir),
        str(state.password_file),
        state.postgres_user,
        state.postgres_database,
        str(state.postgres_port),
    ):
        print(value)


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    image_parser = subparsers.add_parser("image")
    image_parser.set_defaults(handler=lambda _args: print(POSTGRES_IMAGE))
    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("state_dir")
    init_parser.add_argument("image_id")
    values_parser = subparsers.add_parser("values")
    values_parser.add_argument("state_dir")
    values_parser.add_argument("image_id")
    port_parser = subparsers.add_parser("update-port")
    port_parser.add_argument("state_dir")
    port_parser.add_argument("image_id")
    port_parser.add_argument("port", type=int)
    forget_parser = subparsers.add_parser("forget")
    forget_parser.add_argument("state_dir")
    forget_parser.add_argument("image_id")
    args = parser.parse_args()
    if args.command == "init":
        _emit_values(initialize_state(args.state_dir, args.image_id))
    elif args.command == "values":
        _emit_values(
            load_state(
                args.state_dir,
                expected_image_id=args.image_id,
                require_port=False,
            )
        )
    elif args.command == "update-port":
        _emit_values(update_port(args.state_dir, args.port, args.image_id))
    elif args.command == "forget":
        forget_binding(args.state_dir, args.image_id)
    else:
        args.handler(args)


if __name__ == "__main__":
    main()
