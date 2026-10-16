"""
The dispatcher - where scheduling (scheduler.py) meets synchronisation.

Several doctors press "Call next patient" from different computers. Each press is a
separate request on its own thread, so the "read the queue, choose, mark as taken"
sequence is a critical section: if two doctors ran it at the same moment they could both
pick the same patient.

We protect it with SQLite's write lock. BEGIN IMMEDIATE acquires the lock before we read
the queue (that's our wait()/P on a binary semaphore) and COMMIT releases it (signal()/V).
The UPDATE also re-checks `status = 'waiting'`, so even a bug elsewhere cannot assign a
patient twice - the second update would simply change 0 rows.
"""

from db import log_event, minutes_between, now, parse, setting, stamp
from scheduler import AGING_MINUTES, pick_next, preemption_target


def as_job(row):
    """Convert a visits row into the dict the scheduler works with (times in minutes)."""
    done = row["consult_done_min"] or 0
    if row["status"] == "in_consult" and row["consult_started_at"]:
        done += minutes_between(row["consult_started_at"])
    return {
        "id": row["visit_id"],
        "arrived": parse(row["arrived_at"]).timestamp() / 60,
        "esi": row["esi"],
        "predicted": row["predicted_minutes"],
        "consult_done": done,
    }


def now_minutes():
    # same naive-IST convention as the stored timestamps, so differences are always correct
    return now().timestamp() / 60


class DispatchError(Exception):
    pass


def _current_patient(conn, doctor_id):
    return conn.execute("SELECT * FROM visits WHERE doctor_id = ? AND status = 'in_consult'",
                        (doctor_id,)).fetchone()


def _assign(conn, visit_id, doctor_id, user_id, note=""):
    ts = stamp()
    cur = conn.execute(
        """UPDATE visits SET status = 'in_consult', doctor_id = ?, consult_started_at = ?,
                             first_seen_at = COALESCE(first_seen_at, ?)
           WHERE visit_id = ? AND status = 'waiting'""",
        (doctor_id, ts, ts, visit_id))
    if cur.rowcount != 1:
        raise DispatchError("Patient was already taken")  # cannot happen while we hold the lock
    log_event(conn, "called", note or "called by doctor", visit_id, user_id)


def call_next(conn, doctor_id):
    aging = setting(conn, "aging_minutes", AGING_MINUTES)
    conn.execute("BEGIN IMMEDIATE")  # ---- enter critical section ----
    try:
        if _current_patient(conn, doctor_id):
            raise DispatchError("Finish or pause your current patient first.")
        rows = conn.execute("SELECT * FROM visits WHERE status = 'waiting'").fetchall()
        nxt = pick_next([as_job(r) for r in rows], now_minutes(), "mediqueue", aging)
        if nxt is None:
            conn.execute("COMMIT")
            return None
        _assign(conn, nxt["id"], doctor_id, doctor_id)
        conn.execute("COMMIT")  # ---- leave critical section ----
        return nxt["id"]
    except Exception:
        conn.execute("ROLLBACK")
        raise


def preempt_for_critical(conn, doctor_id):
    """ESI 1 only: put the doctor's current (ESI 3-5) patient back in the queue and call the critical one."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        current = _current_patient(conn, doctor_id)
        waiting = conn.execute("SELECT * FROM visits WHERE status = 'waiting'").fetchall()
        critical = [r for r in waiting if r["esi"] == 1]
        if not critical:
            raise DispatchError("No ESI 1 patient is waiting any more.")
        if current is not None:
            if current["esi"] <= 2:
                raise DispatchError("Your current patient is ESI 1-2; ask another doctor or call backup.")
            done = (current["consult_done_min"] or 0) + minutes_between(current["consult_started_at"])
            conn.execute(
                """UPDATE visits SET status = 'waiting', doctor_id = NULL, consult_started_at = NULL,
                                     consult_done_min = ?, paused_count = paused_count + 1
                   WHERE visit_id = ?""", (done, current["visit_id"]))
            log_event(conn, "paused", f"paused after {done:.0f} min for a resuscitation case",
                      current["visit_id"], doctor_id)
        target = min(critical, key=lambda r: r["arrived_at"])
        _assign(conn, target["visit_id"], doctor_id, doctor_id, "called for resuscitation (preemption)")
        conn.execute("COMMIT")
        return target["visit_id"]
    except Exception:
        conn.execute("ROLLBACK")
        raise


def finish(conn, doctor_id, outcome):
    """outcome = 'discharged' or 'admit' (needs an observation bed -> beds.wait_bed)."""
    from beds import wait_bed
    conn.execute("BEGIN IMMEDIATE")
    try:
        current = _current_patient(conn, doctor_id)
        if current is None:
            raise DispatchError("You have no patient right now.")
        done = (current["consult_done_min"] or 0) + minutes_between(current["consult_started_at"])
        conn.execute("UPDATE visits SET consult_done_min = ?, finished_at = ? WHERE visit_id = ?",
                     (done, stamp(), current["visit_id"]))
        if outcome == "admit":
            bed = wait_bed(conn, current["visit_id"], doctor_id)
            message = f"admitted to {bed}" if bed else "no bed free - added to the boarding queue"
        else:
            conn.execute("UPDATE visits SET status = 'discharged' WHERE visit_id = ?", (current["visit_id"],))
            log_event(conn, "discharged", f"consultation took {done:.0f} min", current["visit_id"], doctor_id)
            message = "discharged"
        conn.execute("COMMIT")
        return message
    except Exception:
        conn.execute("ROLLBACK")
        raise


def who_should_preempt(conn):
    """Doctor id that the console should alert about a waiting ESI 1 patient (or None)."""
    waiting = conn.execute("SELECT * FROM visits WHERE status = 'waiting'").fetchall()
    if not any(r["esi"] == 1 for r in waiting):
        return None
    on_duty = conn.execute("SELECT user_id FROM users WHERE role = 'doctor' AND on_duty = 1").fetchall()
    busy_rows = conn.execute("SELECT * FROM visits WHERE status = 'in_consult'").fetchall()
    busy_ids = {r["doctor_id"] for r in busy_rows}
    if any(d["user_id"] not in busy_ids for d in on_duty):
        return None  # a free doctor will simply call them next
    busy = [(r["doctor_id"], as_job(r)) for r in busy_rows]
    return preemption_target([as_job(r) for r in waiting], busy)
