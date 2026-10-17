# One core/worker release

Build `deploy/living-core/Dockerfile` from a selected Git source snapshot,
`pyproject.toml`, `deploy/requirements-4d.lock` and a wheelhouse matching its
SHA256 pins. Its base images are digest-pinned, dependency installation is
`--no-index --require-hashes`, and `SOURCE_DATE_EPOCH` is the source commit time.
Optional model wheels use the separate existing lock and explicit build option.

The worker is a process role of that release. Build this directory's Dockerfile
with `CORE_IMAGE=sha256:<the-built-core-image-id>`. It inherits the exact installed
Python dependencies and source; the build runs the provider's existing SDK
version check. Its own command and healthcheck select the worker, with no extra
HTTP listener or second dependency resolver. The host must resolve and record an
immutable image ID; a tag is not a release identity.

`requirements-delegated-work.lock` and `delegated-work-dependency-roots.in` are
retained historical inputs for the earlier isolated sidecar. They are not inputs
to the supported current build. Their old pydantic-ai version and original hashes
are preserved as evidence; copying or silently regenerating them is not required.
`Dockerfile.source-refresh` remains a source-only operator recipe and now requires
an explicit current immutable worker parent. It cannot update dependencies.

For an existing installation, use the actual current image and canonical Compose
configuration through `eva.operations.runtime_image_update`, preserving other
executors' source and data mounts. It dispatches the selected image update once;
an uncertain result is inspected rather than blindly repeated. Schema migrations
and configuration changes have their existing separate installer paths. Full
future autonomous apply/recovery/later-use is E4.5, not established by this recipe.

The owner-selected model policy is Astra/medium or higher for substantive work
and memory extraction, Sol/none only for the existing simple-contact route.
Provider selection, optional channels, stored tasks and current identity belong
to the same core. No provider account, speech configuration or live memory model
is changed merely by editing these future build defaults.
