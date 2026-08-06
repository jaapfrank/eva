#!/usr/bin/env python3
"""Print real chat threads around the 'finishes nothing' complaints to see the failure mode."""
import sqlite3, sys
DB = sys.argv[1] if len(sys.argv) > 1 else "/tmp/volharding01_ro/eva.db"
lo = sys.argv[2] if len(sys.argv) > 2 else "2026-06-19T08:00"
hi = sys.argv[3] if len(sys.argv) > 3 else "2026-06-19T11:00"
con = sqlite3.connect(DB)
rows = con.execute(
    "SELECT ts, role, sender, content FROM messages WHERE ts>=? AND ts<=? ORDER BY ts",
    (lo, hi)).fetchall()
for ts, role, sender, content in rows:
    who = "JAAP" if role == "user" else "EVA "
    print(f"[{ts[11:19]}] {who}: {(content or '')[:240]}")
    print("-"*80)
con.close()
