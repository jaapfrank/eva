"""Present terminal work failure honestly without manufacturing a successful result."""
from dataclasses import dataclass
from hashlib import sha256


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
    # Keep the full failure report in the work record and explicit status/result
    # route. Automatic chat return must not spill that diagnostic report.
    context = work.title[:160].strip()
    repair_notice = getattr(work, "repair_notice", None)
    if work.state == "ambiguous":
        text = ("Ik weet niet of de laatste handeling is gelukt. "
                "Ik voer die niet nog eens uit zolang dat onduidelijk is.")
    elif isinstance(repair_notice, str) and repair_notice:
        text = repair_notice
    elif (work.state == "failed" and error_code in {"budget_exceeded", "incomplete_work"}
          and isinstance(report, str) and report.strip() and len(report.strip()) <= 600
          and not report.startswith("input_budget_exceeded:")):
        text = "Het lukte me niet om dit af te maken. " + report.strip()
    elif error_code == "provider_unavailable":
        text = "De dienst die ik nodig had was niet bereikbaar."
    elif error_code == "timed_out":
        text = ("Het duurde te lang en de uitvoering is gestopt. "
                "Een begonnen handeling kan al uitgevoerd zijn.")
    elif error_code == "budget_exceeded":
        text = "Ik kreeg deze opdracht niet af binnen mijn limiet voor deze poging."
    else:
        text = "Ik liep vast bij het uitvoeren en heb dit niet afgemaakt."
    if context and not repair_notice:
        text = "Over " + context + ": " + text
    return WorkFailureNotice(work.spec.work_id, work.spec.sha256, work.state,
                             work.updated_at.isoformat(), text)
