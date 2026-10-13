from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any, Callable, TypeVar

from pydantic import BaseModel

from common import (
    EffectReceipt,
    EpisodeContract,
    EpisodeOutcome,
    EpisodePlan,
    FixtureClient,
    ModelPlanResult,
    canonical_json,
    effect_intent,
    execute_or_reconcile,
    load_contract,
    maybe_crash,
    render_reply,
    run_deterministic_model,
    sha256,
)


T = TypeVar("T")


class SqlBaseline:
    def __init__(self, path: Path, contract: EpisodeContract):
        self.path = path
        self.contract = contract
        self.owner = f"sql-baseline:{uuid.uuid4()}"
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS episodes (
                  episode_id TEXT PRIMARY KEY,
                  contract_sha256 TEXT NOT NULL,
                  status TEXT NOT NULL CHECK (status IN ('running','closed')),
                  outcome_json TEXT,
                  outcome_sha256 TEXT
                );
                CREATE TABLE IF NOT EXISTS steps (
                  episode_id TEXT NOT NULL REFERENCES episodes(episode_id),
                  ordinal INTEGER NOT NULL,
                  step_name TEXT NOT NULL,
                  result_json TEXT NOT NULL,
                  result_sha256 TEXT NOT NULL,
                  PRIMARY KEY (episode_id, step_name),
                  UNIQUE (episode_id, ordinal)
                );
                """
            )
            contract_sha = sha256(self.contract.model_dump(mode="json"))
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT contract_sha256 FROM episodes WHERE episode_id=?",
                (self.contract.episode_id,),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO episodes VALUES(?,?,?,NULL,NULL)",
                    (self.contract.episode_id, contract_sha, "running"),
                )
            elif row["contract_sha256"] != contract_sha:
                connection.execute("ROLLBACK")
                raise RuntimeError("episode-id is al aan andere contractbytes gebonden")
            connection.execute("COMMIT")

    def existing_outcome(self) -> EpisodeOutcome | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT status,outcome_json,outcome_sha256 FROM episodes WHERE episode_id=?",
                (self.contract.episode_id,),
            ).fetchone()
        if row is None or row["status"] != "closed":
            return None
        if sha256(json.loads(row["outcome_json"])) != row["outcome_sha256"]:
            raise RuntimeError("duurzame outcomehash wijkt af")
        return EpisodeOutcome.model_validate_json(row["outcome_json"])

    def step(
        self,
        ordinal: int,
        name: str,
        model: type[T],
        execute: Callable[[], T],
    ) -> T:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT result_json,result_sha256 FROM steps "
                "WHERE episode_id=? AND step_name=?",
                (self.contract.episode_id, name),
            ).fetchone()
        if row is not None:
            parsed = json.loads(row["result_json"])
            if sha256(parsed) != row["result_sha256"]:
                raise RuntimeError(f"duurzame stephash wijkt af: {name}")
            if isinstance(model, type) and issubclass(model, BaseModel):
                return model.model_validate(parsed)  # type: ignore[return-value]
            return parsed  # type: ignore[return-value]

        result = execute()
        if isinstance(result, BaseModel):
            serializable: Any = result.model_dump(mode="json")
        else:
            serializable = result
        result_json = canonical_json(serializable)
        result_sha = sha256(serializable)
        with closing(self.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT result_json,result_sha256 FROM steps "
                "WHERE episode_id=? AND step_name=?",
                (self.contract.episode_id, name),
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO steps VALUES(?,?,?,?,?)",
                    (self.contract.episode_id, ordinal, name, result_json, result_sha),
                )
            elif existing["result_sha256"] != result_sha:
                connection.execute("ROLLBACK")
                raise RuntimeError(f"concurrerend stepresultaat wijkt af: {name}")
            connection.execute("COMMIT")
        maybe_crash(f"{name}:after_local_receipt")
        return result

    def close(self, outcome: EpisodeOutcome) -> EpisodeOutcome:
        body = outcome.model_dump(mode="json")
        outcome_json = canonical_json(body)
        outcome_sha = sha256(body)
        with closing(self.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status,outcome_sha256 FROM episodes WHERE episode_id=?",
                (self.contract.episode_id,),
            ).fetchone()
            if row["status"] == "closed" and row["outcome_sha256"] != outcome_sha:
                connection.execute("ROLLBACK")
                raise RuntimeError("gesloten episode heeft andere outcome")
            connection.execute(
                "UPDATE episodes SET status='closed',outcome_json=?,outcome_sha256=? "
                "WHERE episode_id=?",
                (outcome_json, outcome_sha, self.contract.episode_id),
            )
            connection.execute("COMMIT")
        return outcome


def run_episode(store: SqlBaseline, fixture: FixtureClient) -> EpisodeOutcome:
    existing = store.existing_outcome()
    if existing is not None:
        return existing

    maybe_crash("model.plan:before")
    model_result = store.step(
        1, "model.plan", ModelPlanResult,
        lambda: run_deterministic_model(store.contract, fixture),
    )
    if model_result.requests != 1:
        raise RuntimeError("deterministische modellane deed niet exact één request")
    plan: EpisodePlan = model_result.plan
    maybe_crash("plan:after_persist")

    mail = store.step(2, "mail.read", dict, fixture.read_latest_mail)
    customer = store.step(3, "erp.read", dict, fixture.read_customer)
    freebusy = store.step(4, "calendar.freebusy", dict, fixture.read_availability)
    if mail["mail_id"] != store.contract.expected.mail_id:
        raise RuntimeError("verkeerde mail gelezen")
    if customer["customer_id"] != store.contract.expected.customer_id:
        raise RuntimeError("verkeerde ERP-klant gelezen")
    if not any(slot["start"] == plan.meeting_start for slot in freebusy["available"]):
        raise RuntimeError("gekozen tijd is niet extern beschikbaar")

    calendar_intent = effect_intent(store.contract, "calendar.create", {
        "customer_id": customer["customer_id"],
        "start": plan.meeting_start,
        "end": plan.meeting_end,
    })
    calendar = store.step(
        5, "calendar.create", EffectReceipt,
        lambda: execute_or_reconcile(fixture, calendar_intent),
    )
    document_intent = effect_intent(store.contract, "document.create", {
        "calendar_resource_id": calendar.resource_id,
        "customer_id": customer["customer_id"],
        "title": plan.document_title,
        "text": (
            f"Bevestiging voor {customer['name']}: {plan.meeting_start} tot {plan.meeting_end}."
        ),
    })
    document = store.step(
        6, "document.create", EffectReceipt,
        lambda: execute_or_reconcile(fixture, document_intent),
    )
    delivery_intent = effect_intent(store.contract, "delivery.send", {
        "calendar_resource_id": calendar.resource_id,
        "document_resource_id": document.resource_id,
        "recipient": customer["email"],
        "text": (
            f"De afspraak staat op {plan.meeting_start}; bevestiging {document.resource_id}."
        ),
    })
    delivery = store.step(
        7, "delivery.send", EffectReceipt,
        lambda: execute_or_reconcile(fixture, delivery_intent),
    )
    receipts = (calendar, document, delivery)
    outcome = EpisodeOutcome(
        episode_id=store.contract.episode_id,
        mail_id=mail["mail_id"],
        mail_sha256=mail["content_sha256"],
        customer_id=customer["customer_id"],
        receipts=receipts,
        reply=render_reply(store.contract, customer, receipts),
    )
    maybe_crash("episode:before_close")
    return store.close(outcome)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--contract", required=True)
    args = parser.parse_args()
    contract = load_contract(args.contract)
    outcome = run_episode(
        SqlBaseline(Path(args.db).resolve(), contract), FixtureClient(args.fixture),
    )
    print(canonical_json(outcome.model_dump(mode="json")), flush=True)


if __name__ == "__main__":
    main()
