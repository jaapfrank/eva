#!/usr/bin/env python3
"""
EVA_VOLHARDING_01 — replay-only falsification of the persist-latch primitive on REAL Eva data.
See LOCK_volharding01.md (locked before this run). READS a read-only DB copy; writes NOTHING
to any live DB, takes NO live actions. Deterministic / reproducible.

Arms (identical events + budget + stochastic task-world; differ ONLY in the latch):
  NO-PERSIST  : argmax salience each tick (current behaviour)
  PERSIST     : latch the highest open task, hold until KLAAR/VAST/safety-yield/deadline
  SHAM        : same hysteresis, WRONG binding (latch a distractor)
"""
import sqlite3, re, sys, math, random, statistics, json
from datetime import datetime, timedelta
from collections import defaultdict

DB = sys.argv[1] if len(sys.argv) > 1 else "/tmp/volharding01_ro/eva.db"
DELTA = 300.0                      # s per decision tick (real heartbeat median)
S0_TASK = 0.72                     # real: voedOpdrachtenInVeld salience
S_VOORNEMEN = 0.42                 # real: opdrachten.js "zachte voornemens 0.42"
S_CHAT = 0.50
IDLE_GAP = 1800.0                  # s; gap that ends a "session"
SEEDS = list(range(8))

# ---- Eva's real command/task regex (subset of agent.js BOUW_HINT, faithful) ----
BOUW = re.compile(
    r"\b(bouw|maak|schrijf|code|coden|programmeer|installeer|repareer|fix|automatiseer|"
    r"script|regel|zorg|doe|los|zet|pak|check|kijk|stuur|onthoud|noteer|herinner|afmaken|af)\b", re.I)
SAFETY = re.compile(r"(deur|open|heater|rook|brand|water|alarm|lek|gas|inbraak)", re.I)

def parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))

# ---- real-code norm/overlap (mirror opdrachten.js vindGelijkende) ----
def norm(s):
    return re.sub(r"\s+", " ", str(s or "").lower()).strip()
def overlap(a, b):
    aw = set(w for w in re.split(r"\W+", norm(a)) if len(w) > 3)
    bw = [w for w in re.split(r"\W+", norm(b)) if len(w) > 3]
    if not aw or not bw:
        return 0.0
    n = sum(1 for w in bw if w in aw)
    return n / max(len(aw), len(bw))

def load_events():
    con = sqlite3.connect(DB)
    q = lambda s, a=(): con.execute(s, a).fetchall()
    # tasks from user messages, refresh-merged (mirror live code)
    users = q("SELECT ts, sender, content FROM messages WHERE role='user' ORDER BY ts")
    raw = [(parse(t[0]), t[1], t[2]) for t in users if BOUW.search(t[2] or "")]
    # distractors
    dist = []
    for ts, kind, content in q(
        "SELECT ts, kind, content FROM timeline WHERE kind IN "
        "('chat_in','house','journal','innerlijk') ORDER BY ts"):
        dt = parse(ts)
        safe = (kind == 'house' and bool(SAFETY.search(content or "")))
        sal = S_CHAT if kind in ('chat_in', 'house') else S_VOORNEMEN
        dist.append((dt, sal, safe))
    con.close()
    return raw, dist

def build_scenarios(raw, dist):
    """One scenario per calendar day with >=1 task. Refresh-merge repeated requests."""
    by_day_tasks = defaultdict(list)
    for dt, sender, content in raw:
        by_day_tasks[dt.date()].append((dt, sender, content))
    by_day_dist = defaultdict(list)
    for dt, sal, safe in dist:
        by_day_dist[dt.date()].append((dt, sal, safe))

    scenarios = []
    for day in sorted(by_day_tasks):
        # refresh-merge: a request overlapping an open one (same asker, <6h) = refresh
        tasks = []
        for dt, sender, content in by_day_tasks[day]:
            merged = False
            for tk in tasks:
                if tk['sender'] == sender and overlap(tk['text'], content) >= 0.5 \
                        and (dt - tk['arrival']).total_seconds() < 6*3600 and not tk['closed']:
                    tk['refresh'].append(dt)   # re-assertion => refreshes salience later
                    merged = True
                    break
            if not merged:
                tasks.append({'arrival': dt, 'sender': sender, 'text': content,
                              'refresh': [], 'closed': False})
        scenarios.append({'day': str(day), 'tasks': tasks,
                          'dist': sorted(by_day_dist.get(day, []))})
    return scenarios

# --------------------------------------------------------------------------------------
def make_ticks(scn):
    evs = [t['arrival'] for t in scn['tasks']] + [d[0] for d in scn['dist']]
    if not evs:
        return []
    t0, t1 = min(evs), max(evs)
    ticks = []
    t = t0
    end = t1 + timedelta(seconds=DELTA*12)   # +1h tail
    while t <= end:
        ticks.append(t)
        t = t + timedelta(seconds=DELTA)
    return ticks

def deadline_ticks(arrival_idx, ticks, dist_times):
    """ticks until next idle gap>IDLE_GAP after arrival, clamped [6,48]."""
    # merge event times to find next big gap
    later = [dt for dt in dist_times if dt >= ticks[arrival_idx]]
    later_sorted = sorted(later)
    horizon = None
    prev = ticks[arrival_idx]
    for dt in later_sorted:
        if (dt - prev).total_seconds() > IDLE_GAP:
            horizon = prev
            break
        prev = dt
    if horizon is None:
        horizon = ticks[-1]
    d = int((horizon - ticks[arrival_idx]).total_seconds() // DELTA)
    return max(6, min(48, d))

def run_arm(scn, arm, seed, W, h_task, p_vast, p_noaff):
    ticks = make_ticks(scn)
    if not ticks:
        return None
    dnum = int(scn['day'].replace('-', ''))     # stable, process-independent (reproducible)
    rng = random.Random(seed * 1000003 + dnum % 1000)
    sham_rng = random.Random(seed * 2246353 + dnum)  # wrong-task picks
    dist_times = [d[0] for d in scn['dist']]
    decay_task = 0.5 ** (1.0 / h_task)

    # per-task static (seeded) world, IDENTICAL across arms (rng depends on seed+day+task idx only)
    tasks = []
    for i, tk in enumerate(scn['tasks']):
        r = random.Random(seed * 7919 + dnum * 97 + i)
        # arrival tick index
        aidx = min(range(len(ticks)), key=lambda k: abs((ticks[k]-tk['arrival']).total_seconds()))
        D = deadline_ticks(aidx, ticks, dist_times)
        tasks.append({
            'i': i, 'arrival': aidx, 'deadline': aidx + D,
            'refresh_idx': set(min(range(len(ticks)),
                               key=lambda k: abs((ticks[k]-rt).total_seconds())) for rt in tk['refresh']),
            'W': W, 'affordance': (r.random() >= p_noaff),
            'vast': (r.random() < p_vast),
            'status': 'pending', 'work': 0, 'sal': 0.0, 'active': False,
        })

    # distractor salience series per tick (max arriving distractor, decayed) + safety flags
    n = len(ticks)
    dist_sal = [0.0]*n
    safety = [False]*n
    di = 0
    dsal = 0.0
    for k in range(n):
        lo = ticks[k]; hi = ticks[k]+timedelta(seconds=DELTA)
        bumped = S_VOORNEMEN   # always an introspective voornemen present
        while di < len(scn['dist']) and scn['dist'][di][0] < hi:
            dt, sal, safe = scn['dist'][di]
            if dt >= lo:
                bumped = max(bumped, sal)
                if safe:
                    safety[k] = True
            di += 1
        dsal = max(bumped, dsal*0.5)     # distractor half-life 1 tick
        dist_sal[k] = dsal

    latched = None            # index into tasks (for persist/sham)
    loops = 0; switches = 0; wrong = 0
    prev_focus = None

    def open_tasks(k):
        return [t for t in tasks if t['status'] in ('pending', 'open') and t['arrival'] <= k <= t['deadline']]

    for k in range(n):
        # activate arrivals / refreshes
        for t in tasks:
            if t['arrival'] == k and t['status'] == 'pending':
                t['status'] = 'open'; t['sal'] = S0_TASK
            if k in t['refresh_idx'] and t['status'] == 'open':
                t['sal'] = S0_TASK
            # timeout
            if t['status'] == 'open' and k > t['deadline']:
                t['status'] = 'drop'
                if latched == t['i']:
                    latched = None

        opens = open_tasks(k)
        any_open = len(opens) > 0

        # SAFETY: all arms yield to the safety event this tick (not a wrong action)
        if safety[k]:
            # tasks not focused decay
            for t in opens:
                t['sal'] *= decay_task
            loops += 1
            prev_focus = ('safety',)
            continue

        focus = None  # ('task', i) or ('dist',)
        if arm == 'nopersist':
            cands = [('task', t['i'], t['sal']) for t in opens]
            cands.append(('dist', -1, dist_sal[k]))
            focus_c = max(cands, key=lambda c: (c[2], c[0] == 'task'))
            focus = (focus_c[0], focus_c[1])
        elif arm == 'persist':
            if latched is None or tasks[latched]['status'] != 'open':
                latched = None
                if opens:
                    best = max(opens, key=lambda t: (t['sal'], -t['arrival']))
                    latched = best['i']
            focus = ('task', latched) if latched is not None else ('dist', -1)
        elif arm == 'sham':
            # WRONG binding = hold a distractor/voornemen while tasks are open (never a task)
            focus = ('dist', -1)
        elif arm == 'sham_wrongtask':
            # same latch machinery, but binds the WRONG TASK: a seeded-RANDOM open task
            # (not the priority one, and not systematically better e.g. EDF). Holds it to
            # KLAAR/VAST/deadline, then next. Tests whether the *correct* binding matters.
            if latched is None or tasks[latched]['status'] != 'open':
                latched = None
                if opens:
                    latched = sham_rng.choice([t['i'] for t in opens])
            focus = ('task', latched) if latched is not None else ('dist', -1)

        # apply focus
        if focus[0] == 'task':
            t = tasks[focus[1]]
            t['work'] += 1
            t['sal'] = S0_TASK
            # decay the others
            for o in opens:
                if o['i'] != t['i']:
                    o['sal'] *= decay_task
            # verdicts
            if t['vast'] and t['work'] >= math.ceil(t['W']/2) and t['status'] == 'open':
                t['status'] = 'vast'
                if latched == t['i']:
                    latched = None
            elif t['affordance'] and t['work'] >= t['W'] and t['status'] == 'open':
                t['status'] = 'klaar'
                if latched == t['i']:
                    latched = None
        else:  # focused a distractor/voornemen
            for o in opens:
                o['sal'] *= decay_task
            if any_open:
                wrong += 1     # attention off an open requested task

        loops += 1
        if prev_focus != focus:
            switches += 1
        prev_focus = focus

    # finalize: still-open at end = drop
    for t in tasks:
        if t['status'] in ('pending', 'open'):
            t['status'] = 'drop'

    ntask = len(tasks)
    if ntask == 0:
        return None
    klaar = sum(1 for t in tasks if t['status'] == 'klaar')
    drop = sum(1 for t in tasks if t['status'] == 'drop')
    vast = sum(1 for t in tasks if t['status'] == 'vast')
    return {'ntask': ntask, 'completion': klaar/ntask, 'drop': drop/ntask,
            'vast': vast/ntask, 'loops': loops, 'switches': switches, 'wrong': wrong}

# --------------------------------------------------------------------------------------
def cluster_bootstrap(per_day, key_a, key_b, higher_better=True, iters=2000, seed=12345):
    """Delta = mean(a)-mean(b) across day-clusters (each day already seed-averaged).
       Returns (mean_delta, ci_low, ci_high). higher_better flips sign so 'good' is positive."""
    days = sorted(per_day.keys())
    def stat(sample_days):
        a = statistics.mean(per_day[d][key_a] for d in sample_days)
        b = statistics.mean(per_day[d][key_b] for d in sample_days)
        return (a-b) if higher_better else (b-a)
    point = stat(days)
    rng = random.Random(seed)
    boots = []
    for _ in range(iters):
        samp = [rng.choice(days) for _ in days]
        boots.append(stat(samp))
    boots.sort()
    lo = boots[int(0.025*iters)]; hi = boots[int(0.975*iters)]
    return point, lo, hi

ARMS = ['nopersist', 'persist', 'sham', 'sham_wrongtask']

def run_config(scenarios, W, h_task, p_vast, p_noaff):
    """Return per-day per-arm seed-averaged metrics."""
    arms = ARMS
    per_day = {a: {} for a in arms}
    agg = {a: defaultdict(list) for a in arms}
    for scn in scenarios:
        for a in arms:
            accum = defaultdict(list)
            for seed in SEEDS:
                r = run_arm(scn, a, seed, W, h_task, p_vast, p_noaff)
                if r is None:
                    continue
                for kk in ('completion', 'drop', 'vast', 'loops', 'switches', 'wrong', 'ntask'):
                    accum[kk].append(r[kk])
            if not accum['completion']:
                continue
            per_day[a][scn['day']] = {kk: statistics.mean(v) for kk, v in accum.items()}
    return per_day

def summarize(tag, scenarios, W, h_task, p_vast, p_noaff):
    per_day = run_config(scenarios, W, h_task, p_vast, p_noaff)
    arms = ARMS
    means = {}
    for a in arms:
        days = per_day[a]
        means[a] = {kk: statistics.mean(d[kk] for d in days.values())
                    for kk in ('completion', 'drop', 'vast', 'loops', 'switches', 'wrong', 'ntask')}
    print(f"\n===== {tag}  (W={W}, h_task={h_task}, p_vast={p_vast}, p_noaff={p_noaff}) =====")
    print(f"{'arm':10} {'compl':>7} {'drop':>7} {'vast':>7} {'loops':>7} {'switch':>7} {'wrong':>7}")
    for a in arms:
        m = means[a]
        print(f"{a:10} {m['completion']:7.3f} {m['drop']:7.3f} {m['vast']:7.3f} "
              f"{m['loops']:7.1f} {m['switches']:7.1f} {m['wrong']:7.2f}")

    # deltas + clustered CI (persist vs nopersist, persist vs sham)
    def merged(a, b, key, higher):
        pd = {}
        for d in set(per_day[a]) & set(per_day[b]):
            pd[d] = {'x': per_day[a][d][key], 'y': per_day[b][d][key]}
        return cluster_bootstrap({d: {'a': v['x'], 'b': v['y']} for d, v in pd.items()},
                                 'a', 'b', higher_better=higher)
    res = {}
    for (a, b) in [('persist', 'nopersist'), ('persist', 'sham'), ('persist', 'sham_wrongtask')]:
        cp = merged(a, b, 'completion', True)     # want persist higher
        dp = merged(a, b, 'drop', False)          # want persist lower drop
        res[(a, b)] = (cp, dp)
        print(f"  Δcompletion {a}-{b}: {cp[0]:+.3f}  CI[{cp[1]:+.3f},{cp[2]:+.3f}]  ci_low>0={cp[1]>0}")
        print(f"  Δdrop(lower) {a}-{b}: {dp[0]:+.3f}  CI[{dp[1]:+.3f},{dp[2]:+.3f}]  ci_low>0={dp[1]>0}")

    # guardrails
    g_loops = means['persist']['loops'] <= means['nopersist']['loops'] + 1e-9
    g_wrong = means['persist']['wrong'] <= means['nopersist']['wrong'] + 1e-9
    print(f"  guardrail loops(persist<=nopersist)={g_loops}  wrong(persist<=nopersist)={g_wrong}")

    # GREEN (strict): beat nopersist AND the faithful wrong-TASK sham on both metrics, guardrails hold.
    green = (res[('persist','nopersist')][0][1] > 0 and res[('persist','nopersist')][1][1] > 0 and
             res[('persist','sham_wrongtask')][0][1] > 0 and res[('persist','sham_wrongtask')][1][1] > 0 and
             g_loops and g_wrong)
    print(f"  >>> GREEN (vs nopersist AND wrong-task sham) = {green}")
    return means, res, green

# ------------------------- model-free real corroboration -------------------------
def reminder_duplication():
    con = sqlite3.connect(DB)
    rows = con.execute("SELECT id, created_ts, target, text, status FROM reminders ORDER BY created_ts").fetchall()
    con.close()
    # cluster near-identical reminders (overlap>=0.5) created within 48h => re-creations of one task
    clusters = []
    for rid, cts, target, text, status in rows:
        placed = False
        for c in clusters:
            if overlap(c['text'], text) >= 0.5:
                c['members'].append((rid, cts, status)); placed = True; break
        if not placed:
            clusters.append({'text': text, 'members': [(rid, cts, status)]})
    dup = [c for c in clusters if len(c['members']) >= 3]
    print("\n===== MODEL-FREE corroboration: reminder duplication (no-persist signature) =====")
    print(f"reminders total={len(rows)}  distinct-task clusters={len(clusters)}  "
          f"clusters re-created >=3x = {len(dup)}")
    for c in dup:
        print(f"  x{len(c['members'])}  {c['text'][:60]!r}")
    con.close if False else None

# --------------------------------------------------------------------------------------
def main():
    raw, dist = load_events()
    scenarios = build_scenarios(raw, dist)
    ntasks = sum(len(s['tasks']) for s in scenarios)
    print(f"REAL DATA: {len(scenarios)} day-scenarios, {ntasks} task-instances "
          f"(after refresh-merge), {len(dist)} distractor events")
    for s in scenarios:
        print(f"  {s['day']}: tasks={len(s['tasks'])} dist={len(s['dist'])}")

    reminder_duplication()

    # PRIMARY (base params, p_noaff=0 isolates the latch)
    print("\n################## PRIMARY (base, latch isolated) ##################")
    summarize("PRIMARY base", scenarios, W=4, h_task=2, p_vast=0.10, p_noaff=0.0)

    # SWEEP over W and h_task (p_noaff=0)
    print("\n################## SWEEP W x h_task (p_noaff=0) ##################")
    for h in (1, 2, 4):
        for W in (2, 4, 8):
            summarize(f"sweep", scenarios, W=W, h_task=h, p_vast=0.10, p_noaff=0.0)

    # AFFORDANCE CEILING sweep (honest reading)
    print("\n################## AFFORDANCE CEILING (p_noaff) ##################")
    for pna in (0.0, 0.25, 0.5):
        summarize(f"affordance p_noaff={pna}", scenarios, W=4, h_task=2, p_vast=0.10, p_noaff=pna)

if __name__ == '__main__':
    main()
