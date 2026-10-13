# Optional local camera interpretation

This module serves the existing Home Assistant camera GET adapter. It does not
poll cameras, add speaker identity recognition or start household initiatives.
Images go to this local service only. Interpretations retain the image hash,
retrieval time, selected model/revision and an explicit unverified interpretation
label; retrieval time is not camera capture time.

Use the pinned assets and checksums from
`../../research/2026-09-28-vision-gpu-selection.md`. The installed instance uses
Qwen3.5-4B Q4_K_M and llama.cpp b11146 with CUDA on the WSL host. Existing assets
are reused; this template does not download or update models automatically.
The service reserves GPU memory while running; stop it to release that memory.

Copy `eva2-local-vision.service` into the user's systemd directory. Supply
`~/.config/local-vision.env` with VISION_RUNTIME, VISION_MODEL_FILE,
VISION_PROJECTOR_FILE, VISION_BIND_ADDRESS, VISION_PORT and LD_LIBRARY_PATH.
VISION_RUNTIME must be an absolute path without whitespace. Bind only the local
Docker bridge used by Eva, not 0.0.0.0 or a public interface. Bridge changes require
updating this binding and the core/worker endpoint. Start after that bridge exists.
Use `systemctl --user enable --now eva2-local-vision.service`.

Set EVA_LOCAL_VISION_URL, EVA_LOCAL_VISION_MODEL=eva-local-vision and
EVA_LOCAL_VISION_REVISION in the existing core and worker compose configuration.
The revision is deployment provenance, not remote weight attestation. Preserve the
current images/configuration and use the normal managed rollback before changing
these variables. Verify `/health`, model identity and a camera interpretation from
inside the core; a healthy HTTP process alone does not prove image quality.

Disable by restoring the previous core/worker compose configuration, then run
`systemctl --user disable --now eva2-local-vision.service`. No camera or memory
state needs deleting. Failed/offline analysis is reported by the existing tool.

Owner use: ask Eva to describe the garage camera or another existing HA camera.
The model must select the camera through existing HA discovery; no camera-specific
command or name matching has been added. Monochrome imagery cannot establish real
object colours. One reviewed scene does not establish reliable bin counting or
other autonomous decisions. Those remain separate product requirements.

## Existing private initiative integration

`initiative-read-profile.json` is a template for the existing native initiative
mandate, not a second scheduler. Replace its configuration digest with the actual
installed HA API provider digest (including the selected vision configuration).
Merge it without removing other capabilities, then use the existing append-only
mandate revision endpoint. Existing running claims retain their old digest and
must never be silently rebound. Keep the old file and revision for rollback.

The profile permits only camera and calendar REST GET requests. It adds no house
writes or outbound recipient. Native output remains owner-private; this profile
must not be used as authority to expose a private agenda to shared speakers.
The existing initiative inventory profile also needs optional `domain` (empty or
a valid HA domain), retaining its old required fields for saved five-field calls.
Model/reflection limits remain unchanged. A zero transport-fee profile does not
mean free model inference or energy. Autonomous useful reminders require a separate
bound event/scene/calendar result; installing this profile alone does not prove one.
