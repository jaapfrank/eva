# Disposable AgentRL controller build for AgentBench development only.
# The source tree is the exact AgentRL checkout selected by the verifier.
ARG AGENTRL_COMMIT=6a73409d31ba695d383b978a8ad3ef400d90c054
ARG AGENTRL_GIT_ARCHIVE_SHA256=76a50942687e5646ad401adbb579e70fafb0761175e61019cc2c72cd0b7ce2ee
FROM docker.io/library/golang:1.24.5-bookworm@sha256:ef8c5c733079ac219c77edab604c425d748c740d8699530ea6aced9de79aea40 AS builder

ARG AGENTRL_COMMIT
ARG AGENTRL_GIT_ARCHIVE_SHA256

WORKDIR /src

# AgentRL's pinned source uses protobuf edition 2023. Debian bookworm's
# compiler is too old, so this exact upstream compiler is verified before use.
# The final runtime is digest-bound below; this build stage never becomes part
# of Eva or the public benchmark result.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl unzip \
    && rm -rf /var/lib/apt/lists/*

ARG PROTOC_VERSION=31.1
ARG PROTOC_SHA256=96553041f1a91ea0efee963cb16f462f5985b4d65365f3907414c360044d8065
RUN curl --fail --location --silent --show-error \
        --output /tmp/protoc.zip \
        "https://github.com/protocolbuffers/protobuf/releases/download/v${PROTOC_VERSION}/protoc-${PROTOC_VERSION}-linux-x86_64.zip" \
    && echo "${PROTOC_SHA256}  /tmp/protoc.zip" | sha256sum --check --strict \
    && unzip -q /tmp/protoc.zip -d /usr/local \
    && rm /tmp/protoc.zip

COPY controller/go.mod controller/go.sum ./controller/
RUN cd controller && go mod download

COPY proto ./proto
RUN GOBIN=/usr/local/bin go install google.golang.org/protobuf/cmd/protoc-gen-go@v1.36.10 \
    && GOBIN=/usr/local/bin go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@v1.5.1

COPY controller ./controller
RUN mkdir -p controller/internal/pb controller/dashboard/dist \
    && printf '%s\n' '<!doctype html><title>AgentRL development controller</title>' \
        > controller/dashboard/dist/index.html \
    && cd proto \
    && find . -type f -name '*.proto' -print0 \
      | xargs -0 protoc --proto_path=. \
          --go_out=../controller/internal/pb --go_opt=paths=source_relative \
          --go-grpc_out=../controller/internal/pb --go-grpc_opt=paths=source_relative \
    && cd ../controller \
    && CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -buildvcs=false -o /agentrl .

FROM gcr.io/distroless/base-debian12:nonroot@sha256:7f0c72cd138b442ae0deeb69c08b1acf5525439ba251a49ad93c320a061567e5

ARG AGENTRL_COMMIT
ARG AGENTRL_GIT_ARCHIVE_SHA256
LABEL org.opencontainers.image.revision="${AGENTRL_COMMIT}" \
      org.opencontainers.image.source-archive-sha256="${AGENTRL_GIT_ARCHIVE_SHA256}"
COPY --from=builder /agentrl /agentrl
USER 65532:65532
EXPOSE 5020
ENTRYPOINT ["/agentrl"]
