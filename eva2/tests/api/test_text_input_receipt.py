"""Input acknowledgement is available before the response worker is entered."""
from types import SimpleNamespace
from eva.adapters.api.dashboard_conversation import dashboard_stream_response, conversation_stream_response
from eva.application.conversation_receipt import RECEIVED_INPUT_TEXT


def test_both_text_transports_share_receipt_before_model_work():
    def unexpected(*args):
        raise AssertionError("No model work should run to produce HTTP headers")
    runtime = SimpleNamespace(dashboard_turn=unexpected, respond=unexpected)
    for response in (dashboard_stream_response(runtime, "Hallo"),
                     conversation_stream_response(runtime, None, ())):
        assert response.headers["X-Eva-Input-Receipt"] == RECEIVED_INPUT_TEXT
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["X-Accel-Buffering"] == "no"
