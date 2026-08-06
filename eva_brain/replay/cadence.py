#!/usr/bin/env python3
"""Real autonomous cadence + per-day task/distractor structure + why-failed probe."""
import sqlite3, re, sys, statistics
from datetime import datetime

DB = sys.argv[1] if len(sys.argv) > 1 else "/tmp/volharding01_ro/eva.db"
con = sqlite3.connect(DB)
def q(sql, a=()):
    return con.execute(sql, a).fetchall()

def parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))

# tick cadence from vaardigheid (autonomous actions)
vs = [parse(r[0]) for r in q("SELECT ts FROM timeline WHERE kind='vaardigheid' ORDER BY ts")]
gaps = [(vs[i+1]-vs[i]).total_seconds() for i in range(len(vs)-1) if 0 < (vs[i+1]-vs[i]).total_seconds() < 3600]
print("vaardigheid ticks: n=%d, median gap=%.0fs, mean=%.0fs" % (len(vs), statistics.median(gaps), statistics.mean(gaps)))

# heartbeat cadence
hb = [parse(r[0]) for r in q("SELECT ts FROM timeline WHERE kind='journal' ORDER BY ts")]
hg = [(hb[i+1]-hb[i]).total_seconds() for i in range(len(hb)-1) if 0 < (hb[i+1]-hb[i]).total_seconds() < 7200]
print("journal ticks: n=%d, median gap=%.0fs" % (len(hb), statistics.median(hg) if hg else -1))

BOUW = re.compile(r"\b(bouw|maak|schrijf|code|coden|programmeer|installeer|repareer|fix|automatiseer|script|regel|zorg|doe|los|zet|pak|check|kijk|stuur|onthoud|noteer|herinner|afmaken|af)\b", re.I)
users = q("SELECT ts, sender, content FROM messages WHERE role='user' ORDER BY ts")
tasks = [(u[0], u[1], u[2]) for u in users if BOUW.search(u[2] or "")]

# per-day structure
from collections import defaultdict
day_tasks = defaultdict(int)
for t in tasks:
    day_tasks[t[0][:10]] += 1
day_dist = defaultdict(int)
for r in q("SELECT ts FROM timeline WHERE kind IN ('chat_in','house','journal','innerlijk') ORDER BY ts"):
    day_dist[r[0][:10]] += 1
print("\n=== per-day: task-requests / distractor-events ===")
for d in sorted(set(list(day_tasks)+list(day_dist))):
    print(d, "tasks=%2d" % day_tasks.get(d,0), "distractors=%4d" % day_dist.get(d,0))

print("\ntotal task-requests:", len(tasks))
print("days with >=1 task:", sum(1 for d in day_tasks if day_tasks[d]>=1))

con.close()
