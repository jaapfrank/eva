# UX-002: automatically refresh owner work

Status: implemented and fixture-tested; independent review, integration, installation,
live verification and owner acceptance remain with the coordinator.

## Requirement and bounded implementation

The installed owner work view only loaded on mount or manual refresh. A running
selected task could finish while its displayed state and result stayed unchanged.
The existing owner work list/get/stop/resume contracts already provide the needed
information. No backend, authorization, dependency or runtime changes are needed.

Baseline: `4701dd8ffd697cbd384c41ed1f2e56775a44fc03`, confirmed against
`git ls-remote origin refs/heads/main` on 2026-09-22. Origin:
https://github.com/jaapfrank/eva.git. The app-created Windows checkout was left
untouched because its UNC gitdir is not directly usable by WSL Git and WSL sees
checkout-wide line-ending differences. A fresh WSL Git worktree from the verified
commit was created at `/home/administrator/project/eva2-owner-work-auto-refresh`,
branch `codex/owner-work-auto-refresh`; no historical source copies were used.
Source, tests, lockfiles and current owner instructions were present.

Only `src/owner-work.jsx`, `verify-owner-work.cjs` in the existing dashboard
experiment and this evidence file are changed. Existing React effect/ref state,
Fetch AbortController and the existing browser proof are reused. No extra agent.

The mounted view schedules a list read five seconds after the preceding operation
settles, while the document is visible. Visibility restoration reads immediately.
A single serial queue prevents overlapping requests. Foreground selection changes
supersede older responses; queued selection clicks collapse to the newest choice.
A list refresh preserves the selected task. Detail is fetched when its state,
generation, stop flag or result availability changes. Unchanged completed results
are not fetched again by polling. Stop/resume still read stored state back.
All request/contract failures clear private list, detail and notices, discard pending
selection and halt polling. Only explicit refresh reopens this fail-closed path.
Unmount removes the timer/listener and aborts the active request.

## Reproduction and evidence

From `eva2/experiments/react-admin-dashboard-v1` in the WSL worktree:

```sh
npm ci --ignore-scripts
npm run build
EVA_DASHBOARD_PLAYWRIGHT_PACKAGE="/home/administrator/project/automation skill/AegisPT/ui-v2/node_modules/playwright" \
EVA_DASHBOARD_CHROMIUM=/home/administrator/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome \
node verify-owner-work.cjs
git diff --check
```

Build passed (Vite-reported 572 ms). Final browser proof passed, 27 work API fixture
requests, maximum concurrent requests 1. It retains the existing literal-result,
stop-before-start, running-stop-request and resume-new-generation checks and adds:

- Selected admitted task becomes running then succeeds with its stored output
  visible automatically, without pressing refresh.
- Manual refresh preserves the opened task; unchanged polling does not fetch detail.
- A deliberately held old selection response cannot overwrite a newer selection;
  multiple timer periods while blocked do not start overlapping requests.
- Simulated document visibility pauses polling and restoration immediately reads.
- A polling 403 immediately clears private content and stops future polling.
- A superseded held response returning 403 clears the queued newer selection too.
- Navigating away unmounts the component, aborts an active held request and stops polling.
- No browser exceptions, external requests or HTML execution from result text.

The final build/browser/check shell invocation took 3.89 seconds (browser plus
Git checks; build was performed separately). Worktree creation was 12:27:35 UTC;
implementation and final successful test were completed by 12:31:58 UTC (about
4 minutes 23 seconds elapsed, excluding initial repository inspection and later
evidence/handoff). Active compute time, token totals and credit cost are unknown.
No real Eva worker/model call was made by these tests. The original 1–2 hour
estimate was an estimate, not measured usage.

`npm ci` reported seven moderate dependency advisories and Vite its existing
large-chunk warning. No dependency upgrade or broad cleanup was attempted.
Generated experiment `dist` is ignored; no production assets are committed.

## Try after coordinator installation

Open http://localhost:44711/internal/v1/dashboard using existing owner access.
Open a running task under **Mijn hulpopdrachten**, keep the tab visible and let
Eva finish it. Its status and saved result should appear without **Werk vernieuwen**.
Switch between tasks during work: the last selected task should stay open.
Hide and return to the tab: refreshing should resume automatically.

## Limits and handoff

Five seconds is a polling interval, not a bound on backend/network latency.
Visibility means browser document visibility plus component mount, not whether
this section is currently within the viewport. No new server push route exists.
The list contract contains at most twenty tasks. If the selected task falls outside
that window while still active, its detail is read each cycle to track completion;
completed out-of-window results are retained without repeated detail reads.
A transient network/contract error also halts polling conservatively; the existing
manual refresh button retries. A hung request serializes later work until it
settles or the view unmounts; no new timeout policy is introduced.

No installation, live acceptance, backend permission proof or overall product
completion is claimed. The coordinator owns independent review, central workboard,
build/publication, live installation and rollback. Rollback of this source slice
is its bounded commit revert followed by the coordinator's ordinary dashboard build.
