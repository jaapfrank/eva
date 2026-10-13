FROM agent0ai/agent-zero-base:latest@sha256:342ffbf59b5d2771c01f348cb9ee3e05121ad7a639aa5bff5c48c2912685eda8

ARG BRANCH=local
ARG SOURCE_COMMIT
RUN test -n "${SOURCE_COMMIT}"
COPY docker/run/fs/ /
COPY . /git/agent-zero
RUN bash /ins/pre_install.sh "${BRANCH}"
RUN bash /ins/install_A0.sh "${BRANCH}"
RUN mkdir -p /a0 && cp -a /git/agent-zero/. /a0/

WORKDIR /a0
LABEL org.opencontainers.image.source="https://github.com/agent0ai/agent-zero" \
      org.opencontainers.image.revision="${SOURCE_COMMIT}" \
      org.opencontainers.image.version="v2.11"
EXPOSE 80
CMD ["/opt/venv-a0/bin/python", "run_ui.py", "--dockerized=true", "--host=0.0.0.0", "--port=80"]
