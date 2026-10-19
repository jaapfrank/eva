# Optional camera observations

The observer enriches the existing sensory stream. It does not run a separate
agent, greeting policy, memory store or household scheduler. The shared Eva
decides whether to communicate, propose work, act within existing permissions or
remain silent. Registry integration metadata can inform further investigation;
there is no rule that requests credentials for a particular vendor.

## Sources and installation

With an existing Home Assistant connection, set `EVA_CAMERA_OBSERVER_ENABLED=true`
on the core. Disabled is the default. The adapter discovers registered cameras
and joins typed motion/presence/person/doorbell sensors by exact device identity.
It refreshes discovery and samples one advertised view per device. It does not
require camera or room IDs in code. Cameras without a supported trigger and
alternate views remain explicitly uncovered. Event-only triggers and cameras
outside this HA adapter need another source adapter, not household decision rules.

The existing local vision configuration can supply scene descriptions. Set
`EVA_CAMERA_OBSERVER_SCENE_ENABLED=false` to omit scene inference while retaining
face analysis. This only changes the observer; it does not replace the global
vision service or a voice route.

Optional local enrolled-face similarity uses `EVA_LOCAL_FACE_URL`,
`EVA_LOCAL_FACE_REVISION` and `EVA_LOCAL_FACE_TOKEN_FILE`. Run the narrow host
transport using [the service example](face-bridge.service.example). Bind an exact
local interface. Use two private copies of the same bearer token if host and core
have different effective UIDs: each file must be owned by its runtime UID with
owner-only permissions. Mount only the core copy, read-only. Do not expose the
existing loopback model server or profile database to the network.

The bridge can use the installed ArcFace HTTP service or explicitly selected CPU
execution. CPU mode requires `--cpu-python`, `--cpu-model-root` (the InsightFace
root containing `models/buffalo_l/`) and a revision ending in `:cpu`. It uses the
two existing pinned ONNX assets with CPU-only sessions and no downloader. It
imports no Eva1 agent or service. The existing SQLite profile input is opened
read-only; the matcher and recognition backend are separate from its data format.
An installation without confirmed reference profiles cannot recognize names.
This module never enrolls a person from an unverified guess.

## Evidence and limits

Only bounded image provenance, analysis time, optional scene interpretation and
accepted face candidates enter the existing sensory journal. Frames and vectors
are not retained there. Retrieval time is not camera capture time. Name candidates
are similarity evidence, not authentication, and grant no access to private memory.
Cosine scores use scaled integers (`*_ppm`); they are not probabilities.

One bounded analysis queue keeps ordinary house events independent of inference.
Repeated source IDs are ignored, bursts are coalesced, and newer binary resets
cannot displace the latest actual image evidence in the household selection.
Stopping prevents subsequent stages and delivery; an in-flight local operation
has its transport/process deadline. There is no retry loop or frame archive.

The installed 19 October variant uses CPU face analysis because the OS blocks GPU
access. Scene inference is explicitly off for this observer. Actual source
processing is proven; recognition accuracy, multi-person accuracy and whether
Eva chooses useful reactions in daily use are separate observations.
