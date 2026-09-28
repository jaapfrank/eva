"""Owner-visible component observations without claiming end-to-end health."""
from datetime import UTC, datetime

from fastapi import Request
from fastapi.responses import JSONResponse


def install_dashboard_health(app, *, access, runtime, web_voice_configured, native_voice_configured):
    @app.get("/internal/v1/dashboard/components")
    def components(request: Request):
        access.require(request)
        checked_at = datetime.now(UTC).isoformat()
        try:
            health = runtime.health()
        except Exception:
            # Never expose provider exceptions, URLs or credentials to the page.
            health = None

        def observed(identifier, label, value, detail):
            state = "ready" if value == "ready" else "unknown" if value == "unknown" else "attention"
            return {"id": identifier, "label": label, "state": state,
                    "observed_at": checked_at if state != "unknown" else None, "detail": detail}

        def unknown(identifier, label, detail, configured=None):
            return {"id": identifier, "label": label,
                    "state": "not_configured" if configured is False else "unknown",
                    "observed_at": None, "detail": detail}

        rows = [
            observed("database", "Taak- en gegevensopslag", getattr(health, "database", "unknown"),
                     "Verbindingscontrole met de database; geen controle van alle opgeslagen gegevens."),
            observed("memory", "Geheugendienst", getattr(health, "memory_projection", "unknown"),
                     "Bereikbaarheid van de geheugendienst; geen beoordeling van herinneringen."),
            observed("model_credentials", "Modeltoegang", ("unknown" if getattr(health, "model_provider", "unknown") == "ready" else getattr(health, "model_provider", "unknown")),
                     "De kern meldt een toegangsprobleem als zij dit vaststelt. Zonder probleemmelding blijft daadwerkelijke modelbereikbaarheid onbekend; er is geen modelvraag gestuurd."),
            observed("website", "Eigenaarwebsite", "ready", "Deze beveiligde statusaanvraag is ontvangen en beantwoord."),
            unknown("worker", "Achtergrondwerker", "Geen actuele werkerhartslag aangesloten op dit overzicht. Taakstatus staat bij Mijn opdrachten."),
            unknown("telegram", "Telegram", "Geen actuele ontvangst- of aflevercontrole in dit overzicht. Een ingestelde koppeling bewijst geen bereikbaarheid."),
            unknown("home_assistant", "Home Assistant", "Geen actuele API-controle in dit overzicht. Huisbediening is hiermee niet getest."),
            unknown("web_voice", "Spraak op de website", "Spraakroute ingesteld; microfoon, transcriptie en geluid niet beproefd." if web_voice_configured else "Deze webspraakroute is niet ingesteld.", web_voice_configured),
            unknown("voice_pe", "Voice PE-speakers", "Native spraakroute ingesteld; beide fysieke speakers en hun wakeword zijn niet gecontroleerd." if native_voice_configured else "Native spraakroute niet ingesteld; een eventuele andere HA-spraakroute is niet gecontroleerd."),
        ]
        return JSONResponse({"contract": "EvaComponentObservations/v1", "read_only": True,
                             "checked_at": checked_at, "components": rows},
                            headers={"Cache-Control": "no-store"})
