# ContextForge 1.1 maintenance candidate

This offline recipe prepares four image families for the six existing gateway
and translator roles. It does not change a running service. The gateway uses
eight reviewed source changes; the translators use official 1.1.0 source.
The application imports under /app/mcpgateway. The installed wheel keeps its
own RECORD; the effective source tree has a separate manifest. The five
existing gateway code binds use the matching reviewed bytes at activation.

Use a local artifact directory with wheels/, rpms/, and build-artifacts/.
Place the 141 exact locked wheels, the applicable 30 or 37 signed UBI RPMs,
the pinned build-only pip wheel, and the proven CPython prefix archive there.
The manifests specify every filename and checksum and the available official
artifact URLs. native-source-recipe.json describes the matching native build
inputs, compiler settings, and source-built Psycopg C wheel. The prefix and
C wheel are derived artifacts. A fresh compiler run is a candidate and needs
new byte binding and review if its outputs differ.

Run "python3 prepare_context.py gateway ARTIFACTS NEW_CONTEXT", using one of
gateway, filesystem, atomic, or projects. The output directory must not exist.
The assembler verifies the artifacts, applies only the reviewed patch, and
emits a Dockerfile bound to the actual predecessor image. With that image
already present, build with "docker build --network none NEW_CONTEXT". The
legacy builder keeps a public-artifact input layer; those inputs are absent
from the final application filesystem. There is no private operator
configuration, runtime data, token, or host path in the build context.

The provider recipe reinstates the exact existing /usr/bin/node link after
the vendor Node22 RPM updates, so the translator still starts Node26.13.0.
Node, npm, and tool source bytes stay exact. Raw metadata has 16 filesystem
tools or two atomic tools per provider. The gateway still admits 14 filesystem
tools and four existing two-tool atomic catalogs. The extra raw tools gain
no access.

Activation is a separate reviewed operation with current claim, generation,
profile, network, and data checks. Both documented gateway cache flags become
false to enforce the existing idle policy. A new schema head excludes old
1.0.8 fallback and database restoration. Preserve the newer runtime and data
for forward repair. This recipe does not claim installation or full
automatic recovery.

Run "python3 verify_image.py gateway" or select the provider family to
verify the reviewed image in an isolated metadata-only process. It verifies
the installed package records, full application source hashes, the native
version and RPM updates, and the preserved Node link and binary. An explicit
new image digest may be supplied for a freshly rebuilt candidate. The
reproduction readback records the contexts assembled and the existing
candidate images checked. It does not claim a fresh full image rebuild.
