"""Re-extract a Hindsight transfer archive without assistant-as-user pollution.

The source archive is read in memory. Exported facts and observations are never
imported: every document is retained again so Hindsight derives them only from
the corrected source text and the target embedding provider.
"""

from __future__ import annotations

import argparse
from io import BytesIO
import json
import time
from typing import Any
from urllib import error, request
from uuid import NAMESPACE_URL, uuid5
from zipfile import ZipFile


_USER_PREFIX = "Gebruiker (directe bron):"
_ASSISTANT_PREFIX = "Eva (gesprekscontext, geen gebruikersfeit):"


def _legacy_user_transcript(text: str) -> str:
    user_parts: list[str] = []
    active: list[str] | None = None
    for line in text.splitlines():
        if line.startswith("Jaap:"):
            active = [line.removeprefix("Jaap:").strip()]
            user_parts.append("")
        elif line.startswith("Eva:"):
            if active is not None:
                user_parts[-1] = "\n".join(active).strip()
            active = None
        elif active is not None:
            active.append(line)
    if active is not None:
        user_parts[-1] = "\n".join(active).strip()
    clean = [part for part in user_parts if part]
    if not clean:
        raise ValueError("legacy transcript contains no direct user turns")
    return f"{_USER_PREFIX} " + "\n\n".join(clean)


def trusted_source_text(text: str, trust: str) -> str:
    """Return only testimony that is valid for the document's trust class."""

    if trust != "direct_user":
        return text.strip()
    if text.startswith(_USER_PREFIX):
        return text.split(f"\n{_ASSISTANT_PREFIX}", 1)[0].strip()
    if text.startswith("Jaap:"):
        return _legacy_user_transcript(text)
    raise ValueError("direct_user document has an unknown source format")


def _json_request(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 30.0,
) -> dict[str, Any]:
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            decoded = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read(2048).decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} returned HTTP {exc.code}: {detail}") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError(f"{method} {url} returned a non-object JSON response")
    return decoded


def _source_documents(export_url: str) -> list[dict[str, Any]]:
    with request.urlopen(export_url, timeout=30) as response:
        archive = response.read(16 * 1024 * 1024 + 1)
    if len(archive) > 16 * 1024 * 1024:
        raise RuntimeError("Hindsight export exceeds 16 MiB")
    with ZipFile(BytesIO(archive)) as transfer:
        names = sorted(
            name
            for name in transfer.namelist()
            if name.startswith("documents/") and name.endswith(".json")
        )
        documents = [json.loads(transfer.read(name)) for name in names]
    if not documents or not all(isinstance(item, dict) for item in documents):
        raise RuntimeError("Hindsight export contains no valid documents")
    return documents


def _retain_item(document: dict[str, Any]) -> dict[str, Any]:
    params = document["retain_params"]
    metadata = params["metadata"]
    content = trusted_source_text(document["original_text"], metadata["trust"])
    return {
        "content": content,
        "timestamp": params["event_date"],
        "context": params.get("context", "Eva conversation and experience memory"),
        "document_id": document["id"],
        "update_mode": "replace",
        "metadata": metadata,
        "tags": document.get("tags", []),
        "observation_scopes": params.get("observation_scopes", []),
    }


def _wait_for_operation(base_url: str, bank_id: str, operation_id: str) -> None:
    deadline = time.monotonic() + 900
    path = f"{base_url}/v1/default/banks/{bank_id}/operations/{operation_id}"
    while time.monotonic() < deadline:
        status = _json_request("GET", path, timeout=30)
        state = status.get("status")
        if state == "completed":
            return
        if state in {"failed", "cancelled"}:
            raise RuntimeError(
                f"Hindsight migration operation {operation_id} {state}: "
                f"{status.get('error_message')}"
            )
        time.sleep(1)
    raise TimeoutError(f"Hindsight migration operation {operation_id} timed out")


def migrate(export_url: str, target_url: str, bank_id: str, batch_size: int) -> dict[str, int]:
    documents = _source_documents(export_url)
    items = [_retain_item(document) for document in documents]
    polluted = [
        item["document_id"]
        for item in items
        if item["metadata"]["trust"] == "direct_user"
        and _ASSISTANT_PREFIX in item["content"]
    ]
    if polluted:
        raise RuntimeError(f"assistant text survived cleansing: {polluted[:3]}")

    base_url = target_url.rstrip("/")
    for offset in range(0, len(items), batch_size):
        batch = items[offset : offset + batch_size]
        stable = "\n".join(item["document_id"] for item in batch)
        operation_id = str(
            uuid5(NAMESPACE_URL, f"{bank_id}:provenance-clean-v1:{stable}")
        )
        result = _json_request(
            "POST",
            f"{base_url}/v1/default/banks/{bank_id}/memories",
            {"items": batch, "async": True, "operation_id": operation_id},
        )
        accepted_id = result.get("operation_id")
        if result.get("success") is not True or not isinstance(accepted_id, str):
            raise RuntimeError("Hindsight did not accept the migration batch")
        _wait_for_operation(base_url, bank_id, accepted_id)

    return {
        "documents": len(items),
        "direct_user_documents": sum(
            item["metadata"]["trust"] == "direct_user" for item in items
        ),
        "authority_documents": sum(
            item["metadata"]["trust"] != "direct_user" for item in items
        ),
        "assistant_text_retained_as_direct_user": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-export-url", required=True)
    parser.add_argument("--target-url", required=True)
    parser.add_argument("--bank-id", required=True)
    parser.add_argument("--batch-size", type=int, default=8)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 32:
        raise SystemExit("--batch-size must be between 1 and 32")
    print(
        json.dumps(
            migrate(
                args.source_export_url,
                args.target_url,
                args.bank_id,
                args.batch_size,
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
