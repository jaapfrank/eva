"""Read-only channel observations composed from existing installer probes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import os
import json

from contextlib import closing
from http.client import HTTPConnection
from urllib.parse import urlsplit
from threading import Lock
from time import monotonic

from eva.adapters.credentials.runtime_files import read_runtime_secret_file
from eva.adapters.connectors.installer_telegram_probe import TelegramInstallerReadOnlyProbe
from eva.adapters.connectors.installer_home_assistant_probe import HomeAssistantInstallerReadOnlyProbe


def configured_component_observer():
    channels = {
        "telegram": (os.environ.get("EVA_TELEGRAM_BOT_TOKEN_FILE", "").strip(),
                     TelegramInstallerReadOnlyProbe,
                     "Telegram-API en bottoegang gecontroleerd; berichtontvangst en aflevering niet getest."),
        "home_assistant": (os.environ.get("EVA_HOME_ASSISTANT_TOKEN_FILE", "").strip(),
                           lambda: HomeAssistantInstallerReadOnlyProbe(
                               base_url=os.environ.get("EVA_HOME_ASSISTANT_URL", "").strip()),
                           "Home Assistant-API en toegang gecontroleerd; huisbediening en speakergeluid niet getest."),
    }
    return ComponentObserver(channels, worker_url=os.environ.get("EVA_DELEGATED_WORK_HEALTH_URL", "").strip() or None)


class ComponentObserver:
    """Bound the frequency of owner-requested probes; preserve measurement times."""

    def __init__(self, channels, *, clock=monotonic, worker_url=None):
        self._channels = channels
        self._worker_url = worker_url
        self._clock = clock
        self._lock = Lock()
        self._expires = 0.0
        self._snapshot = {}

    @staticmethod
    def _observe(identifier, configuration):
        path, factory, detail = configuration
        if not path:
            return identifier, {"state": "not_configured", "observed_at": None,
                                "detail": "Geen toegang voor deze verbindingscontrole ingesteld."}
        try:
            setting = {"telegram": "EVA_TELEGRAM_BOT_TOKEN_FILE",
                       "home_assistant": "EVA_HOME_ASSISTANT_TOKEN_FILE"}[identifier]
            token = read_runtime_secret_file(path, setting_name=setting).strip().encode()
            factory().read_only_probe(secret=memoryview(token), timeout_seconds=2.0)
            state = "ready"
        except Exception:
            # Do not return provider messages, endpoint URLs or credential bytes.
            state = "attention"
            detail = "Verbindingscontrole niet geslaagd; bereikbaarheid of toegang controleren."
        return identifier, {"state": state, "observed_at": datetime.now(UTC).isoformat(), "detail": detail}

    @staticmethod
    def _observe_worker(url):
        detail = "Werkerproces reageert; ingestelde werklussen zijn actief. De modeltoegang, voortgang en succesvolle taakuitvoering zijn hiermee niet getest."
        try:
            target = urlsplit(url)
            if (target.scheme != "http" or not target.hostname or target.username or target.password
                    or target.query or target.fragment or target.path != "/health"):
                raise ValueError("invalid internal worker observation URL")
            with closing(HTTPConnection(target.hostname, target.port or 80, timeout=2.0)) as connection:
                connection.request("GET", "/health")
                response = connection.getresponse()
                if response.status != 200:
                    raise ValueError("worker is not ready")
                body = response.read(1025)
                if len(body) > 1024:
                    raise ValueError("worker observation exceeds bound")
            value = json.loads(body)
            if (not isinstance(value, dict) or value.get("contract") != "EvaWorkerLiveness/v1"
                    or value.get("state") != "ready" or type(value.get("active_lanes")) is not int
                    or not 1 <= value["active_lanes"] <= 2):
                raise ValueError("worker observation is not ready")
            state = "ready"
        except Exception:
            state = "attention"
            detail = "Werker reageert niet met een geldige actuele status; nieuwe taakuitvoering is niet bevestigd."
        return "worker", {"state": state, "observed_at": datetime.now(UTC).isoformat(), "detail": detail}

    def __call__(self):
        with self._lock:
            if self._clock() >= self._expires:
                with ThreadPoolExecutor(max_workers=3, thread_name_prefix="eva-component-check") as pool:
                    futures = [pool.submit(self._observe, key, value) for key, value in self._channels.items()]
                    if self._worker_url is not None:
                        futures.append(pool.submit(self._observe_worker, self._worker_url))
                    self._snapshot = dict(future.result() for future in futures)
                self._expires = self._clock() + 30.0
            return {key: dict(value) for key, value in self._snapshot.items()}
