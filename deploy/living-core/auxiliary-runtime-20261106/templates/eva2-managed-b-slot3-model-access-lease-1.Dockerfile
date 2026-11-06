FROM sha256:455be8e6f52adcf2f16288ad76793f150684c058753de3d8d84730a7ce623f7e AS installed
FROM python@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS builder
USER 0:0
RUN ["/usr/local/bin/python", "-B", "-c", "import pathlib,shutil;s=pathlib.Path('/usr/local/lib/python3.12/site-packages');t=pathlib.Path('/build-tools');t.mkdir();[shutil.copytree(p,t/p.name) for p in s.glob('pip*') if p.is_dir()];[shutil.rmtree(p) if p.is_dir() else p.unlink() for p in s.iterdir()]"]
COPY --from=installed /usr/local/lib/python3.12/site-packages/ /usr/local/lib/python3.12/site-packages/
COPY . /build/
ENV PYTHONPATH=/build-tools PYTHONDONTWRITEBYTECODE=1 PIP_CONFIG_FILE=/dev/null PIP_DISABLE_PIP_VERSION_CHECK=1
RUN ["/usr/local/bin/python", "-B", "/build/install.py"]
RUN ["/usr/local/bin/python", "-B", "/build/prepare-runtime.py"]
FROM installed
USER 0:0
COPY role-plan.json site-before.json old-runtime.json cleanup.py /build/
RUN ["/usr/local/bin/python", "-B", "/build/cleanup.py"]
COPY --from=builder /runtime/ /
COPY --from=builder /usr/local/lib/python3.12/site-packages/ /usr/local/lib/python3.12/site-packages/
COPY --from=builder /updated-external/ /
COPY --from=builder /build-proof.json /opt/eva/auxiliary-runtime-upgrade.json
COPY pyproject.toml /opt/eva/pyproject.toml
COPY requirements.lock /opt/eva/auxiliary-runtime/requirements.lock
COPY model_credentials.py /opt/eva/src/eva/operations/model_credentials.py
USER 65532:65532
