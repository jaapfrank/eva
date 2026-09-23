"""Read the existing owner browser's availability for private background work."""
from __future__ import annotations

import json
import os

from eva.adapters.connectors.browser_session import BrowserSessionClient, BrowserSessionError
from eva.adapters.credentials.runtime_files import read_runtime_secret_file


def configured_browser_client():
    origin = os.environ.get("EVA_BROWSER_CONTROL_URL", "").strip()
    path = os.environ.get("EVA_BROWSER_CONTROL_TOKEN_FILE", "").strip()
    if not origin and not path:
        return None
    if not origin or not path:
        raise ValueError("background browser availability requires complete host configuration")
    return BrowserSessionClient(origin, read_runtime_secret_file(
        path, setting_name="EVA_BROWSER_CONTROL_TOKEN_FILE"))


class BackgroundBrowserContext:
    """Availability is context, not an authorization or a browser-action receipt."""
    def __init__(self, client):
        self.client = client

    def __call__(self):
        if self.client is None:
            return ()
        try:
            raw = self.client.status_snapshot(timeout=1)
            state = raw.get("state")
            keys = ("owner_control", "eva_control", "browser_ready", "mcp_ready")
            if (not isinstance(state, str) or state not in {"stopped", "starting", "eva", "taking_over", "owner",
                              "resuming", "stopping", "recovery_required"}
                    or any(type(raw.get(key)) is not bool for key in keys)):
                raise BrowserSessionError("browser availability shape differs")
            snapshot = {"state": state, **{key: raw[key] for key in keys}}
        except BrowserSessionError:
            return ("De status van de beheerde browser is nu niet bereikbaar. Dit bewijst "
                    "niet dat een eerdere browseractie mislukte. Gebruik zo nodig andere "
                    "beschikbare tools; meld de ontbrekende browserverbinding als die nodig is.",)
        return ("Actuele beschikbaarheid van de beheerde browser (geen inhoud van tabbladen): "
                + json.dumps(snapshot, ensure_ascii=False) + ". "
                "Gebruik bij browserwerk de bestaande MCP-connector en zijn ontdekte tools. "
                "Als owner_control waar is, heeft de eigenaar de bediening; probeer die "
                "niet over te nemen of te omzeilen. Leg uit dat teruggeven kan in het "
                "browserpaneel van Eva’s dashboard. Bij stopped kan de eigenaar daar starten; "
                "bij recovery_required kan de eigenaar daar Herstel en neem over kiezen, "
                "de pagina controleren en teruggeven. Dat hervat geen gestopte taak; "
                "gebruik daarvoor de bestaande taakbediening na een expliciet verzoek. Ga waar mogelijk "
                "verder met andere stappen. Bewaar een browserblokkade en de resterende stap "
                "in het taakplan als de browser nodig is. Na teruggeven of verbindingsherstel "
                "moet de browser vers worden waargenomen voordat een actie wordt gekozen; "
                "gebruik geen oude elementverwijzingen. Deze status is geen toestemming "
                "of bewijs dat een browseropdracht al uitgevoerd is.",)
