FROM sha256:0ef19e699069c3a5d3a26812c5da3d31141fcd5c3faedb14f92c9dcce1209465
COPY --chown=10001:10001 document-provider-health.py /opt/eva2-health/document-provider-health.py
HEALTHCHECK --interval=30s --timeout=8s --start-period=30s --retries=3 CMD ["python3", "/opt/eva2-health/document-provider-health.py"]
