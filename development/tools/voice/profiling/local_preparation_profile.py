import asyncio,json,time
from eva.adapters.model.pydantic_conversation import _input_tokenizer
from eva.adapters.model.openai_codex_provider import OpenAICodexProvider,OpenAICodexProviderSettings
async def main():
 for attempt in range(2):
  t=time.monotonic();_input_tokenizer();tokenizer=time.monotonic()-t
  t=time.monotonic()
  async with OpenAICodexProvider(OpenAICodexProviderSettings(endpoint="https://chatgpt.com/backend-api/codex/responses",model_name="gpt-6-sol",reasoning_effort="none"),credential_loader=lambda:("offline-dummy","offline-dummy")) as session:
   pass
  print(json.dumps({"attempt":attempt+1,"tokenizer_ms":round(tokenizer*1000),"provider_lifecycle_ms":round((time.monotonic()-t)*1000),"model_requests":0}))
asyncio.run(main())
