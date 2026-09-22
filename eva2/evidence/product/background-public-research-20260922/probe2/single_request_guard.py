import httpx2
ENDPOINT = "https://chatgpt.com/backend-api/codex/responses"
class SingleRequestGuard:
    def __init__(self, delegate):
        self.delegate = delegate
        self.counts = {"sent": 0, "blocked": 0}
    async def handle(self, transport, request):
        if str(request.url) != ENDPOINT or self.counts["sent"] >= 1:
            self.counts["blocked"] += 1
            raise RuntimeError("single outgoing provider request guard")
        self.counts["sent"] += 1
        return await self.delegate(transport, request)
