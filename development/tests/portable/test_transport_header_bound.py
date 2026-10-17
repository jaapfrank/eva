from __future__ import annotations

from pathlib import Path
import struct

import pytest

from tools import portable_transport as transport


class _ReadProbe:
    def __init__(self, stream, reads):
        self.stream = stream
        self.reads = reads

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.stream.__exit__(*args)

    def read(self, size=-1):
        # The maximum uint32 test must never allocate an attacker-sized buffer.
        assert 0 <= size <= transport.MAX_HEADER_BYTES
        raw = self.stream.read(size)
        self.reads.append((size, len(raw)))
        return raw

    def __getattr__(self, name):
        return getattr(self.stream, name)


def _read_probe(monkeypatch, source):
    reads = []
    original_open = Path.open

    def observed_open(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        return _ReadProbe(stream, reads) if path == source else stream

    monkeypatch.setattr(Path, "open", observed_open)
    return reads


@pytest.mark.parametrize("length", [0, transport.MAX_HEADER_BYTES + 1, 0xFFFFFFFF])
def test_invalid_declared_header_length_is_rejected_before_body_read(tmp_path, monkeypatch, length):
    source, destination = tmp_path / "synthetic.eva2pt", tmp_path / "plaintext"
    source.write_bytes(transport.MAGIC + struct.pack(">I", length) + b"unread synthetic body")
    reads = _read_probe(monkeypatch, source)
    with pytest.raises(transport.PortableTransportError, match="^transport header length is invalid$"):
        transport._decrypt_to_file(source, destination, passphrase="synthetic passphrase")
    assert reads == [(len(transport.MAGIC), len(transport.MAGIC)), (4, 4)]
    assert not destination.exists()


@pytest.mark.parametrize("length", [1, transport.MAX_HEADER_BYTES])
def test_admitted_header_lengths_still_reach_existing_json_validation(tmp_path, monkeypatch, length):
    source, destination = tmp_path / "synthetic.eva2pt", tmp_path / "plaintext"
    source.write_bytes(transport.MAGIC + struct.pack(">I", length) + b"{" * length)
    reads = _read_probe(monkeypatch, source)
    with pytest.raises(transport.PortableTransportError, match="^transport header is not valid JSON$"):
        transport._decrypt_to_file(source, destination, passphrase="synthetic passphrase")
    assert reads[-1] == (length, length)
    assert sum(returned for _, returned in reads) == len(transport.MAGIC) + 4 + length
    assert not destination.exists()


@pytest.mark.parametrize("available_length_bytes", [0, 1, 2, 3])
def test_truncated_length_keeps_existing_error_and_does_not_read_header(tmp_path, monkeypatch, available_length_bytes):
    source, destination = tmp_path / "synthetic.eva2pt", tmp_path / "plaintext"
    source.write_bytes(transport.MAGIC + b"\x00" * available_length_bytes)
    reads = _read_probe(monkeypatch, source)
    with pytest.raises(transport.PortableTransportError, match="^transport header is truncated$"):
        transport._decrypt_to_file(source, destination, passphrase="synthetic passphrase")
    assert reads == [(len(transport.MAGIC), len(transport.MAGIC)), (4, available_length_bytes)]
    assert not destination.exists()


def test_valid_header_keeps_existing_ciphertext_length_check(tmp_path, monkeypatch):
    header = transport._header(salt=b"s" * transport.SALT_BYTES, nonce=b"n" * transport.NONCE_BYTES,
        plaintext_bytes=1024, plaintext_sha256="1" * 64)
    source, destination = tmp_path / "synthetic.eva2pt", tmp_path / "plaintext"
    source.write_bytes(transport.MAGIC + struct.pack(">I", len(header)) + header)
    reads = _read_probe(monkeypatch, source)
    with pytest.raises(transport.PortableTransportError, match="^transport ciphertext length differs from header$"):
        transport._decrypt_to_file(source, destination, passphrase="synthetic passphrase")
    assert reads[-1] == (len(header), len(header))
    assert not destination.exists()
