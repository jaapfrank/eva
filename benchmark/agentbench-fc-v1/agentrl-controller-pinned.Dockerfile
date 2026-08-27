# Disposable AgentRL controller build for AgentBench development only.
# The source tree is the exact AgentRL checkout selected by the verifier.
ARG AGENTRL_COMMIT=6a73409d31ba695d383b978a8ad3ef400d90c054
ARG AGENTRL_GIT_ARCHIVE_SHA256=76a50942687e5646ad401adbb579e70fafb0761175e61019cc2c72cd0b7ce2ee
ARG AGENTRL_CONTROLLER_GOMODCACHE_SHA256=4abfff3108ce315dc34b7b0d3474c8a6431534a219d37b2369e4d08f71765b57
ARG PROTOC_SHA256=96553041f1a91ea0efee963cb16f462f5985b4d65365f3907414c360044d8065
ARG PROTOC_GEN_GO_SHA256=2596e878bbebd8a1532814308b0671163fa13ead676f7896dcef4d4cf981e955
ARG PROTOC_GEN_GO_GRPC_SHA256=ab1571864246d188885e5d23b46a0296add2b3d71aa79d8ddc29287b62537bed
FROM docker.io/library/golang:1.24.5-bookworm@sha256:ef8c5c733079ac219c77edab604c425d748c740d8699530ea6aced9de79aea40 AS builder

ARG AGENTRL_COMMIT
ARG AGENTRL_GIT_ARCHIVE_SHA256
ARG AGENTRL_CONTROLLER_GOMODCACHE_SHA256
ARG PROTOC_SHA256
ARG PROTOC_GEN_GO_SHA256
ARG PROTOC_GEN_GO_GRPC_SHA256

ENV GOPROXY=off \
    GOSUMDB=off

# Every build input below is supplied from the separate sealed context.  The
# controller build itself can therefore run with --network none: no apt index,
# Go module proxy, GitHub release download, or generator source is consulted.
COPY agentrl-controller-gomodcache.tar /tmp/gomodcache.tar
RUN echo "${AGENTRL_CONTROLLER_GOMODCACHE_SHA256}  /tmp/gomodcache.tar" | sha256sum --check --strict \
    && mkdir -p /go/pkg/mod \
    && tar -xf /tmp/gomodcache.tar -C /go/pkg/mod \
    && rm /tmp/gomodcache.tar

COPY protoc-31.1-linux-x86_64.zip /tmp/protoc.zip
RUN echo "${PROTOC_SHA256}  /tmp/protoc.zip" | sha256sum --check --strict \
    && python3 -c 'from zipfile import ZipFile; ZipFile("/tmp/protoc.zip").extractall("/usr/local")' \
    && chmod 0755 /usr/local/bin/protoc \
    && rm /tmp/protoc.zip

COPY protoc-gen-go /usr/local/bin/protoc-gen-go
COPY protoc-gen-go-grpc /usr/local/bin/protoc-gen-go-grpc
RUN echo "${PROTOC_GEN_GO_SHA256}  /usr/local/bin/protoc-gen-go" | sha256sum --check --strict \
    && echo "${PROTOC_GEN_GO_GRPC_SHA256}  /usr/local/bin/protoc-gen-go-grpc" | sha256sum --check --strict \
    && chmod 0755 /usr/local/bin/protoc-gen-go /usr/local/bin/protoc-gen-go-grpc

WORKDIR /src

COPY controller/go.mod controller/go.sum ./controller/
RUN cd controller && go mod verify

COPY proto ./proto
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
ARG AGENTRL_CONTROLLER_GOMODCACHE_SHA256
LABEL org.opencontainers.image.revision="${AGENTRL_COMMIT}" \
      org.opencontainers.image.source-archive-sha256="${AGENTRL_GIT_ARCHIVE_SHA256}" \
      io.eva.agentrl-controller-gomodcache-sha256="${AGENTRL_CONTROLLER_GOMODCACHE_SHA256}"
COPY --from=builder /agentrl /agentrl
USER 65532:65532
EXPOSE 5020
ENTRYPOINT ["/agentrl"]
