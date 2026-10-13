# Temporary dependency-compatibility probe; never a production or benchmark image.
FROM docker.io/library/golang:1.25-bookworm@sha256:3b4a11519ad929d1e1d261a12cff056f0c85b735253d7d861346b9c6f8b36437 AS builder
WORKDIR /src
COPY protoc-31.1-linux-x86_64.zip /tmp/protoc.zip
RUN install -d -m 0755 /opt/protoc \
    && python3 -c 'from zipfile import ZipFile; ZipFile("/tmp/protoc.zip").extractall("/opt/protoc")' \
    && chmod 0755 /opt/protoc/bin/protoc
COPY protoc-gen-go /usr/local/bin/protoc-gen-go
COPY protoc-gen-go-grpc /usr/local/bin/protoc-gen-go-grpc
RUN chmod 0755 /usr/local/bin/protoc-gen-go /usr/local/bin/protoc-gen-go-grpc
COPY proto ./proto
COPY controller ./controller
COPY agentrl-controller-security-upgrade-v1.patch /tmp/security-upgrade.patch
RUN cd /src && git apply /tmp/security-upgrade.patch \
    && cd controller && go mod download && go mod verify
RUN install -d -m 0755 controller/internal/pb controller/dashboard/dist \
    && printf '%s\n' '<!doctype html><title>AgentRL development controller</title>' \
        > controller/dashboard/dist/index.html \
    && cd proto \
    && find . -type f -name '*.proto' -print0 \
      | xargs -0 /opt/protoc/bin/protoc --proto_path=. \
          --go_out=../controller/internal/pb --go_opt=paths=source_relative \
          --go-grpc_out=../controller/internal/pb --go-grpc_opt=paths=source_relative \
    && cd ../controller \
    && CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -buildvcs=false -o /agentrl .

FROM cgr.dev/chainguard/static@sha256:96d02f455d5a73b817c0602910748609cf8471b1cc9522f78c75cedb1f67d072
COPY --from=builder /agentrl /agentrl
USER 65532:65532
ENTRYPOINT ["/agentrl"]
