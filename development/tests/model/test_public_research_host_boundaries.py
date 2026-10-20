"""Public product domains must not be confused with private DNS suffixes."""
import pytest
from eva.adapters.model.research_tools import validate_public_projection
from eva.application.conversation import ConversationConflict


def projection(source):
    return {"question": "Describe the documented indicator semantics.",
            "title": "Public integration documentation", "options": [source],
            "public_review": "public_unlinked"}


@pytest.mark.parametrize("source", [
    "https://www.home-assistant.io/integrations/rpi_power/",
    "https://www.home-assistant.io./integrations/rpi_power/",
    "https://www.raspberrypi.com/documentation/computers/os.html",
    "https://service.local-tools.example/docs",
    "https://www.home.example/docs",
    "Home Assistant public integration documentation",
])
def test_complete_public_domains_are_admitted(source):
    assert validate_public_projection(projection(source))["host_checks"] == "passed"


@pytest.mark.parametrize("source", [
    "https://sensor.home/", "https://sensor.home./", "https://sensor.HOME/",
    "https://sensor.lan/docs", "https://sensor.internal/docs", "https://sensor.local/",
    "https://sensor%2ehome/", "sensor.home", "sensor.home.",
    "localhost", "https://localhost/", "https://localhost./",
    "https://127.0.0.1/", "https://[::1]/", "https://public.example/?token=x",
    "https://user:pass@public.example/", "http://public.example/",
])
def test_private_or_uncontrolled_inputs_remain_rejected(source):
    with pytest.raises(ConversationConflict):
        validate_public_projection(projection(source))
