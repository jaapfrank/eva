"""Present terminal work failure honestly without manufacturing a successful result."""
from dataclasses import dataclass
from hashlib import sha256

from eva.application.work_result_presentation import work_failure_description

FAILURE_STATES = frozenset({"failed", "needs_help", "ambiguous"})
DELIVERY_STATES = frozenset({"succeeded", "verified"}) | FAILURE_STATES


@dataclass(frozen=True)
class WorkFailureNotice:
    work_id: str
    spec_sha256: str
    state: str
    completed_at: str
    output: str

    @property
    def output_sha256(self):
        return sha256(self.output.encode()).hexdigest()

    def to_payload(self):
        return {"contract": "EvaWorkFailureNotice/v1", "work_id": self.work_id,
                "spec_sha256": self.spec_sha256, "state": self.state,
                "completed_at": self.completed_at, "output": self.output,
                "output_sha256": self.output_sha256}


def delivery_outcome(work):
    """Callers retain all existing audience, retention and destination checks."""
    if work.stop_requested_at is not None:
        return None
    if work.state in {"succeeded", "verified"}:
        return work.result
    if work.state not in FAILURE_STATES:
        return None
    report = getattr(work, "failure_report", None)
    error_code = getattr(work, "error_code", None)
    if work.state == "failed" and error_code in {"budget_exceeded", "incomplete_work"}:
        text = work_failure_description(error_code, report)
    elif work.state == "ambiguous":
        text = ("Ik kan niet bevestigen hoe je opdracht is afgelopen. Een begonnen "
                "handeling kan al uitgevoerd zijn. Ik herhaal die niet automatisch. "
                "Vraag me naar de taakstatus voordat we verdergaan.")
    elif work.state == "needs_help":
        text = ("Je opdracht is vastgelopen en heeft aandacht nodig. Er is geen "
                "bevestigd eindresultaat. Vraag me waar de taak is gebleven; "
                "daarna kunnen we gericht verdergaan.")
    else:
        text = ("Het is me niet gelukt je opdracht af te ronden. Er is geen bevestigd "
                "eindresultaat. De taakstatus is bewaard. Vraag me wat er misging "
                "voordat we besluiten of hervatten zinvol is.")
    text = "Over je opdracht " + work.title[:240] + ": " + text
    return WorkFailureNotice(work.spec.work_id, work.spec.sha256, work.state,
                             work.updated_at.isoformat(), text)
