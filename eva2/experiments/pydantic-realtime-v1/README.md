# DF03b — Pydantic AI Realtime-kandidaat

Deze map is de afzonderlijke dependencyproef voor voice V02. Hij gebruikt
Pydantic AI `2.34.0` met de upstream-extra `openai-realtime`; Eva's productlock
blijft onveranderd. De proef controleert alleen dat de exacte dependencyversie
de verwachte agent-/sessiegrens en `interrupt(played_ms=...)` exposeert.

Een groene proef is geen voice-call. Zonder een uitdrukkelijk geautoriseerde
provider, microfoon, speaker en meetcampagne zijn audio, transcripties,
onderbreking en latency allemaal `unmeasured`.

`check_candidate.py` draait binnen de geïsoleerde kandidaatvenv; hij verifieert
de geïnstalleerde packages en de publieke `RealtimeSession.interrupt(played_ms)`
grens zonder netwerkverbinding. `verify_receipt.py` controleert daarna hashes,
de exacte dependencydelta en de Git-gebonden bron van de receipt.
