# Eva product and project dashboards

This directory maintains two separate builds: the packaged owner product UI
(`dist/`, served by Eva) and the development project dashboard (`project-dist/`,
port44712). The owner product UI is installed in the existing core image. It
reuses Eva's real owner API; it is not a second application backend.

The candidate uses React-admin's data and authentication providers above Eva's
existing owner-only API. Eva remains the authority for people, privacy,
permissions, work state, evidence and every mutation. The browser stores no
credential or UI state, and React-admin telemetry is disabled.

## Historical initial library selection

| Candidate | Exact line tested | Result |
| --- | --- | --- |
| React-admin | `react-admin 5.15.1`, React `19.2.8` | 200 locked packages, clean peer install, zero npm audit findings, 254.7 kB gzip JavaScript |
| Refine | core `5.0.12`, MUI `8.0.2`, React `18.3.1`, MUI `6.5.0` | 267 locked packages and zero audit findings, but a real MUI 5/6 peer conflict through `notistack 2.0.8`; telemetry also had to be disabled after the browser test observed an external call |
| Appsmith | current self-hosted platform | Rejected before a bundle trial: it adds a second application/runtime instead of replacing Eva's presentation layer with a library |

React-admin's JavaScript bundle is larger than the Refine trial. That cost is
recorded, not hidden. It wins because its current supported UI line installs
without peer overrides and already supplies the admin, data, authentication,
routing, forms and access-control building blocks required by later dashboard
slices.

## Product navigation — E2.1

Open `http://127.0.0.1:44711/internal/v1/dashboard` on the Eva computer through
its existing local owner gateway. No token needs to be entered in this local UI.
The core retains its existing owner access boundary.

The six sections are Overview, Conversation, People & memory, Work & results,
Initiative & learning, and Connections. Conversation opens the existing bound
conversation page; its return link opens this product entry again. Other sections
reuse the current people-memory status, memory source controls, delegated work,
initiative and component observation APIs. Only the selected section is mounted;
private source content is cleared by unmounting when leaving memory.

The E2.1 navigation, E2.2 memory controls and E2.3 conversation/work links are
installed. Full reflection/improvement history, extra provider settings and
WhatsApp remain the existing E2.4–E2.6 tasks. Unknown
connection observations stay unknown. The project workboard is not runtime data.

## People and memory controls — E2.2

Choose **Personen & geheugen**. The existing private Hindsight recall presents
up to twelve relevant memories and the names/topics mentioned in those memories.
Search by name or topic, then open **Bron bekijken en aanpassen**. Every result
must resolve to an exact currently accessible private source. This is a ranked
selection of the owner's memory, not an exhaustive directory or access to another
person's private account. Source information, Eva's inferences and unverified
legacy information have separate labels. A confirmed assessment of an inference
does not turn it into an independently verified fact.

Corrections reuse the existing journal, human-understanding revision chain and
coldpath processing. A typed correction links to the current head; stale heads
must be opened before correction. The UI says queued until storage processing
finishes. Forgetting reuses document erasure: for a derived/document source, the
original conversation or work result remains; for an exchange source, the existing
exchange-erasure semantics apply. The confirmation describes that difference.
The original paginated private source list remains independently accessible.

Run `verify-owner-memory.cjs` with the same Playwright/Chromium variables below.
Synthetic mode checks correction, the current revision, erasure, named search,
failure clearing and mobile layout. Set `EVA_DASHBOARD_ORIGIN` for a read-only
installed check. Installed mode reads an actual memory and exact source and never
corrects or forgets household data. Scoped recall may use the existing embedding
backend; no conversational model is invoked by these UI operations.

## Conversation, work and results — E2.3

The existing text conversation page shows **Opdrachten bij dit gesprek**. It uses
only the server-bound private episode, including resumed attempts. Open its work
link to see the saved task, state, worker progress and original conversation.
Stop and resume use the existing owner-work service. A resumed attempt preserves
the original source; it does not replace it with the resume instruction.

Digital sources reuse the exact original source/hash reader; retained Telegram
admission destinations provide an exact source for older task formats. Where a
legacy website job has only an episode, its exact message stays unavailable.
The UI never guesses a question from similar text, timestamps or a nearby chat.
Forgotten ingress remains unavailable even when its independent result survives.

Returns show a short excerpt and a link. Work detail also defaults to a short
excerpt. Choose **Volledig resultaat tonen** or then **Resultaat downloaden** to
request the full stored output. Progress remains worker notes; delivery state and
verified outcome remain separate. Hidden tabs clear private task/source views;
returning rechecks current authority. No conversation/model request is needed for
these work controls. This closes E2.3 presentation, not family acceptance.

`verify-owner-work-context.cjs` checks the isolated conversation-to-work link,
exact stop/resume selection, original source, progress, result disclosure on click,
privacy clearing and390px layout. It takes `EVA_CONVERSATION_FIXTURE` with the
exported current conversation HTML. Installed mode uses `EVA_DASHBOARD_ORIGIN`
and only reads existing own work; it never starts, stops or resumes household work.

## Reproduce the product build

Run from the repository root with the existing locked development dependencies:

```sh
npm --prefix development/tools/project-dashboard run build
python3 src/tools/build_dashboard_assets.py --materialize
python3 src/tools/build_dashboard_assets.py --check
EVA_DASHBOARD_PLAYWRIGHT_PACKAGE=/absolute/path/to/playwright \
EVA_DASHBOARD_CHROMIUM=/absolute/path/to/chromium \
  node development/tools/project-dashboard/verify-owner-navigation.cjs
```

The navigation proof uses a synthetic same-origin server, including a scoped
503 failure and private-panel unmount check. For a read-only installed check,
set `EVA_DASHBOARD_ORIGIN=http://127.0.0.1:44711`; it uses the existing owner
gateway. Both modes verify navigation, refresh/Back,390px layout and absence
of JavaScript errors or external requests. They send no conversation/model
request or task mutation. An isolated fixture is not evidence of live backend data.

Package assets are sealed by hash/size. Installation uses the current actual core
image with only the selected backend/UI files and retains configuration, mounts and the worker.
The project build is not replaced by a normal product build.

## Personal Chrome entry

The Browser card opens the existing personal Chrome controls at
`http://127.0.0.1:44718` in a new tab. It offers that link only when the dashboard
hostname is `localhost`, `127.0.0.1` or `[::1]`, and explains that it must be used
on Eva's Chrome computer. Remote dashboard visitors receive local-computer
instructions instead of a loopback link to their own device.

The card does not fetch browser status or send control requests. Sharing a tab,
taking over and reconnecting use the existing bridge page and explicit extension
selection. The backend's legacy browser routes remain unchanged. To run only
the synthetic entry-point checks, set `EVA_DASHBOARD_BROWSER_ONLY=1` alongside
the two browser-test paths above; no live bridge or tab sharing is used.

## Local project dashboard (port 44712)

Build with `npm run build -- --mode project`, then restart the user service
`systemctl --user restart eva2-project-dashboard.service`. Project output lives
in `project-dist/`, separate from the packaged dashboard in `dist/`. A normal
packaged build must not erase the running project dashboard. Check `/`, its
referenced assets, and `/project-state` after installing.


### Owner overview freshness

The overview contains four lanes and visible component summaries. Historical
proof lives on the separate Details & historie tab. Component acceptance is
still derived by the existing completion projection, never by recent activity.

At each meaningful start, result, blocker or installation, the coordinator updates
`audit.projectDashboard.deliveryOverview` in the existing workboard JSON and the
current paragraph in development/docs/WERKBORD.md together. Set `reviewedAt` to the actual ISO UTC
time of that content review. Never advance it from polling, builds, file mtime or
an unrelated edit. Missing/invalid timestamps are unknown; after 60 minutes the
UI requests rechecking. This is a freshness warning, not an automatic judgement
of engineering progress. Polling still refreshes the stored source every 5 seconds.

Validate with `node --test project-freshness.test.mjs project-completion.test.mjs`
and `npm run build -- --mode project`. The existing preview serves project-dist;
rollback the UI with its prior committed source and rebuild in project mode.

### Small-task checklist

The project Overview and Tasks pages show every existing
`deliveryPlan.implementationSlices[].remainingTasks` item directly. Only a child's
`completed` status is green; all remaining work is red, including pending physical
trials. Search and All/Open/Completed filters do not change the workboard. Open a
row for its finish condition, owner, blocker and recorded trial steps. The normal
five-second source refresh updates the list; child completion is never inferred
from its parent's state. This is independent of the nine product-component counter.

### Initiative and learning history (E2.4)

`Initiatief & leren` reads `/internal/v1/dashboard/initiatives/history` through
the existing owner binding. It shows adopted reasons, saved short results, real
feedback and requested full contributions. Memory reflections, later exact context
bindings and component candidate/evaluation/reference/application receipts are
separate facts. A saved reflection or supplied context is not a proven behavior
improvement; missing per-contribution later-use evidence is explicitly unknown.
Reading this section never starts a model, experiment or application.

The work section counts current owner work before the history limit. Open work
and waiting start agreements appear first; closed failed/uncertain attempts and
dispatched agreements remain accessible as history. Closing a presentation lane
does not change any task outcome or retry work.

Run `verify-owner-learning.cjs` and `verify-owner-work-overview.cjs` with the same
Playwright/Chromium variables as the navigation check. The learning script supports
`EVA_DASHBOARD_ORIGIN` for an installed read-only check; synthetic feedback is
submitted only to its isolated local fixture.

### Optional model connections (E2.5)

`Verbindingen` offers the existing subscription route plus optional Anthropic and
OpenAI-compatible text backends. Add a workspace API key, exact model ID and
provider endpoint, explicitly check one small tool-shaped response, then select
the connection. Selection and disable are persisted in the existing work volume
under a private `model-providers` directory. Credentials use InstallerSecretStore;
GET responses never return them. Reading status or saving configuration makes no
provider call. Checks may incur provider usage and are never automatically retried.

Selection applies to private website/Telegram model generations through the same
conversation, memory, journal and action contracts. Durable background work, native
research and initiative retain their configured Astra subscription route. Voice
uses the existing connection unchanged. A failed selected provider does not resend
family context to a fallback provider. Disable blocks the next admission; already
dispatched work may finish. Remote endpoints require HTTPS.

For packaged installs use the `optional-models` project extra. Reproducible Docker
builds accept `--build-arg INSTALL_OPTIONAL_MODELS=1` with the two extra wheels from
`deploy/requirements-optional-models.lock` in the normal wheelhouse. Without the
extra, status accurately shows the missing Anthropic adapter. This does not reuse
Claude subscription credentials or create another agent. Run
`verify-owner-model-providers.cjs` with the existing Playwright variables; setting
`EVA_DASHBOARD_ORIGIN` uses installed read-only verification.

Existing root-owned work volumes need only a new empty private
`/var/lib/eva/work/model-providers` directory owned by runtime UID/GID65532, mode700.
The current installation has been prepared without changing existing files. Fresh
named volumes inherit this directory from the image. Missing unchosen storage
never blocks default Eva startup; a configure operation reports an unavailable
write instead of silently switching providers.

Official protocol/authentication sources checked for this implementation:
[Anthropic authentication](https://platform.claude.com/docs/en/manage-claude/authentication),
[OpenAI authentication](https://developers.openai.com/api/reference/overview),
[PydanticAI Anthropic](https://pydantic.dev/docs/ai/models/anthropic/) and
[PydanticAI OpenAI](https://pydantic.dev/docs/ai/models/openai/).
