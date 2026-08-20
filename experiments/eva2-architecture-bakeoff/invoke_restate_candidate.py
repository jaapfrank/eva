from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from common import EpisodeFailureOutcome, EpisodeOutcome, canonical_json, load_contract
from restate_runtime import checked_fixture_url, checked_state_dir, load_runtime


WORKFLOW_NAME = "EvaNoorWorkflow"


def post(
    url: str,
    body: dict[str, Any],
    *,
    timeout: float,
    accepted_statuses: set[int],
) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url,
        data=canonical_json(body).encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        payload = error.read()
        if error.code in accepted_statuses:
            return error.code, payload
        detail = payload.decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code} voor {url}: {detail}") from error


def invoke(
    state_dir: Path,
    fixture_url: str,
    contract_path: Path,
) -> EpisodeOutcome | EpisodeFailureOutcome:
    runtime = load_runtime(state_dir, fixture_url)
    contract = load_contract(contract_path)
    ingress = f"http://127.0.0.1:{runtime['ingress_port']}"
    key = urllib.parse.quote(contract.episode_id, safe="")

    status, _ = post(
        f"{ingress}/restate/send/{WORKFLOW_NAME}/{key}/run",
        contract.model_dump(mode="json"),
        timeout=10,
        accepted_statuses={409},
    )
    if status not in {202, 409}:
        raise RuntimeError(f"onverwachte workflow-submitstatus: {status}")

    _, payload = post(
        f"{ingress}/restate/attach",
        {
            "target": "workflow",
            "workflowName": WORKFLOW_NAME,
            "workflowKey": contract.episode_id,
        },
        timeout=(contract.limits.max_wall_clock_ms / 1000) + 30,
        accepted_statuses=set(),
    )
    decoded = json.loads(payload)
    if decoded.get("contract") == "EvaArchitectureOutcome/v1":
        return EpisodeOutcome.model_validate(decoded)
    if decoded.get("contract") == "EvaArchitectureFailureOutcome/v1":
        return EpisodeFailureOutcome.model_validate(decoded)
    raise RuntimeError("Restate gaf geen bekend episode-outcomecontract")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--contract", required=True)
    args = parser.parse_args()

    outcome = invoke(
        checked_state_dir(args.state_dir),
        checked_fixture_url(args.fixture),
        Path(args.contract).resolve(),
    )
    print(canonical_json(outcome.model_dump(mode="json")), flush=True)


if __name__ == "__main__":
    main()
