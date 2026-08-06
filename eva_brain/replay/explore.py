#!/usr/bin/env python3
"""EVA_VOLHARDING_01 — data exploration (read-only). Prints candidate task-request
counts and the real interruption cadence. No writes to any live DB."""
import sqlite3, re, sys

DB = sys.argv[1] if len(sys.argv) > 1 else "/tmp/volharding01_ro/eva.db"
con = sqlite3.connect(DB)
def q(sql, a=()):
    return con.execute(sql, a).fetchall()

print("=== messages role/sender ===")
for r in q("SELECT role, sender, COUNT(*) FROM messages GROUP BY role, sender ORDER BY 3 DESC"):
    print(r)

BOUW = re.compile(
    r"\b(bouw|maak|schrijf|code|coden|programmeer|installeer|repareer|fix|"
    r"automatiseer|script|regel|zorg|doe|los|zet|pak|check|kijk|stuur|onthoud|"
    r"noteer|herinner|afmaken|af)\b", re.I)

users = q("SELECT ts, sender, content FROM messages WHERE role='user' ORDER BY ts")
print("\n=== total user messages: %d ===" % len(users))
cmd = [u for u in users if BOUW.search(u[2] or "")]
print("=== user msgs matching command/task hint: %d ===" % len(cmd))
for u in cmd[:40]:
    print(u[0][:19], u[1], repr((u[2] or "")[:80]))

# Real interruption cadence: gaps between consecutive incoming stimuli (chat_in + house)
print("\n=== interruption cadence (timeline chat_in/house/reminder) ===")
rows = q("SELECT ts, kind FROM timeline WHERE kind IN ('chat_in','house','reminder') ORDER BY ts")
print("count incoming stimuli:", len(rows))

con.close()
