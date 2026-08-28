from pathlib import Path


ROOT = Path(__file__).parent


def test_hindsight_image_is_api_only_and_exactly_pinned() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "ghcr.io/vectorize-io/hindsight@sha256:" in dockerfile
    assert "gcr.io/distroless/cc-debian13@sha256:" in dockerfile
    assert "COPY --from=upgraded_api /app/api /app/api" in dockerfile
    assert "COPY --from=upstream /app/control-plane" not in dockerfile
    assert "COPY --from=upstream /app/node_modules" not in dockerfile
    assert "USER 1000:1000" in dockerfile
    assert 'ENTRYPOINT ["/app/api/.venv/bin/hindsight-api"]' in dockerfile


def test_hindsight_security_updates_are_hash_bound() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    expected = {
        "aiohttp-3.14.3": "53e7b4ce82b54a8bcc71b3b67a5cbd177ca1d7f592cbc92cd38b7349f73482db",
        "cryptography-50.0.1": "51afcfceb15597cf2635068e4ac9a56b2abde622edde17f37d85fd7b5306497a",
        "jaraco_context-6.1.0": "a43b5ed85815223d0d3cfdb6d7ca0d2bc8946f28f30b6f3216bda070f68badda",
    }
    for name, digest in expected.items():
        assert name in dockerfile
        assert digest in dockerfile
    assert "--offline --no-index --no-deps --reinstall" in dockerfile


def test_pgvector_image_pins_source_bases_and_build_packages() -> None:
    dockerfile = (ROOT / "Dockerfile.pgvector").read_text(encoding="utf-8")

    assert "8ee86c96f0fd72390f890aa8a336fda6d3ab4c6c" in dockerfile
    assert "sha256:8e9b080f89604bab8fcfe48d02569f5d9f0a822013ad0f55667d70019e8ea594" in dockerfile
    assert "sha256:75f633a8213629eef75f31a80b69756eb007ba1cafaa005d7cc47e5a09221c2d" in dockerfile
    for package in (
        "build-base=1-r9",
        "clang-19=19.1.7-r27",
        "llvm-19=19.1.7-r27",
        "postgresql-18-dev=18.6-r0",
    ):
        assert package in dockerfile


def test_pgvector_final_image_contains_only_runtime_extension_paths() -> None:
    dockerfile = (ROOT / "Dockerfile.pgvector").read_text(encoding="utf-8")
    final = dockerfile.split("\nFROM cgr.dev/chainguard/postgres@", 1)[1]

    assert "/out/usr/lib/postgresql18 /usr/lib/postgresql18" in final
    assert "/out/usr/share/postgresql18/extension" in final
    assert "apk add" not in final
    assert "COPY . /src" not in final
