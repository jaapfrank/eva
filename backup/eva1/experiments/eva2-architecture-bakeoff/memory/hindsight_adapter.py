#!/usr/bin/env python3
"""Disposable Hindsight 0.9.1 projection adapter for the sealed memory probe.

The account-free run uses Hindsight's shipped ``LLM_PROVIDER=none`` chunk mode.
That is a real server/API/storage run with real local retrieval, but it does not
exercise Hindsight's LLM temporal extraction.  Results are therefore labelled as
infrastructure/provenance evidence, never as memory-quality evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from canonical_memory import (
    CRASH_EXIT_CODE,
    ERASE_SENTINEL,
    EVENT_CANARY_ALPHA,
    EVENT_CANARY_BETA,
    EVENT_CRASH_WRITE,
    EVENT_DECAF,
    EVENT_ERASE_TARGET,
    EVENT_MEETING_CORRECTION,
    EVENT_REGULAR,
    NOW,
    SUBJECT_SAM,
    TENANT_ALPHA,
    TENANT_BETA,
    THEN,
    _append_retraction,
    _erase_source,
    append_events,
    canonical_json,
    connect,
    crash_event,
    filler_events,
    initialize_authority,
    recall,
    recall_exact_events,
    rebuild_projection,
    sealed_events_without_crash,
    validate_recall_provenance,
)


IMAGE = (
    "ghcr.io/vectorize-io/hindsight@"
    "sha256:aeb0831e2ec55e659d5e69baa064c1278113e523513b8f60a1f09c5cabe59b9b"
)
IMAGE_INDEX_DIGEST = "sha256:a0e937366261b8a8f20ebcaf13758c689c381dcbbf01684e4375c2787c8c666d"
IMAGE_REVISION = "e5b49eb6729512bd9b103058daa93f701da25644"
VERSION = "0.9.1"
CONTRACT = "EvaHindsightInfrastructureProbeScale/v1"


class HttpFailure(RuntimeError):
    def __init__(self, method: str, path: str, status: int | None, detail: str):
        super().__init__(f"{method} {path}: status={status}: {detail}")
        self.method = method
        self.path = path
        self.status = status
        self.detail = detail


def http_json(
    base_url: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    timeout: float = 300.0,
    allowed_statuses: set[int] | None = None,
) -> tuple[int, dict[str, Any]]:
    encoded = None if body is None else canonical_json(body).encode("utf-8")
    headers = {"Accept": "application/json"}
    if encoded is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base_url + path, data=encoded, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
            return response.status, parsed
    except urllib.error.HTTPError as error:
        raw = error.read()
        parsed = raw.decode("utf-8", errors="replace") if raw else ""
        if allowed_statuses and error.code in allowed_statuses:
            try:
                return error.code, json.loads(parsed) if parsed else {}
            except json.JSONDecodeError:
                return error.code, {"raw": parsed}
        raise HttpFailure(method, path, error.code, parsed) from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise HttpFailure(method, path, None, str(error)) from error


def percentile(values: Sequence[float], quantile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * quantile) - 1)]


def directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _docker(*args: str, check: bool = True, timeout: float = 300.0) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["docker", *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )
    if check and completed.returncode != 0:
        raise RuntimeError(
            f"docker {' '.join(args)} failed rc={completed.returncode}: {completed.stderr.strip()}"
        )
    return completed


@dataclass
class DisposableHindsight:
    runtime: Path
    generation: str
    container_name: str = ""
    network_name: str = ""
    state_dir: Path | None = None
    base_url: str = ""
    startup_ms: float = 0.0

    def start(self) -> None:
        safe_root = self.runtime.resolve()
        docker_root = Path("/tmp/docker").resolve()
        if docker_root not in safe_root.parents:
            raise ValueError("Hindsight runtime must be an isolated child of /tmp/docker")
        # Include the mkdtemp parent nonce as well as the scale so concurrent or
        # interrupted bake-off runs can never share Docker names.
        token = f"{self.runtime.parent.name}-{self.runtime.name}".replace(".", "-").lower()
        self.container_name = f"{token}-{self.generation}"
        self.network_name = f"{self.container_name}-net"
        self.state_dir = self.runtime / f"state-{self.generation}"
        self.state_dir.mkdir()
        self.state_dir.chmod(0o777)
        _docker("network", "create", self.network_name)
        started = time.perf_counter_ns()
        _docker(
            "run",
            "-d",
            "--name",
            self.container_name,
            "--network",
            self.network_name,
            "--label",
            "eva2.memory.probe=true",
            "--mount",
            f"type=bind,src={self.state_dir},dst=/home/hindsight/.pg0",
            "-p",
            "127.0.0.1::8888",
            "-e",
            "HINDSIGHT_API_LLM_PROVIDER=none",
            "-e",
            "HINDSIGHT_API_WORKER_ID=eva2-memory-probe",
            "-e",
            "HINDSIGHT_API_LOG_LEVEL=warning",
            IMAGE,
        )
        port_output = _docker("port", self.container_name, "8888/tcp").stdout.strip()
        port = port_output.rsplit(":", 1)[-1]
        self.base_url = f"http://127.0.0.1:{port}"
        last_error = "not attempted"
        for _ in range(120):
            try:
                status, health = http_json(self.base_url, "GET", "/health", timeout=2.0)
                if status == 200 and health.get("status") == "healthy":
                    self.startup_ms = (time.perf_counter_ns() - started) / 1_000_000
                    return
                last_error = canonical_json(health)
            except Exception as error:  # service is expected to refuse while booting
                last_error = str(error)
            time.sleep(1)
        raise RuntimeError(f"Hindsight did not become healthy: {last_error}")

    def stats(self) -> dict[str, Any]:
        raw = _docker(
            "stats", "--no-stream", "--format", "{{json .}}", self.container_name
        ).stdout.strip()
        return json.loads(raw) if raw else {}

    def logs(self, tail: int = 200) -> str:
        return _docker("logs", "--tail", str(tail), self.container_name, check=False).stdout[-20000:]

    def stop_and_remove(self) -> dict[str, Any]:
        stats: dict[str, Any] = {}
        logs = ""
        if self.container_name:
            try:
                stats = self.stats()
            except Exception as error:
                stats = {"stats_error": str(error)}
            logs = self.logs()
            _docker("stop", "--time", "10", self.container_name, check=False, timeout=30)
            _docker("container", "rm", self.container_name, check=False)
        if self.network_name:
            _docker("network", "rm", self.network_name, check=False)
        return {"stats": stats, "logs_tail": logs[-4000:]}


class HindsightClient:
    def __init__(self, base_url: str, scale: int):
        self.base_url = base_url
        self.scale = scale
        self.token_usage = {"input": 0, "output": 0, "total": 0}
        self.write_attempts = 0
        self.failed_writes = 0

    def bank(self, tenant_id: str) -> str:
        suffix = "alpha" if tenant_id == TENANT_ALPHA else "beta"
        return f"eva2-{self.scale}-{suffix}"

    def version(self) -> dict[str, Any]:
        return http_json(self.base_url, "GET", "/version", timeout=10)[1]

    def create_banks(self) -> None:
        config = {
            "retain_extraction_mode": "chunks",
            "enable_observations": False,
            "enable_temporal_retrieval": False,
            "enable_graph_retrieval": False,
        }
        for tenant in (TENANT_ALPHA, TENANT_BETA):
            http_json(
                self.base_url,
                "PUT",
                f"/v1/default/banks/{self.bank(tenant)}",
                config,
                timeout=30,
            )

    @staticmethod
    def item(row: sqlite3.Row) -> dict[str, Any]:
        actionable = row["trust_status"] in {"direct_user", "attested", "trusted_connector"}
        tags = [f"trust:{row['trust_status']}", f"slot:{row['slot']}"]
        if actionable:
            tags.append("actionable")
        metadata = {
            "event_id": row["event_id"],
            "source_id": row["source_id"],
            "source_connector": row["source_connector"],
            "source_hash": row["source_hash"],
            "observed_at": row["observed_at"],
            "valid_from": row["valid_from"],
            "trust_status": row["trust_status"],
        }
        content = (
            f"EVENT_ID {row['event_id']}\nSUBJECT {row['subject_id']}\n"
            f"SLOT {row['slot']}\nFACT {row['value_text']}"
        )
        return {
            "content": content,
            "timestamp": row["observed_at"],
            "context": "derived projection of a canonical Eva memory event",
            "document_id": row["event_id"],
            "metadata": metadata,
            "tags": tags,
        }

    @staticmethod
    def filler_bundle(rows: Sequence[sqlite3.Row]) -> dict[str, Any]:
        if not rows:
            raise ValueError("empty filler bundle")
        payload = "\n".join(canonical_json(dict(row)) for row in rows)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        first = rows[0]
        last = rows[-1]
        return {
            "content": payload,
            "timestamp": last["observed_at"],
            "context": "JSONL bundle of canonical archive events; one line is one event",
            "document_id": f"filler-{first['sequence_no']}-{last['sequence_no']}-{digest[:12]}",
            "metadata": {
                "event_count": str(len(rows)),
                "first_sequence": str(first["sequence_no"]),
                "last_sequence": str(last["sequence_no"]),
                "bundle_sha256": digest,
            },
            "tags": ["actionable", "trust:trusted_connector", "slot:archive_reference"],
        }

    def projection_plan(
        self, connection: sqlite3.Connection, filler_events_per_document: int = 500
    ) -> tuple[dict[str, list[dict[str, Any]]], int]:
        rows = connection.execute(
            "SELECT * FROM canonical_events WHERE event_type = 'fact' ORDER BY sequence_no"
        ).fetchall()
        by_tenant: dict[str, list[dict[str, Any]]] = {TENANT_ALPHA: [], TENANT_BETA: []}
        filler_by_tenant: dict[str, list[sqlite3.Row]] = {TENANT_ALPHA: [], TENANT_BETA: []}
        for row in rows:
            if row["slot"] == "archive_reference":
                filler_by_tenant[row["tenant_id"]].append(row)
            else:
                by_tenant[row["tenant_id"]].append(self.item(row))
        for tenant, filler_rows in filler_by_tenant.items():
            for start in range(0, len(filler_rows), filler_events_per_document):
                by_tenant[tenant].append(
                    self.filler_bundle(filler_rows[start : start + filler_events_per_document])
                )
        return by_tenant, len(rows)

    def retain_items(self, tenant_id: str, items: Sequence[dict[str, Any]], batch_size: int = 250) -> None:
        path = f"/v1/default/banks/{self.bank(tenant_id)}/memories"
        for start in range(0, len(items), batch_size):
            batch = list(items[start : start + batch_size])
            self.write_attempts += len(batch)
            try:
                _, response = http_json(
                    self.base_url,
                    "POST",
                    path,
                    {"items": batch, "async": False},
                    timeout=600,
                )
            except Exception:
                self.failed_writes += len(batch)
                raise
            if not response.get("success") or response.get("items_count") != len(batch):
                self.failed_writes += len(batch)
                raise AssertionError(f"Hindsight retain acknowledgement mismatch: {response}")
            usage = response.get("usage") or {}
            self.token_usage["input"] += int(usage.get("input_tokens", 0))
            self.token_usage["output"] += int(usage.get("output_tokens", 0))
            self.token_usage["total"] += int(usage.get("total_tokens", 0))

    def project(self, connection: sqlite3.Connection) -> dict[str, int]:
        by_tenant, event_count = self.projection_plan(connection)
        document_count = 0
        for tenant, items in by_tenant.items():
            self.retain_items(tenant, items)
            document_count += len(items)
        return {"events": event_count, "documents": document_count}

    def raw_recall_ids(self, tenant_id: str, query: str, *, actionable: bool = True) -> list[str]:
        body: dict[str, Any] = {
            "query": query,
            "budget": "low",
            "max_tokens": 512,
            "include": {"entities": None, "chunks": None, "source_facts": None},
        }
        if actionable:
            body.update({"tags": ["actionable"], "tags_match": "all_strict"})
        _, response = http_json(
            self.base_url,
            "POST",
            f"/v1/default/banks/{self.bank(tenant_id)}/memories/recall",
            body,
            timeout=120,
        )
        event_ids: list[str] = []
        for result in response.get("results", []):
            metadata = result.get("metadata") or {}
            event_id = metadata.get("event_id") or result.get("document_id")
            if isinstance(event_id, str):
                event_ids.append(event_id)
        return list(dict.fromkeys(event_ids))

    def gated_recall(
        self,
        connection: sqlite3.Connection,
        tenant_id: str,
        query: str,
        *,
        as_of: str = NOW,
        subject_id: str | None = None,
        slot: str | None = None,
    ) -> dict[str, Any]:
        event_ids = self.raw_recall_ids(tenant_id, query, actionable=True)
        result = recall_exact_events(
            connection,
            tenant_id=tenant_id,
            event_ids=event_ids,
            as_of=as_of,
            subject_id=subject_id,
            slot=slot,
            actionable_only=True,
            backend="hindsight_chunks_plus_canonical_barrier",
        )
        validate_recall_provenance(connection, result)
        return result

    def delete_document(self, tenant_id: str, document_id: str) -> dict[str, Any]:
        encoded = urllib.parse.quote(document_id, safe="")
        return http_json(
            self.base_url,
            "DELETE",
            f"/v1/default/banks/{self.bank(tenant_id)}/documents/{encoded}",
            timeout=120,
        )[1]

    def document_status(self, tenant_id: str, document_id: str) -> int:
        encoded = urllib.parse.quote(document_id, safe="")
        return http_json(
            self.base_url,
            "GET",
            f"/v1/default/banks/{self.bank(tenant_id)}/documents/{encoded}",
            timeout=30,
            allowed_statuses={404},
        )[0]

    def document_total(self) -> int:
        total = 0
        for tenant in (TENANT_ALPHA, TENANT_BETA):
            _, response = http_json(
                self.base_url,
                "GET",
                f"/v1/default/banks/{self.bank(tenant)}/documents?limit=0&offset=0",
                timeout=60,
            )
            total += int(response["total"])
        return total


def _projection_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        "SELECT * FROM canonical_events WHERE event_type = 'fact' ORDER BY sequence_no"
    ).fetchall()


def _prepare_before_crash(connection: sqlite3.Connection, event_count: int) -> None:
    fixed = sealed_events_without_crash()
    filler_count = event_count - len(fixed) - 1
    with connection:
        sequence = append_events(connection, fixed, 0)
        append_events(connection, filler_events(filler_count, sequence), sequence)
    rebuild_projection(connection)


def _add_crash_to_authority(connection: sqlite3.Connection) -> sqlite3.Row:
    sequence = int(
        connection.execute("SELECT COALESCE(MAX(sequence_no), -1) + 1 FROM canonical_events").fetchone()[0]
    )
    with connection:
        append_events(connection, [crash_event()], sequence)
    rebuild_projection(connection)
    return connection.execute(
        "SELECT * FROM canonical_events WHERE event_id = ?", (EVENT_CRASH_WRITE,)
    ).fetchone()


def _semantic_snapshot(client: HindsightClient, connection: sqlite3.Connection) -> dict[str, list[str]]:
    calls = {
        "coffee_now": (TENANT_ALPHA, "Sam koffie gewone decaf", NOW, "coffee_preference"),
        "coffee_then": (TENANT_ALPHA, "Sam koffie gewone decaf", THEN, "coffee_preference"),
        "canary_alpha": (TENANT_ALPHA, "Sam tenantcanary", NOW, "tenant_canary"),
        "canary_beta": (TENANT_BETA, "Sam tenantcanary", NOW, "tenant_canary"),
        "direct_correction": (
            TENANT_ALPHA,
            "Sam afspraak voorkeurstijd correctie veiligheidsregels geheimen",
            NOW,
            "meeting_time_preference",
        ),
        "crash_write": (
            TENANT_ALPHA,
            "geaccepteerde geheugenwrite crash readback",
            NOW,
            "crash_probe",
        ),
        "erased_source": (TENANT_ALPHA, ERASE_SENTINEL, NOW, "erasable_note"),
    }
    snapshot: dict[str, list[str]] = {}
    for name, (tenant, query, as_of, slot) in calls.items():
        recalled = client.gated_recall(
            connection,
            tenant,
            query,
            as_of=as_of,
            subject_id=SUBJECT_SAM,
            slot=slot,
        )
        snapshot[name] = [item["event_id"] for item in recalled["results"]]
    expected = {
        "coffee_now": [EVENT_DECAF],
        "coffee_then": [EVENT_REGULAR],
        "direct_correction": [EVENT_MEETING_CORRECTION],
        "crash_write": [EVENT_CRASH_WRITE],
        "erased_source": [],
    }
    for name, event_ids in expected.items():
        if snapshot[name] != event_ids:
            raise AssertionError(f"Hindsight gated semantic fixture mismatch for {name}: {snapshot[name]}")
    if snapshot["canary_alpha"] != [EVENT_CANARY_ALPHA]:
        raise AssertionError(f"alpha tenant canary failure: {snapshot['canary_alpha']}")
    if snapshot["canary_beta"] != [EVENT_CANARY_BETA]:
        raise AssertionError(f"beta tenant canary failure: {snapshot['canary_beta']}")
    return snapshot


def _latency(client: HindsightClient, connection: sqlite3.Connection, samples: int = 4) -> dict[str, Any]:
    probes = [
        (TENANT_ALPHA, "Sam koffie gewone decaf", NOW, "coffee_preference"),
        (TENANT_ALPHA, "Sam afspraak voorkeurstijd correctie", NOW, "meeting_time_preference"),
        (TENANT_ALPHA, "Sam tenantcanary", NOW, "tenant_canary"),
        (TENANT_BETA, "Sam tenantcanary", NOW, "tenant_canary"),
    ]
    values: list[float] = []
    for index in range(samples):
        tenant, query, as_of, slot = probes[index % len(probes)]
        started = time.perf_counter_ns()
        recalled = client.gated_recall(
            connection,
            tenant,
            query,
            as_of=as_of,
            subject_id=SUBJECT_SAM,
            slot=slot,
        )
        if not recalled["results"]:
            raise AssertionError("Hindsight latency probe returned no result")
        values.append((time.perf_counter_ns() - started) / 1_000_000)
    return {
        "samples": len(values),
        "p50_ms": round(percentile(values, 0.50), 3),
        "p95_ms": round(percentile(values, 0.95), 3),
        "max_ms": round(max(values), 3),
    }


def _crash_retain(base_url: str, bank: str, item_path: Path) -> None:
    item = json.loads(item_path.read_text(encoding="utf-8"))
    _, response = http_json(
        base_url,
        "POST",
        f"/v1/default/banks/{bank}/memories",
        {"items": [item], "async": False},
        timeout=600,
    )
    if not response.get("success") or response.get("items_count") != 1:
        os._exit(85)
    # Deliberately terminate before any candidate recall/readback or acknowledgement.
    os._exit(CRASH_EXIT_CODE)


def run_scale(runtime: Path, event_count: int) -> dict[str, Any]:
    if event_count not in {10_000, 100_000}:
        raise ValueError("sealed Hindsight scale must be 10,000 or 100,000")
    started = time.perf_counter_ns()
    database = runtime / "canonical.sqlite3"
    connection = connect(database)
    initialize_authority(connection)
    _prepare_before_crash(connection, event_count)

    first = DisposableHindsight(runtime, "initial")
    second: DisposableHindsight | None = None
    try:
        first.start()
        client = HindsightClient(first.base_url, event_count)
        version = client.version()
        if version.get("api_version") != VERSION:
            raise AssertionError(f"Hindsight API version mismatch: {version}")
        client.create_banks()
        projected_before_crash = client.project(connection)
        if projected_before_crash["events"] != event_count - 1:
            raise AssertionError("pre-crash Hindsight projection count mismatch")

        crash_row = _add_crash_to_authority(connection)
        crash_item_path = runtime / "crash-item.json"
        crash_item_path.write_text(canonical_json(client.item(crash_row)) + "\n", encoding="utf-8")
        crash_child = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "_crash-retain",
                "--base-url",
                first.base_url,
                "--bank",
                client.bank(TENANT_ALPHA),
                "--item",
                str(crash_item_path),
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=660,
        )
        if crash_child.returncode != CRASH_EXIT_CODE or crash_child.stdout or crash_child.stderr:
            raise AssertionError(
                f"Hindsight crash child mismatch rc={crash_child.returncode} "
                f"stdout={crash_child.stdout!r} stderr={crash_child.stderr!r}"
            )
        crash_raw_ids = client.raw_recall_ids(
            TENANT_ALPHA, "geaccepteerde geheugenwrite crash readback"
        )
        if EVENT_CRASH_WRITE not in crash_raw_ids:
            raise AssertionError("Hindsight lost accepted write across adapter crash")
        count_at_scale = int(connection.execute("SELECT COUNT(*) FROM canonical_events").fetchone()[0])
        documents_at_scale = projected_before_crash["documents"] + 1
        if count_at_scale != event_count or client.document_total() != documents_at_scale:
            raise AssertionError("Hindsight 10k/100k scale count mismatch")

        _append_retraction(connection)
        rebuild_projection(connection)
        raw_after_retraction = client.raw_recall_ids(
            TENANT_ALPHA, ERASE_SENTINEL, actionable=False
        )
        gated_after_retraction = recall_exact_events(
            connection,
            tenant_id=TENANT_ALPHA,
            event_ids=raw_after_retraction,
            backend="hindsight_retraction_authority_barrier",
        )
        if gated_after_retraction["results"]:
            raise AssertionError("retracted Hindsight source crossed the authority barrier")

        delete_response = client.delete_document(TENANT_ALPHA, EVENT_ERASE_TARGET)
        if not delete_response.get("success"):
            raise AssertionError(f"Hindsight delete failed: {delete_response}")
        _erase_source(connection)
        if client.document_status(TENANT_ALPHA, EVENT_ERASE_TARGET) != 404:
            raise AssertionError("Hindsight erase readback still finds document")
        raw_after_erase = client.raw_recall_ids(TENANT_ALPHA, ERASE_SENTINEL, actionable=False)
        if EVENT_ERASE_TARGET in raw_after_erase:
            raise AssertionError("Hindsight erased document remains recallable")

        snapshot_before_rebuild = _semantic_snapshot(client, connection)
        latency = _latency(client, connection)
        first_storage = directory_size(first.state_dir or runtime)
        first_stats = first.stats()
        final_fact_count = len(_projection_rows(connection))
        final_plan, planned_final_events = client.projection_plan(connection)
        final_document_count = sum(len(items) for items in final_plan.values())
        if planned_final_events != final_fact_count or client.document_total() != final_document_count:
            raise AssertionError("Hindsight document count after erase differs from canonical facts")

        first.stop_and_remove()
        if first.state_dir is None:
            raise AssertionError("missing initial state directory")
        shutil.rmtree(first.state_dir)
        if first.state_dir.exists():
            raise AssertionError("initial Hindsight projection was not destroyed")

        second = DisposableHindsight(runtime, "rebuild")
        second.start()
        rebuilt_client = HindsightClient(second.base_url, event_count)
        rebuilt_client.create_banks()
        rebuilt_count = rebuilt_client.project(connection)
        if (
            rebuilt_count["events"] != final_fact_count
            or rebuilt_count["documents"] != final_document_count
            or rebuilt_client.document_total() != final_document_count
        ):
            raise AssertionError("Hindsight rebuild count mismatch")
        snapshot_after_rebuild = _semantic_snapshot(rebuilt_client, connection)
        if snapshot_before_rebuild != snapshot_after_rebuild:
            raise AssertionError("Hindsight rebuild changed gated recalls")
        rebuild_storage = directory_size(second.state_dir or runtime)
        rebuild_stats = second.stats()

        # Stop the candidate and prove the same query falls back to canonical SQL/FTS.
        second.stop_and_remove()
        if second.state_dir is None:
            raise AssertionError("missing rebuilt state directory")
        shutil.rmtree(second.state_dir)
        if second.state_dir.exists():
            raise AssertionError("rebuilt Hindsight projection was not destroyed")
        try:
            rebuilt_client.raw_recall_ids(TENANT_ALPHA, "Sam koffie")
            raise AssertionError("stopped Hindsight unexpectedly answered recall")
        except HttpFailure:
            fallback = recall(
                connection,
                tenant_id=TENANT_ALPHA,
                subject_id=SUBJECT_SAM,
                slot="coffee_preference",
                query="koffie",
            )
        validate_recall_provenance(connection, fallback)
        if fallback["results"][0]["event_id"] != EVENT_DECAF:
            raise AssertionError("Hindsight outage SQL fallback failed")

        return {
            "contract": CONTRACT,
            "scale": event_count,
            "classification": "account_free_real_hindsight_chunks_infrastructure_gate",
            "semantic_quality": "not_proven_without_real_llm_extraction",
            "candidate": {
                "name": "Hindsight",
                "version": VERSION,
                "image": IMAGE,
                "image_index_digest": IMAGE_INDEX_DIGEST,
                "source_revision": IMAGE_REVISION,
                "api_version_response": version,
                "llm_provider": "none",
                "retain_mode": "chunks",
            },
            "canonical_event_count_at_scale_gate": count_at_scale,
            "projection_before_crash": projected_before_crash,
            "projection_at_scale_gate": {
                "canonical_events": count_at_scale,
                "hindsight_documents": documents_at_scale,
            },
            "projection_after_rebuild": rebuilt_count,
            "crash_before_readback": {
                "adapter_exit_code": crash_child.returncode,
                "accepted_event_id": EVENT_CRASH_WRITE,
                "raw_recall_contains_event": True,
            },
            "retraction": {
                "candidate_still_held_source_before_erase": EVENT_ERASE_TARGET in raw_after_retraction,
                "gated_recall_count": 0,
            },
            "erase": {
                "delete_response": delete_response,
                "document_get_status": 404,
                "raw_recall_contains_event": False,
            },
            "snapshot_before_rebuild": snapshot_before_rebuild,
            "snapshot_after_rebuild": snapshot_after_rebuild,
            "rebuild_equivalent": snapshot_before_rebuild == snapshot_after_rebuild,
            "tenant_leaks": 0,
            "untrusted_promotions": 0,
            "provenance_failures": 0,
            "fallback": {"backend": fallback["backend"], "event_id": fallback["results"][0]["event_id"]},
            "latency": latency,
            "tokens": {
                key: client.token_usage[key] + rebuilt_client.token_usage[key]
                for key in client.token_usage
            },
            "writes": {
                "attempted": client.write_attempts + 1 + rebuilt_client.write_attempts,
                "failed": client.failed_writes + rebuilt_client.failed_writes,
            },
            "resources": {
                "cold_start_ms": round(first.startup_ms, 3),
                "rebuild_start_ms": round(second.startup_ms, 3),
                "initial_storage_bytes": first_storage,
                "rebuild_storage_bytes": rebuild_storage,
                "initial_docker_stats": first_stats,
                "rebuild_docker_stats": rebuild_stats,
            },
            "process_and_projection_destroyed": True,
            "total_duration_ms": round((time.perf_counter_ns() - started) / 1_000_000, 3),
        }
    finally:
        if first.container_name and _docker("inspect", first.container_name, check=False).returncode == 0:
            first.stop_and_remove()
        if second and second.container_name and _docker("inspect", second.container_name, check=False).returncode == 0:
            second.stop_and_remove()
        connection.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    crash_parser = subparsers.add_parser("_crash-retain")
    crash_parser.add_argument("--base-url", required=True)
    crash_parser.add_argument("--bank", required=True)
    crash_parser.add_argument("--item", required=True, type=Path)

    scale_parser = subparsers.add_parser("run-scale")
    scale_parser.add_argument("--runtime", required=True, type=Path)
    scale_parser.add_argument("--event-count", required=True, type=int)
    scale_parser.add_argument("--result", required=True, type=Path)

    args = parser.parse_args(argv)
    if args.command == "_crash-retain":
        _crash_retain(args.base_url, args.bank, args.item)
    elif args.command == "run-scale":
        result = run_scale(args.runtime, args.event_count)
        args.result.write_text(canonical_json(result) + "\n", encoding="utf-8")
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
