# Core maintenance payload — 6 November 2026

This reproduces the reviewed dependency, CPython and three application-file overlays over the exact retained local core/worker base images in `manifest.json`. It does not install services, copy a working tree, carry private configuration, or claim a new whole-project build. The legacy Docker builder does not make image IDs reproducible; the verifier checks the bound file payload instead.

The final interpreter is **CPython3.12.16** from the pinned official Debian12 vendor image. The installed base remains Distroless Debian13. The historical3.12.14 dependency stage reproduces the already verified intermediate payload; it is never the deployment target. Native OS security updates are a separate overlay. The current main `deploy/living-core/Dockerfile` build stage also selects3.12.16. Its `runtime_libs` stage supplies only libffi/zlib, not the interpreter.

Prerequisites: Linux amd64 Docker, the exact local base images and two official pinned Python images in the manifest, and this source revision with matching five source-file hashes. Pull the two official images by digest if absent. Wheel downloads use published PyPI URLs and SHA256; `--wheelhouse` can supply an already verified local cache. No optional model provider is enabled.

For each role (`core` or `worker`), prepare a NEW directory and build only these overlays:

```sh
recipe=deploy/living-core/core-maintenance-20261106
role=core
context=/tmp/eva-core-maintenance-context
python3 "$recipe/prepare_context.py" --role "$role" --output "$context"
docker build --network none --build-arg BASE_IMAGE=sha256:9ba125818a6b21ac7ee93c6095dddba2f69b12bbf09da66883929b761d2d710c -t eva-reproduce-dependencies "$context/dependencies"
docker build --network none --build-arg BASE_IMAGE=eva-reproduce-dependencies -t eva-reproduce-comparator "$context/comparator"
docker build --network none --build-arg BASE_IMAGE=eva-reproduce-comparator -t eva-reproduce-python16 "$context/python"
docker run --rm --network none --read-only --mount "type=bind,src=$(pwd)/$recipe/manifest.json,dst=/recipe-manifest.json,readonly" --mount "type=bind,src=$(pwd)/$recipe/verify_payload.py,dst=/verify.py,readonly" --entrypoint /usr/local/bin/python eva-reproduce-python16 /verify.py "$role"
```

For `worker`, use its exact `baselineImage` from the manifest in the first build and a separate new context. The preparation command refuses changed source hashes, changed package identities, unknown base images and existing output directories. The three Docker contexts contain only selected wheels, public file hashes, three scoped source files, the source package declaration, and the current dependency lock. No deployment configuration, environment, access lease, DSN, household conversation or work payload is included.

The completed installation and measured regressions are recorded separately in `development/evidence/reassessment/maintenance-updates-20261106/core-*.json`. Original historical3.12.14 gates and receipts remain unchanged. A later operator must independently bind current configuration, idle claims and authority before any activation; these recipes deliberately perform no activation.
