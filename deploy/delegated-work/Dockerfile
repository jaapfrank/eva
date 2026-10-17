# Build the core once from deploy/living-core/Dockerfile and its hash-locked
# requirements-4d.lock. The worker uses that exact source/dependency release.
# CORE_IMAGE must be an immutable sha256 image reference, verified by the host.
ARG CORE_IMAGE
FROM ${CORE_IMAGE}

RUN ["/usr/local/bin/python", "-B", "-c", "from eva.bootstrap.delegated_work_provider import verify_delegated_provider_dependencies; verify_delegated_provider_dependencies()"]
USER 65532:65532
HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
  CMD ["/usr/local/bin/python", "-m", "eva.bootstrap.delegated_work_service", "--check"]
ENTRYPOINT ["/usr/local/bin/python"]
CMD ["-m", "eva.bootstrap.delegated_work_service"]
