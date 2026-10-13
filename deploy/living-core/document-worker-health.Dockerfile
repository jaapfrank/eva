FROM sha256:2393a1d5aa0a24f9eb0f19aebbbdbf9b32efe28949015bb002e90e181459dd5c
COPY --chown=65532:65532 document-worker-health.py /opt/eva2-health/document-worker-health.py
HEALTHCHECK --interval=30s --timeout=8s --start-period=30s --retries=3 CMD ["python3", "/opt/eva2-health/document-worker-health.py"]
