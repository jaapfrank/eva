FROM golang:1.25.11-bookworm@sha256:b96f24a8d7d010ea0acb9c3ba99064740f02b6b984612b28bd3c9c5ab9453e38 AS build
ENV GOTOOLCHAIN=local CGO_ENABLED=0
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download && go mod verify
COPY *.go ./
RUN go test ./... && go build -trimpath -ldflags="-s -w -buildid=" -o /whatsapp-calling .
RUN mkdir -p /private-state/eva-whatsapp && chown 65532:65532 /private-state/eva-whatsapp && chmod 700 /private-state/eva-whatsapp

FROM gcr.io/distroless/static-debian12:nonroot@sha256:afa5c872c891853ca7fcf1f12c3edb23f7eeef36189728842dd51042ff57f7ab
COPY --from=build /whatsapp-calling /whatsapp-calling
COPY --from=build --chown=65532:65532 /private-state/ /var/lib/
USER 65532:65532
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=6 CMD ["/whatsapp-calling", "--healthcheck"]
ENTRYPOINT ["/whatsapp-calling"]
