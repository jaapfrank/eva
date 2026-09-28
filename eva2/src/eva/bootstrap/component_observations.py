"""Read-only channel observations composed from existing installer probes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import os
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
    return ComponentObserver(channels)


class ComponentObserver:
    """Bound the frequency of owner-requested probes; preserve measurement times."""

    def __init__(self, channels, *, clock=monotonic):
        self._channels = channels
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

    def __call__(self):
        with self._lock:
            if self._clock() >= self._expires:
                with ThreadPoolExecutor(max_workers=2, thread_name_prefix="eva-component-check") as pool:
                    futures = [pool.submit(self._observe, key, value) for key, value in self._channels.items()]
                    self._snapshot = dict(future.result() for future in futures)
                self._expires = self._clock() + 30.0
            return {key: dict(value) for key, value in self._snapshot.items()}
