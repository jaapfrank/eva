"""Reconcile target bindings with fresh private operator authorization.

No upstream request is made. Approved hashes must be supplied independently by
trusted operator configuration, never inferred from targets or uploaded receipts.
"""
from __future__ import annotations
import hashlib
import hmac
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sqlalchemy as sa

from eva.contracts.canonical_json import canonical_sha256
from eva.operations.model_credentials import build_access_lease
from eva.adapters.credentials.runtime_files import read_shared_runtime_file_bytes
from tools.container_inspection import inspect_all as inspect_containers
from tools.installation_gateway_start import _host_file
from tools.installation_recovery_package import read_installation_package
from tools.restore_runtime_guard import verify_restore_runtime


def _path(value):
    if (not isinstance(value, str) or not value.startswith('/') or len(value) > 1024
            or str(PurePosixPath(value)) != value or '..' in PurePosixPath(value).parts):
        raise ValueError("runtime authorization path differs")


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
        raise ValueError("runtime authorization digest differs")


def validate_runtime_authorization_configuration(configuration):
    if not isinstance(configuration, dict) or set(configuration) != {'model_authority_file', 'writers'}:
        raise ValueError("runtime authorization configuration differs")
    _path(configuration['model_authority_file'])
    writers = configuration['writers']
    if not isinstance(writers, list) or not 1 <= len(writers) <= 64:
        raise ValueError("runtime authorization writer coverage differs")
    ids = set()
    for writer in writers:
        if not isinstance(writer, dict) or set(writer) != {'id', 'profile_sha256', 'model_lease_path', 'credential_files'}:
            raise ValueError("runtime authorization writer fields differ")
        _digest(writer['id']); _digest(writer['profile_sha256']); _path(writer['model_lease_path'])
        if writer['id'] in ids:
            raise ValueError("runtime authorization duplicate writer")
        ids.add(writer['id'])
        files = writer['credential_files']
        if not isinstance(files, list) or len(files) > 128:
            raise ValueError("runtime credential inventory differs")
        paths = {writer['model_lease_path']}
        for item in files:
            if not isinstance(item, dict) or set(item) != {'path', 'sha256'}:
                raise ValueError("runtime credential fields differ")
            _path(item['path']); _digest(item['sha256'])
            if item['path'] in paths:
                raise ValueError("runtime credential path duplicated")
            paths.add(item['path'])


def runtime_profile_sha256(row):
    """Exact native runtime launch and mount configuration approved by operator."""
    mounts = row.get('Mounts')
    if (not isinstance(mounts, list)
            or any(not isinstance(mount, dict) or not isinstance(mount.get('Destination'), str)
                   or not mount['Destination'].startswith('/') for mount in mounts)
            or len({mount['Destination'] for mount in mounts}) != len(mounts)):
        raise ValueError("runtime profile mount destinations are invalid or duplicated")
    # Docker serializes its mount map in unspecified order; command/env order stays exact.
    profile = {key: row.get(key) for key in ('Id', 'Image', 'Config', 'HostConfig')}
    host = profile['HostConfig']
    if isinstance(host, dict) and host.get('OomKillDisable') is None and 'OomKillDisable' in host:
        # Docker may report its enabled OOM-killer default as null after first start.
        profile['HostConfig'] = {**host, 'OomKillDisable': False}
    profile['Mounts'] = sorted(mounts, key=lambda mount: mount['Destination'])
    return canonical_sha256(profile)


def _image_public_trust_file(row, key, value):
    """The standard CA bundle is image-pinned public data, unless overridden."""
    if (key != 'SSL_CERT_FILE' or value != '/etc/ssl/certs/ca-certificates.crt'
            or row.get('HostConfig', {}).get('ReadonlyRootfs') is not True):
        return False
    destinations = [mount.get('Destination', '') for mount in row.get('Mounts', [])]
    destinations.extend(row.get('HostConfig', {}).get('Tmpfs', {}).keys())
    return not any(destination and (value == destination or value.startswith(destination.rstrip('/') + '/'))
                   for destination in destinations)


def _read_credential(path):
    if path.resolve() != path:
        raise ValueError("runtime credential path contains links")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 1 <= before.st_size <= 65536:
            raise ValueError("runtime credential file differs")
        raw = os.read(fd, 65537)
        after = os.fstat(fd)
        if (len(raw) != before.st_size or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise ValueError("runtime credential changed during read")
        return raw
    finally:
        os.close(fd)


def _runtime_file_host(container_path, row, environment, *, is_core, model_lease_path):
    """The core's erasure/restore authority is mutable state, not a secret.

    Its exact path, initial contents and mount remain operator approved. Other
    credential routes, including the model lease, must stay read-only.
    """
    try:
        return _host_file(container_path, row.get('Mounts', []), readonly=True)[0]
    except ValueError:
        aliases = [key for key, value in environment.items()
                   if key.endswith('_FILE') and value == container_path]
        if (not is_core or container_path == model_lease_path
                or aliases != ['EVA_PORTABLE_RESTORE_AUTHORITY_FILE']):
            raise
        return _host_file(container_path, row.get('Mounts', []), readonly=False)[0]


def verify_runtime_authorization(connection, *, package_root, restore_id,
                                 configuration, runtime_configuration, guard):
    validate_runtime_authorization_configuration(configuration)
    if not callable(guard):
        raise ValueError("runtime authorization requires current fencing")
    guard()
    verify_restore_runtime(connection, expected=runtime_configuration)
    root = Path(package_root).resolve()
    package = read_installation_package(root)
    hold = connection.execute(sa.text("SELECT restore_id,archive_sha256,target_database_oid,held FROM eva_core.restore_activation")).mappings().one()
    if (str(hold['restore_id']) != str(restore_id) or hold['held'] is not True
            or hold['archive_sha256'] != package['archive_sha256']
            or hold['target_database_oid'] != runtime_configuration['target_database']['oid']):
        raise ValueError("runtime authorization restore identity differs")
    authority = Path(configuration['model_authority_file'])
    expected_lease = build_access_lease(authority)
    profiles = _verify_targets(configuration, runtime_configuration, root, authority, expected_lease)
    bindings = connection.execute(sa.text("SELECT binding_id,status,credential_reference FROM eva_core.connector_bindings")).mappings().all()
    for binding in bindings:
        if (binding['status'] != 'reauthorization_required'
                or binding['credential_reference'] != 'portable-restore-redacted:' + str(binding['binding_id'])):
            raise ValueError("restored connector binding unexpectedly active")
    guard()
    verify_restore_runtime(connection, expected=runtime_configuration)
    if (build_access_lease(authority) != expected_lease
            or read_installation_package(root)['archive_sha256'] != package['archive_sha256']):
        raise ValueError("runtime authorization source changed")
    if _verify_targets(configuration, runtime_configuration, root, authority, expected_lease) != profiles:
        raise ValueError("runtime authorization targets changed")
    return {'contract': 'EvaRestoredRuntimeAuthorization/v1', 'verified': True,
        'scope': 'locally_authorized_target_bindings', 'restore_id': str(restore_id),
        'archive_sha256': package['archive_sha256'], 'target_database_oid': hold['target_database_oid'],
        'authorized_profiles': profiles, 'pending_connector_reauthorizations': len(bindings),
        'upstream_response_verified': False}


def _verify_targets(configuration, runtime_configuration, root, authority, expected_lease):
    rows = inspect_containers()
    pins = {item['id']: item for item in runtime_configuration['target']['workers']}
    if {item['id'] for item in configuration['writers']} != set(pins):
        raise ValueError("runtime authorization target coverage differs")
    if authority == root or root in authority.parents:
        raise ValueError("model authorization cannot come from recovery material")
    profiles = {}
    for writer in configuration['writers']:
        matches = [row for row in rows if row.get('Id') == writer['id']]
        if len(matches) != 1 or runtime_profile_sha256(matches[0]) != writer['profile_sha256']:
            raise ValueError("runtime target profile is not currently authorized")
        row = matches[0]
        env = {}
        for setting in row.get('Config', {}).get('Env', []):
            key, sep, value = setting.partition('=')
            if not sep or key in env:
                raise ValueError("runtime target environment differs")
            env[key] = value
        is_core = pins[writer['id']]['name'] == runtime_configuration['target']['core_name']
        field = 'EVA_CONVERSATION_AUTH_FILE' if is_core else 'EVA_MODEL_ACCESS_LEASE_FILE'
        actual_path = env.get(field, '/run/eva/codex/auth.json' if is_core else '')
        if actual_path != writer['model_lease_path']:
            raise ValueError("runtime model lease route differs")
        for other in ('EVA_CONVERSATION_AUTH_FILE', 'EVA_MODEL_ACCESS_LEASE_FILE'):
            if env.get(other) and env[other] != writer['model_lease_path']:
                raise ValueError("runtime model lease routes disagree")
        approved = {item['path']: item['sha256'] for item in writer['credential_files']}
        covered = set(approved) | {writer['model_lease_path']}
        for key, value in env.items():
            if (value and key.endswith('_FILE') and value not in covered
                    and not _image_public_trust_file(row, key, value)):
                raise ValueError("runtime credential route lacks current authorization")
        for container_path in covered:
            host = _runtime_file_host(container_path, row, env, is_core=is_core,
                                      model_lease_path=writer['model_lease_path'])
            if host == root or root in host.parents:
                raise ValueError("runtime credentials cannot come from recovery material")
            if container_path == writer['model_lease_path']:
                actual = read_shared_runtime_file_bytes(str(host), setting_name='restored model lease',
                    owner_uid=1000, reader_gid=65532, required_mode=0o640)
                if not hmac.compare_digest(actual, expected_lease):
                    raise ValueError("runtime model lease does not match current owner authorization")
            elif not hmac.compare_digest(hashlib.sha256(_read_credential(host)).hexdigest(), approved[container_path]):
                raise ValueError("runtime credential is not currently authorized")
        profiles[writer['id']] = writer['profile_sha256']
    return profiles
