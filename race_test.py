"""
Concurrency stress test - shows WHY the critical sections in dispatch.py and beds.py exist.

Many threads (= doctors pressing "Call next" / admitting patients at the same moment) hit a
scratch copy of the database. We run each scenario twice:

  unsafe - read, think for a few milliseconds, then write (no lock, no re-check)
  safe   - the same work inside BEGIN IMMEDIATE ... COMMIT with a conditional UPDATE

and count how often the same patient went to two doctors, or the same bed to two patients.

    python race_test.py
"""

import os
import random
import sqlite3
import tempfile
import threading
import time

SCRATCH_SCHEMA = """
CREATE TABLE visits (visit_id INTEGER PRIMARY KEY, esi INTEGER, arrived REAL, status TEXT, doctor_id INTEGER);
CREATE TABLE assignments (visit_id INTEGER, doctor_id INTEGER);
CREATE TABLE beds (bed_id INTEGER PRIMARY KEY, status TEXT, visit_id INTEGER);
CREATE TABLE bed_log (bed_id INTEGER, visit_id INTEGER);
"""


def _connect(path):
    conn = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _fresh_db(patients, beds):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = _connect(path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCRATCH_SCHEMA)
    for i in range(patients):
        conn.execute("INSERT INTO visits VALUES (?, ?, ?, 'waiting', NULL)", (i + 1, random.randint(1, 5), i))
    for b in range(beds):
        conn.execute("INSERT INTO beds VALUES (?, 'free', NULL)", (b + 1,))
    conn.close()
    return path


def _think():
    # the gap between reading and writing: request parsing, network latency, another thread
    time.sleep(random.uniform(0.0005, 0.004))


def _call_next(conn, doctor_id, safe):
    if safe:
        conn.execute("BEGIN IMMEDIATE")
    row = conn.execute("SELECT visit_id FROM visits WHERE status = 'waiting' "
                       "ORDER BY esi, arrived LIMIT 1").fetchone()
    if row is None:
        if safe:
            conn.execute("COMMIT")
        return False
    _think()
    if safe:
        cur = conn.execute("UPDATE visits SET status = 'in_consult', doctor_id = ? "
                           "WHERE visit_id = ? AND status = 'waiting'", (doctor_id, row[0]))
        if cur.rowcount == 1:
            conn.execute("INSERT INTO assignments VALUES (?, ?)", (row[0], doctor_id))
        conn.execute("COMMIT")
    else:
        conn.execute("UPDATE visits SET status = 'in_consult', doctor_id = ? WHERE visit_id = ?", (doctor_id, row[0]))
        conn.execute("INSERT INTO assignments VALUES (?, ?)", (row[0], doctor_id))
    return True


def _admit(conn, visit_id, safe):
    if safe:
        conn.execute("BEGIN IMMEDIATE")
    row = conn.execute("SELECT bed_id FROM beds WHERE status = 'free' ORDER BY bed_id LIMIT 1").fetchone()
    if row is None:
        if safe:
            conn.execute("COMMIT")
        return False
    _think()
    if safe:
        cur = conn.execute("UPDATE beds SET status = 'occupied', visit_id = ? WHERE bed_id = ? AND status = 'free'",
                           (visit_id, row[0]))
        if cur.rowcount == 1:
            conn.execute("INSERT INTO bed_log VALUES (?, ?)", (row[0], visit_id))
        conn.execute("COMMIT")
    else:
        conn.execute("UPDATE beds SET status = 'occupied', visit_id = ? WHERE bed_id = ?", (visit_id, row[0]))
        conn.execute("INSERT INTO bed_log VALUES (?, ?)", (row[0], visit_id))
    return True


def _run_threads(path, workers, job):
    barrier = threading.Barrier(workers)
    timings = []
    lock = threading.Lock()

    def worker(i):
        conn = _connect(path)
        barrier.wait()  # everybody presses the button at the same instant
        start = time.perf_counter()
        job(conn, i)
        with lock:
            timings.append((time.perf_counter() - start) * 1000)
        conn.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return timings


def dispatch_race(safe, doctors=6, trials=30):
    conflicts, timings = 0, []
    for _ in range(trials):
        path = _fresh_db(patients=doctors, beds=0)
        timings += _run_threads(path, doctors, lambda conn, i: _call_next(conn, i + 1, safe))
        conn = _connect(path)
        conflicts += conn.execute("SELECT COUNT(*) FROM (SELECT visit_id FROM assignments "
                                  "GROUP BY visit_id HAVING COUNT(*) > 1)").fetchone()[0]
        conn.close()
        os.remove(path)
    return {"trials": trials, "workers": doctors, "conflicts": conflicts,
            "conflict_rate": conflicts / trials, "avg_ms": sum(timings) / len(timings), "max_ms": max(timings)}


def bed_race(safe, beds=3, requests=8, trials=30):
    conflicts, overbooked, timings = 0, 0, []
    for _ in range(trials):
        path = _fresh_db(patients=0, beds=beds)
        timings += _run_threads(path, requests, lambda conn, i: _admit(conn, 100 + i, safe))
        conn = _connect(path)
        doubled = conn.execute("SELECT COUNT(*) FROM (SELECT bed_id FROM bed_log "
                               "GROUP BY bed_id HAVING COUNT(*) > 1)").fetchone()[0]
        admitted = conn.execute("SELECT COUNT(*) FROM bed_log").fetchone()[0]
        conflicts += doubled
        overbooked += max(0, admitted - beds)
        conn.close()
        os.remove(path)
    return {"trials": trials, "workers": requests, "beds": beds, "conflicts": conflicts,
            "patients_without_real_bed": overbooked, "avg_ms": sum(timings) / len(timings), "max_ms": max(timings)}


def run_all(trials=30):
    return {
        "dispatch": {"unsafe": dispatch_race(False, trials=trials), "safe": dispatch_race(True, trials=trials)},
        "beds": {"unsafe": bed_race(False, trials=trials), "safe": bed_race(True, trials=trials)},
    }


if __name__ == "__main__":
    random.seed(1)
    res = run_all()
    for scenario, pair in res.items():
        for mode, r in pair.items():
            print(f"{scenario:8s} {mode:6s} conflicts {r['conflicts']:3d} in {r['trials']} trials   "
                  f"avg {r['avg_ms']:.1f} ms  max {r['max_ms']:.1f} ms"
                  + (f"   patients sharing a bed {r['patients_without_real_bed']}" if scenario == 'beds' else ""))
