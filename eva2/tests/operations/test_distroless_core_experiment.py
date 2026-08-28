from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / "experiments/distroless-core-v1"
SOURCE_ID = "b545dfaba03995a81b9eafe08d62ab0eb06e5eb3766e7c649043238a44e8e135"
PYTHON_BASE = "09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217"
DISTROLESS_BASE = "9dac0a79194e45a7da0158a9c6da57b217585af0786db3845d1f0ec1a0dd182f"
CRYPTOGRAPHY_WHEEL = "51afcfceb15597cf2635068e4ac9a56b2abde622edde17f37d85fd7b5306497a"


def test_distroless_candidate_binds_all_external_image_inputs() -> None:
    dockerfile = (EXPERIMENT / "Dockerfile").read_text(encoding="utf-8")

    assert SOURCE_ID in dockerfile
    assert f"python@sha256:{PYTHON_BASE}" in dockerfile
    assert f"cc-debian12@sha256:{DISTROLESS_BASE}" in dockerfile
    assert "latest" not in dockerfile


def test_distroless_candidate_replaces_only_the_scanner_blocking_python_package() -> None:
    dockerfile = (EXPERIMENT / "Dockerfile").read_text(encoding="utf-8")

    assert "cryptography-50.0.1-cp311-abi3-manylinux_2_34_x86_64.whl" in dockerfile
    assert CRYPTOGRAPHY_WHEEL in dockerfile
    assert "--no-index --no-deps --force-reinstall" in dockerfile
    assert "m.version('cryptography') == '50.0.1'" in dockerfile


def test_distroless_candidate_copies_only_two_explicit_system_libraries() -> None:
    dockerfile = (EXPERIMENT / "Dockerfile").read_text(encoding="utf-8")
    library_copies = [
        line for line in dockerfile.splitlines() if "/usr/lib/x86_64-linux-gnu/" in line
    ]

    assert len(library_copies) == 2
    assert any("libffi.so.8*" in line for line in library_copies)
    assert any("libz.so.1*" in line for line in library_copies)
    assert not any("/usr/lib/x86_64-linux-gnu/*" in line for line in library_copies)


def test_distroless_candidate_has_no_network_package_or_shell_runtime_step() -> None:
    dockerfile = (EXPERIMENT / "Dockerfile").read_text(encoding="utf-8")

    for forbidden in ("apt-get", "apk add", "curl ", "wget "):
        assert forbidden not in dockerfile
    assert 'ENTRYPOINT ["/usr/local/bin/python"]' in dockerfile
    assert "USER 65532:65532" in dockerfile
