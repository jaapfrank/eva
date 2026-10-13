# Independent review: real voice transport recovery

GO for test-only evidence. Coordinator reviewed the new harness and independently executed it against copied exact live media assets, a disposable LiveKit1.13.6 SFU and Chromium from pinned Playwright image18c0a9c93400. Production was not restarted or modified. Harness SHA256:301a95454155e15b14b1661de0084e570c70ee51d48af129d4ef083c0d97465d.

The real SDK connects and publishes a native synthetic audio track. After the ready barrier, the coordinator kills only the disposable SFU. The real reconnect/disconnect path ends that track and disables conversation input. A fresh SFU container is confirmed ready; during five seconds without user actions, getUserMedia and session counts remain one and the track remains ended. Explicit Begin gesprek creates the second live track/publication. Ending stops the remaining track. No Room event, online flag or MediaStream is fabricated by JavaScript.

The session HTTP issuer is a test fixture using isolated credentials. The browser capture wrapper observes native tracks and does not replace them. The HTML uses only the exact localAccess transformation made by the real media server; no dashboardtoken is needed.

Attempt1 failed before browser launch because the image lacks the default headless_shell. The harness now selects the preflight-verified full Chromium executable. Attempt2 could not click Start: a network-none namespace reports navigator.onLine=false and the product correctly disables Start. Separate real-browser probes confirmed false on network-none, true on a dedicated internal network. Attempt3 passed with that isolated internal interface and no Internet egress. Earlier attempt logs are retained; they are not product regression failures or passing trials.

Result SHA256:91e66e816ce647e297538fb32a495c45d0819531de6049924edb9b9a1e97135f. Exact commands, container provenance, asset hashes and all receipts are retained in /var/lib/eva2/livekit-recovery-20260922. Disposable containers and network were removed after success.

This proves a real SFU-loss lifecycle boundary of the installed webclient, not physical microphone/audio quality, Voice PE firmware, STT/TTS, memory/task continuity, whole HTTP core/worker restart, PostgreSQL18.6 or host reboot. The bounded observation is five seconds, not an unlimited temporal guarantee. Existing other integration evidence remains separate; no full-release or owner-acceptance claim. Zero modelcalls and external messages; no product patch or deployment needed.
