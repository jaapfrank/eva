"""Memory verification compares content without applying archived SQL."""
from contextlib import contextmanager
import hashlib
import sys
import pytest
from tools import installation_memory_verification as module

HEADER = "-- Dumped from database version 16.13\n-- Dumped by pg_dump version 16.13\nSET client_encoding = 'UTF8';\n"

def dump(rows="1\tshared\n2\tprivate\n"):
    return (HEADER + "COPY public.memories (id, content) FROM stdin;\n" + rows + "\\.\nSELECT pg_catalog.setval('public.memories_id_seq', 2, true);\n").encode()


def test_inventory_ignores_row_order_but_preserves_duplicates_columns_and_sequence():
    original = module.data_inventory(dump())
    assert original == module.data_inventory(dump("2\tprivate\n1\tshared\n"))
    for changed in (dump("1\tchanged\n2\tprivate\n"), dump("1\tshared\n1\tshared\n"),
                    dump().replace(b'(id, content)', b'(id, text)'),
                    dump().replace(b"_seq', 2", b"_seq', 3")):
        assert module.data_inventory(changed) != original


@pytest.mark.parametrize("raw", [dump() + b"INSERT INTO public.other VALUES (1);\n",
    dump().replace(b'\\.\n', b''), dump() + b"SELECT pg_catalog.lo_create('123');\n",
    dump().replace(b'16.13', b'17.1')])
def test_unsupported_or_incomplete_data_fails_closed(raw):
    with pytest.raises(ValueError): module.data_inventory(raw)


def test_capture_bounds_output_and_does_not_include_data_in_errors(monkeypatch):
    monkeypatch.setattr(module, 'MAX_DATA_BYTES', 32)
    with pytest.raises(RuntimeError, match='exceeds bound') as error:
        module._capture_data([sys.executable, '-c', "print('SECRET' * 20)"])
    assert 'SECRET' not in str(error.value)
    assert module._capture_data([sys.executable, '-c', 'import sys;sys.stdout.buffer.write(sys.stdin.buffer.read())'], raw=b'ok') == b'ok'


@pytest.mark.parametrize('changed', [False, True])
def test_verifier_binds_archive_and_target_and_never_restores(monkeypatch, tmp_path, changed):
    source = b'authenticated archive fixture'
    package = {'archive_sha256': 'a'*64, 'manifest': {'files': {'memory/hindsight.dump':
        {'size': len(source), 'sha256': hashlib.sha256(source).hexdigest()}}}}
    config = {'memory': {'container': {'id': 'b'*64}, 'user': 'reader', 'database': {'name': 'memory'}}, 'writers': [{'id': 'running-gateway'}]}
    calls = []; guards = []
    monkeypatch.setattr(module, 'validate_external_configuration', lambda c: None)
    monkeypatch.setattr(module, 'read_installation_package', lambda p: package)
    @contextmanager
    def descriptor(p): yield 1
    monkeypatch.setattr(module, '_root_descriptor', descriptor)
    monkeypatch.setattr(module, '_read_regular', lambda *a, **kw: source)
    def memory_pin_only(c):
        assert c['memory'] == config['memory']
        assert c['writers'] == []
        assert config['writers'] == [{'id': 'running-gateway'}]
        guards.append('pins')
    monkeypatch.setattr(module, '_pins_current', memory_pin_only)
    monkeypatch.setattr(module, '_database_current', lambda c, **kw: guards.append('lineage'))
    def capture(argv, **kwargs):
        calls.append(argv)
        if 'pg_restore' in argv:
            assert kwargs['raw'] == source and '--file=-' in argv and '--dbname' not in argv
            return dump()
        assert 'pg_dump' in argv and '--dbname' in argv
        return dump('1\twrong\n') if changed else dump()
    monkeypatch.setattr(module, '_capture_data', capture)
    invoke = lambda: module.verify_memory_data(tmp_path, configuration=config, guard=lambda: guards.append('guard'))
    if changed:
        with pytest.raises(ValueError, match='differs from archive'): invoke()
    else:
        receipt = invoke()
        assert receipt['table_count'] == 1 and receipt['archive_sha256'] == package['archive_sha256']
        assert guards == ['guard', 'pins', 'lineage', 'guard', 'pins', 'lineage']
    assert len(calls) == 2
