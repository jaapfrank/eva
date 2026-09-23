"""Keep HA STT/TTS and use a cancellable, device-bound Eva2 request."""
import asyncio

from homeassistant.components import conversation
from homeassistant.helpers import entity_registry, intent

from . import DOMAIN
from .client import EvaVoiceUnavailable


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([EvaConversation(entry, hass.data[DOMAIN][entry.entry_id])])


class EvaConversation(conversation.ConversationEntity):
    _attr_name = "Eva2"
    _attr_has_entity_name = True

    def __init__(self, entry, client):
        self._attr_unique_id = entry.entry_id
        self._client = client

    @property
    def supported_languages(self):
        return "*"

    async def _async_handle_message(self, user_input, chat_log):
        satellite_id = user_input.satellite_id
        registry = entity_registry.async_get(self.hass)
        satellite = registry.async_get(satellite_id) if satellite_id else None
        response = intent.IntentResponse(language=user_input.language or self.hass.config.language)
        if satellite is None or satellite.device_id is None or satellite.disabled_by is not None:
            response.async_set_speech("Deze speaker is niet aan Eva gekoppeld.")
            return conversation.ConversationResult(
                response=response, conversation_id=chat_log.conversation_id,
            )
        try:
            queue = asyncio.Queue()
            async def delta(text):
                await queue.put(text)
            worker = asyncio.create_task(self._client.respond(
                satellite_id=satellite_id, device_id=satellite.device_id,
                conversation_id=chat_log.conversation_id, text=user_input.text,
                language=user_input.language or self.hass.config.language,
                on_delta=delta,
            ))
            worker.add_done_callback(lambda _: queue.put_nowait(None))
            async def content():
                yield {"role": "assistant"}
                while True:
                    text = await queue.get()
                    if text is None:
                        break
                    yield {"content": text}
                # Propagate a failed final binding instead of saving a partial answer.
                worker.result()
            try:
                async for _ in chat_log.async_add_delta_content_stream(self.entity_id, content()):
                    pass
                answer = worker.result()
            finally:
                if not worker.done():
                    worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
        except EvaVoiceUnavailable:
            response.async_set_speech("Ik kan Eva momenteel niet bereiken.")
            return conversation.ConversationResult(
                response=response, conversation_id=chat_log.conversation_id,
            )
        # CancelledError deliberately propagates to the HA pipeline: an
        # interrupted request must never become speech or an error utterance.
        response.async_set_speech(answer.text)
        return conversation.ConversationResult(
            response=response, conversation_id=chat_log.conversation_id,
            continue_conversation=answer.continue_conversation,
        )
