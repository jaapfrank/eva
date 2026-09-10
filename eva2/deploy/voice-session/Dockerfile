FROM python@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217 AS build
COPY deploy/voice-session/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --requirement /tmp/requirements.lock
COPY src/eva/voice_service/ /opt/eva/src/eva/voice_service/
COPY src/eva/adapters/connectors/home_assistant_voice.py /opt/eva/src/eva/adapters/connectors/home_assistant_voice.py
COPY deploy/voice-session/vendor/livekit-client.js /opt/eva/voice-assets/livekit-client.js
FROM gcr.io/distroless/cc-debian13@sha256:9b615fff20e1a4fad29c2b30562580b212c7dd5e2225236735cca0070ed11c78
COPY --from=build /usr/local /usr/local
COPY --from=build /opt/eva /opt/eva
COPY --from=build /usr/lib/x86_64-linux-gnu/libffi.so.8* /usr/lib/x86_64-linux-gnu/
COPY --from=build /usr/lib/x86_64-linux-gnu/libz.so.1* /usr/lib/x86_64-linux-gnu/
# ONNX Runtime 1.29.0 invokes a shell during import; absence causes a native crash.
# Keep only dash, not the unrelated utilities it optionally probes for telemetry.
COPY --from=build /usr/bin/dash /bin/sh
ENV PYTHONPATH=/opt/eva/src LD_LIBRARY_PATH=/usr/local/lib PYTHONDONTWRITEBYTECODE=1 PATH=/usr/local/bin:/usr/bin
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/python"]
CMD ["-m", "uvicorn", "eva.voice_service.server:create_app", "--factory", "--host", "127.0.0.1", "--port", "44716", "--no-access-log"]
