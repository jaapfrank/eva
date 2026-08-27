# Isolated development environment for the unchanged AgentBench FC requirements.
#
# This contains no Eva source, model credential, benchmark task image or task
# server.  A clean AgentBench checkout is later mounted read-only and its
# commit is preflighted inside this pinned Python 3.9 image.  CPU PyTorch
# avoids accidentally pulling the multi-gigabyte CUDA runtime for a benchmark
# that does not use Eva's GPU.
FROM python@sha256:2d97f6910b16bd338d3060f261f53f144965f755599aab1acda1e13cf1731b1b

WORKDIR /opt/eva-agentbench
COPY benchmark/agentbench-fc-v1/requirements.upstream.txt requirements.txt

# ``git`` is not part of AgentBench's Python environment.  It is the one
# direct OS package the fail-closed preflight needs to attest that the
# read-only upstream source mount is the pinned, clean checkout before any
# task server or model can start.  Apt also installs Git's required system
# dependencies; no extra Python dependency is introduced.
RUN apt-get update \
 && apt-get install --yes --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/* \
 && test "$(sha256sum requirements.txt | awk '{print $1}')" = "2a2ecd2cc0e25ef7155a088604213682d95912c94adfdab9a865ee4b4f0e5da2" \
 && pip install --no-cache-dir --disable-pip-version-check --only-binary=:all: \
      --extra-index-url https://download.pytorch.org/whl/cpu \
      -r requirements.txt \
 && pip check \
 && python -c 'from importlib.metadata import version; [version(name) for name in ("numpy", "pydantic", "requests", "tqdm", "PyYAML", "jsonlines", "aiohttp", "uvicorn", "fastapi", "urllib3", "mysql-connector-python", "docker", "SPARQLWrapper", "networkx", "anthropic", "fschat", "accelerate", "transformers")]'

USER 65532:65532
ENTRYPOINT ["python", "-I"]
