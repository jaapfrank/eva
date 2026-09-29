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


def test_rate_limit_stream_exposes_only_a_fixed_code():
    import asyncio
    import json
    from eva.adapters.model.contracts import ConversationModelRateLimited
    def fail(*args):
        raise ConversationModelRateLimited("private provider response must stay hidden")
    runtime = SimpleNamespace(respond=fail)
    async def read():
        response = conversation_stream_response(runtime, None, ())
        return b"".join([chunk async for chunk in response.body_iterator])
    assert json.loads(asyncio.run(read())) == {
        "contract": "EvaTextStreamError/v1", "code": "provider_rate_limited"}
