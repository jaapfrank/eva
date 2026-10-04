import sqlite3

import pytest

from benchmark.inspect_ai_v1.ev003_bottie_readback import read_person_snapshot


def test_exact_person_across_sessions_absence_and_failed_query(tmp_path):
    database = tmp_path / "eva.db"
    with sqlite3.connect(database) as db:
        db.executescript("""
            CREATE TABLE memories (id INTEGER PRIMARY KEY, person TEXT, content TEXT, archived INTEGER);
            CREATE TABLE messages (id INTEGER PRIMARY KEY, ts TEXT, channel TEXT,
                conversation_id TEXT, sender TEXT, role TEXT, content TEXT);
            INSERT INTO memories VALUES (1,'Adult','old preference',1),(2,'Adult','corrected',0),
                (3,'Child','private sentinel',0),(4,NULL,'shared',0),(5,'adult','different identity',0);
            INSERT INTO messages VALUES (1,'t','web','first','Adult','user','first turn'),
                (2,'t','web','later','Adult','user','later turn'),
                (3,'t','web','first','Child','user','private sentinel'),
                (4,'t','web','first','Eva','assistant','unbound assistant');
        """)
    before = database.read_bytes()
    result = read_person_snapshot(database, person="Adult", timeout_seconds=2)
    assert [row["id"] for row in result["memories"]] == [1, 2]
    assert [row["conversation_id"] for row in result["user_messages"]] == ["first", "later"]
    assert "private sentinel" not in str(result)
    assert database.read_bytes() == before
    absent = read_person_snapshot(database, person="Unknown", timeout_seconds=2)
    assert absent["status"] == "observed"
    assert absent["memories"] == absent["user_messages"] == []
    with pytest.raises(FileNotFoundError):
        read_person_snapshot(tmp_path / "missing.db", person="Adult", timeout_seconds=2)
    with sqlite3.connect(database) as db:
        db.execute("DROP TABLE memories")
    with pytest.raises(sqlite3.OperationalError):
        read_person_snapshot(database, person="Adult", timeout_seconds=2)
