#!/usr/bin/env python3
"""Run a bounded, replay-resistant PC05 text latency corpus."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse


_FIRST_TOKEN = re.compile(r"(?u)(?<!\w)[^\W\d_]+(?:[-'][^\W\d_]+)*(?=\s|[.,!?;:])")


@dataclass(frozen=True)
class CorpusTurn:
    index: int
    principal_id: int
    message_id: int
    phase: str
    prompt: str
    expected_reply: str


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def build_corpus(*, turns: int, run_seed: int) -> tuple[CorpusTurn, ...]:
    if turns < 2 or turns % 2:
        raise ValueError("turns must be an even number of at least two")
    if not 1 <= run_seed <= 9999:
        raise ValueError("run_seed must be between 1 and 9999")
    corpus: list[CorpusTurn] = []
    principal_base = 991_000_000 + run_seed * 10_000
    message_base = 9_000_000_000 + run_seed * 100_000
    for index in range(turns):
        pair = index // 2
        phase = "cold" if index % 2 == 0 else "warm"
        expected = "SNEL klaar" if phase == "cold" else "WARM klaar"
        prompt = (
            f"Gewone tekstproef {pair + 1}, deel {phase}. "
            f'Antwoord exact met: "{expected}".'
        )
        corpus.append(
            CorpusTurn(
                index=index + 1,
                principal_id=principal_base + pair,
                message_id=message_base + index + 1,
                phase=phase,
                prompt=prompt,
                expected_reply=expected,
            )
        )
    return tuple(corpus)


def first_complete_token(text: str) -> bool:
    return _FIRST_TOKEN.search(text) is not None


def normalized_exact_reply(text: str) -> str:
    return text.strip().removesuffix(".")


def percentile(values: list[int], quantile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(quantile * len(ordered)))
    return ordered[rank - 1]


def _milliseconds(start_ns: int) -> int:
    return max(0, round((time.monotonic_ns() - start_ns) / 1_000_000))


def execute_turn(
    *,
    parsed_base_url,
    turn: CorpusTurn,
    timeout_seconds: float,
) -> dict[str, object]:
    body = _canonical_json(
        {
            "chat_id": str(turn.principal_id),
            "installation_id": "live-test-installation",
            "message_id": turn.message_id,
            "occurred_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "recent_messages": [],
            "telegram_user_id": turn.principal_id,
            "tenant_id": "live-test-tenant",
            "text": turn.prompt,
        }
    )
    connection_class = (
        http.client.HTTPSConnection if parsed_base_url.scheme == "https" else http.client.HTTPConnection
    )
    connection = connection_class(
        parsed_base_url.hostname,
        parsed_base_url.port,
        timeout=timeout_seconds,
    )
    started_ns = time.monotonic_ns()
    first_content_ms: int | None = None
    accumulated = ""
    final: dict[str, object] | None = None
    delta_count = 0
    error: str | None = None
    status: int | None = None
    try:
        connection.request(
            "POST",
            "/internal/v1/telegram/canary/respond",
            body=body,
            headers={
                "Accept": "application/x-ndjson",
                "Content-Type": "application/json",
            },
        )
        response = connection.getresponse()
        status = response.status
        if status != 200:
            raise RuntimeError(f"http_{status}")
        if "application/x-ndjson" not in response.getheader("Content-Type", ""):
            raise RuntimeError("response_not_ndjson")
        while line := response.readline():
            item = json.loads(line)
            contract = item.get("contract")
            if contract == "EvaTextDelta/v1" and isinstance(item.get("text"), str):
                delta_count += 1
                accumulated += item["text"]
                if first_content_ms is None and first_complete_token(accumulated):
                    first_content_ms = _milliseconds(started_ns)
            elif contract == "EvaInternalGroundedReply/v1":
                final = item
            else:
                raise RuntimeError("invalid_stream_contract")
        if final is None:
            raise RuntimeError("missing_final_reply")
        expected_conversation = f"telegram:{turn.principal_id}"
        expected_message = f"telegram:{turn.principal_id}:{turn.message_id}"
        if final.get("conversation_id") != expected_conversation:
            raise RuntimeError("wrong_conversation_id")
        if final.get("message_id") != expected_message:
            raise RuntimeError("wrong_message_id")
        final_text = final.get("text")
        if final_text != accumulated:
            raise RuntimeError("stream_final_mismatch")
        if normalized_exact_reply(accumulated) != turn.expected_reply:
            raise RuntimeError("unexpected_reply")
        if first_content_ms is None:
            raise RuntimeError("no_complete_lexical_token")
    except Exception as exc:  # noqa: BLE001 - errors belong in the measurement denominator
        error = type(exc).__name__ + ":" + str(exc)[:120]
    finally:
        connection.close()
    completed_ms = _milliseconds(started_ns)
    if error and completed_ms < round(timeout_seconds * 1000):
        failure_latency_ms = completed_ms
    elif error:
        failure_latency_ms = round(timeout_seconds * 1000)
    else:
        failure_latency_ms = first_content_ms
    return {
        "index": turn.index,
        "phase": turn.phase,
        "httpStatus": status,
        "success": error is None,
        "firstContentMilliseconds": failure_latency_ms,
        "completeMilliseconds": completed_ms,
        "deltaCount": delta_count,
        "replySha256": _sha256(accumulated.encode()) if accumulated else None,
        "error": error,
    }


def summarize(observations: list[dict[str, object]], *, target_ms: int) -> dict[str, object]:
    first = [int(row["firstContentMilliseconds"]) for row in observations]
    complete = [int(row["completeMilliseconds"]) for row in observations]
    successful = sum(row["success"] is True for row in observations)
    return {
        "turns": len(observations),
        "successfulTurns": successful,
        "failedTurns": len(observations) - successful,
        "firstContentP50Milliseconds": percentile(first, 0.50),
        "firstContentP95Milliseconds": percentile(first, 0.95),
        "firstContentMaximumMilliseconds": max(first),
        "completeP95Milliseconds": percentile(complete, 0.95),
        "turnsWithinTarget": sum(value <= target_ms for value in first),
        "targetMilliseconds": target_ms,
    }


def _write_receipt(path: Path, receipt: dict[str, object]) -> None:
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"refusing to overwrite receipt: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(receipt, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--run-seed", required=True, type=int)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--turns", type=int, default=200)
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument("--target-milliseconds", type=int, default=2000)
    args = parser.parse_args()
    parsed = urlparse(args.base_url)
    if parsed.scheme not in {"http", "https"} or parsed.path not in {"", "/"}:
        parser.error("base URL must contain only http(s), host and optional port")
    corpus = build_corpus(turns=args.turns, run_seed=args.run_seed)
    manifest = {
        "contract": "EvaPc05TextCorpusManifest/v1",
        "turns": args.turns,
        "coldTurns": args.turns // 2,
        "warmTurns": args.turns // 2,
        "route": "/internal/v1/telegram/canary/respond",
        "networkClass": "host_loopback",
        "streamContentType": "application/x-ndjson",
        "prompts": [turn.prompt for turn in corpus],
    }
    manifest_sha256 = _sha256(_canonical_json(manifest))
    observations: list[dict[str, object]] = []
    started = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    for turn in corpus:
        observations.append(
            execute_turn(
                parsed_base_url=parsed,
                turn=turn,
                timeout_seconds=args.timeout_seconds,
            )
        )
        if turn.index % 20 == 0 or turn.index == len(corpus):
            current = summarize(observations, target_ms=args.target_milliseconds)
            print(
                json.dumps(
                    {
                        "progress": turn.index,
                        "failed": current["failedTurns"],
                        "p95": current["firstContentP95Milliseconds"],
                    },
                    separators=(",", ":"),
                ),
                flush=True,
            )
    summary = summarize(observations, target_ms=args.target_milliseconds)
    passed = summary["failedTurns"] == 0 and summary["firstContentP95Milliseconds"] <= args.target_milliseconds
    receipt = {
        "contract": "EvaPc05TextLatencyDevelopmentReceipt/v1",
        "startedAt": started,
        "completedAt": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "status": "passed" if passed else "failed",
        "sourceCommit": args.source_commit,
        "imageId": args.image_id,
        "baseUrl": args.base_url,
        "runSeed": args.run_seed,
        "corpusManifestSha256": manifest_sha256,
        "corpus": manifest,
        "summary": summary,
        "observations": observations,
        "nonclaims": [
            "This development run is not PC05 acceptance unless the image is frozen and V07 is also complete.",
            "Telegram provider delivery time is outside this host-loopback measurement.",
        ],
    }
    _write_receipt(args.output, receipt)
    print(json.dumps({"status": receipt["status"], "summary": summary}, separators=(",", ":")))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
