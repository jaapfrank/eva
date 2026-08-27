# Isolated development environment for the unchanged AgentBench FC requirements.
#
# This contains no Eva source, model credential, benchmark task image or task
# server.  The exact clean AgentBench checkout is preflighted on the host and
# later mounted read-only for an explicitly approved run.  CPU PyTorch avoids
# accidentally pulling the multi-gigabyte CUDA runtime for a benchmark that
# does not use Eva's GPU.
FROM python@sha256:2d97f6910b16bd338d3060f261f53f144965f755599aab1acda1e13cf1731b1b

WORKDIR /opt/eva-agentbench
COPY benchmark/agentbench-fc-v1/requirements.upstream.txt requirements.txt

RUN test "$(sha256sum requirements.txt | awk '{print $1}')" = "2a2ecd2cc0e25ef7155a088604213682d95912c94adfdab9a865ee4b4f0e5da2" \
 && pip install --no-cache-dir --disable-pip-version-check --only-binary=:all: \
      --extra-index-url https://download.pytorch.org/whl/cpu \
      -r requirements.txt \
 && pip check \
 && python -c 'from importlib.metadata import version; [version(name) for name in ("numpy", "pydantic", "requests", "tqdm", "PyYAML", "jsonlines", "aiohttp", "uvicorn", "fastapi", "urllib3", "mysql-connector-python", "docker", "SPARQLWrapper", "networkx", "anthropic", "fschat", "accelerate", "transformers")]'

USER 65532:65532
ENTRYPOINT ["python", "-I"]
