"""Lossless presentation of known saved work output."""

import json

def readable_work_result(output: str) -> str:
    """Render the known tool-free output shape without hiding unknown fields."""
    try:
        value = json.loads(output)
    except (ValueError, RecursionError):
        return output
    if (not isinstance(value, dict) or set(value) != {"summary", "findings"}
            or not isinstance(value["summary"], str) or not value["summary"].strip()
            or not isinstance(value["findings"], list)
            or any(not isinstance(item, str) or not item.strip() for item in value["findings"])):
        return output
    findings = "\n".join(f"- {item}" for item in value["findings"])
    return value["summary"] + ("\n\n" + findings if findings else "")



def work_failure_description(error_code: str | None, failure_report: str | None = None) -> str | None:
    """Explain known task failures without inferring side effects or exposing errors."""
    description = {
        "incomplete_work": "Deze opdracht is nog niet af. De bewaarde voortgang toont de open stappen en eventuele blokkade. Hervatten kan verdergaan met die stand; er is nog geen afgerond resultaat.",
        "provider_unavailable": "Een benodigde dienst was niet bereikbaar. Er is geen afgerond resultaat bevestigd. Controleer eerdere bestanden of acties voordat je hervat.",
        "internal_error": "De uitvoering is technisch vastgelopen. Er is geen afgerond resultaat bevestigd. Controleer eerdere bestanden of acties voordat je hervat.",
        "budget_exceeded": "Deze poging heeft het ingestelde model- of toolbudget bereikt. De opdracht is niet afgerond; eerdere acties en voortgang blijven bewaard. Controleer die voordat je hervat.",
        "timed_out": "De uitvoering heeft de tijdslimiet bereikt. Eerdere acties kunnen al uitgevoerd zijn; controleer die voordat je hervat.",
    }.get(error_code)

    if error_code == "budget_exceeded" and failure_report is not None:
        return (description + "\n\nBewaard antwoord — niet als afgerond resultaat bevestigd:\n"
                + failure_report)
    return description
