"""Read-only, bounded comparison of restored PostgreSQL memory data.

This proves supported COPY data and sequence values, not schema equivalence,
Hindsight API readiness or provider authorization. Unsupported dump forms fail.
"""
from __future__ import annotations

import hashlib
import re
import selectors
import subprocess
import tempfile
import time
from pathlib import Path

from eva.contracts.canonical_json import canonical_sha256
from tools.installation_external_restore import (
    validate_external_configuration, _pins_current, _database_current,
)
from tools.installation_recovery_package import read_installation_package
from tools.read_eva_portable_archive import _root_descriptor, _read_regular
from tools.portable_transport import MAX_ARCHIVE_BYTES

MAX_DATA_BYTES = 128 * 1024 * 1024
_IDENTIFIER = r'(?:[a-z_][a-z0-9_$]*|"(?:[^"]|"")+")'
_QUALIFIED = _IDENTIFIER + r'\.' + _IDENTIFIER
_COPY = re.compile(r'COPY (' + _QUALIFIED + r') \((' + _IDENTIFIER +
                   r'(?:, ' + _IDENTIFIER + r')*)\) FROM stdin;')
_SEQUENCE = re.compile(r"SELECT pg_catalog.setval\('((?:[^']|'')+)', (-?[0-9]+), (true|false)\);")
_SETTINGS = {
    "SET statement_timeout = 0;", "SET lock_timeout = 0;",
    "SET idle_in_transaction_session_timeout = 0;", "SET transaction_timeout = 0;",
    "SET client_encoding = 'UTF8';", "SET standard_conforming_strings = on;",
    "SELECT pg_catalog.set_config('search_path', '', false);",
    "SET check_function_bodies = false;", "SET xmloption = content;",
    "SET client_min_messages = warning;", "SET row_security = off;",
}


def _capture_data(argv, *, raw=None, timeout=120):
    """Bound pipe reads while keeping database bytes out of logs and errors."""
    with tempfile.TemporaryFile() as source:
        if raw is not None:
            source.write(raw)
        source.seek(0)
        process = subprocess.Popen(argv, stdin=source, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + timeout
        output = bytearray()
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise RuntimeError("memory verification command timed out")
                    chunk = process.stdout.read1(65536)
                    if not chunk:
                        break
                    output.extend(chunk)
                    if len(output) > MAX_DATA_BYTES:
                        raise RuntimeError("memory verification output exceeds bound")
            if process.wait(timeout=max(.01, deadline - time.monotonic())) != 0:
                raise RuntimeError("memory verification command failed")
            return bytes(output)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()


def data_inventory(raw):
    """Canonicalize dump row multisets; never execute SQL from the archive."""
    if not isinstance(raw, bytes) or len(raw) > MAX_DATA_BYTES:
        raise ValueError("memory dump exceeds verification bound")
    tables, sequences, versions = {}, {}, {}
    active = None
    rows = []
    for line in raw.decode('utf-8', errors='strict').split('\n'):
        if active is not None:
            if line == r"\.":
                tables[active] = {"rows": len(rows), "rows_sha256": canonical_sha256(sorted(rows))}
                active, rows = None, []
            else:
                if len(rows) >= 1_000_000:
                    raise ValueError("memory table row count exceeds bound")
                rows.append(hashlib.sha256(line.encode()).hexdigest())
            continue
        if line.startswith('-- Dumped from database version '):
            versions['database'] = line.removeprefix('-- Dumped from database version ')
        elif line.startswith('-- Dumped by pg_dump version '):
            versions['tool'] = line.removeprefix('-- Dumped by pg_dump version ')
        elif not line or line.startswith('--') or line in _SETTINGS:
            continue
        elif re.fullmatch(r"\\(?:un)?restrict [A-Za-z0-9]+", line):
            continue
        elif match := _COPY.fullmatch(line):
            if len(tables) >= 10000:
                raise ValueError("memory table inventory exceeds bound")
            active = match[1] + ' (' + match[2] + ')'
            if active in tables:
                raise ValueError("duplicate memory table data block")
        elif match := _SEQUENCE.fullmatch(line):
            if len(sequences) >= 10000:
                raise ValueError("memory sequence inventory exceeds bound")
            if match[1] in sequences:
                raise ValueError("duplicate memory sequence data")
            sequences[match[1]] = [int(match[2]), match[3] == 'true']
        else:
            raise ValueError("unsupported memory dump data form")
    if active is not None or set(versions) != {'database', 'tool'} or not tables:
        raise ValueError("incomplete memory dump data")
    if not all(re.fullmatch(r'(?:16|18)\.[0-9]+(?: \([^\r\n]+\))?', value) for value in versions.values()):
        raise ValueError("unsupported memory PostgreSQL version")
    return {"versions": versions, "tables": tables, "sequences": sequences}


def verify_memory_data(package_root, *, configuration, guard):
    """Verify exact archive-bound target data; do not release or start anything."""
    validate_external_configuration(configuration)
    if not callable(guard):
        raise ValueError("memory verification requires a current restore guard")
    guard()
    root = Path(package_root).resolve()
    package = read_installation_package(root)
    archive = package['archive_sha256']
    entry = package['manifest']['files']['memory/hindsight.dump']
    with _root_descriptor(root) as fd:
        raw = _read_regular(fd, 'memory/hindsight.dump', maximum=MAX_ARCHIVE_BYTES)
    if len(raw) != entry['size'] or hashlib.sha256(raw).hexdigest() != entry['sha256']:
        raise ValueError("memory archive bytes differ")
    # Gateway/document targets may already be running after promotion. This
    # memory-only check owns the database pin; the guard owns source/core fencing
    # and the full release verifier owns the other components' current states.
    _pins_current({**configuration, "writers": []})
    _database_current(configuration, empty=False)
    memory = configuration['memory']
    prefix = ['docker', 'exec', '--interactive', memory['container']['id']]
    expected = data_inventory(_capture_data(prefix + ['pg_restore', '--data-only',
        '--no-owner', '--no-privileges', '--file=-'], raw=raw))
    observed = data_inventory(_capture_data(prefix + ['pg_dump', '--no-password',
        '--data-only', '--no-owner', '--no-privileges', '--encoding=UTF8',
        '--username', memory['user'], '--dbname', memory['database']['name']]))
    if expected != observed:
        raise ValueError("restored memory data differs from archive")
    guard()
    _pins_current({**configuration, "writers": []})
    _database_current(configuration, empty=False)
    if read_installation_package(root)['archive_sha256'] != archive:
        raise ValueError("memory archive changed during verification")
    return {"contract": "EvaRestoredMemoryDataVerification/v1", "archive_sha256": archive,
            "source_sha256": entry['sha256'], "target_binding_sha256": canonical_sha256(memory),
            "data_inventory_sha256": canonical_sha256(observed),
            "table_count": len(observed['tables']), "sequence_count": len(observed['sequences']),
            "scope": "COPY data and sequence values only; schema and API authorization not verified"}
