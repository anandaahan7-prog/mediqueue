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

A patient who stepped away (from their own page) is like a process in the blocked state:
still in the queue, keeping their place and their waiting time, but never dispatched until
they press "I'm back" or their time away runs out (a sleep with a timeout).
"""

from db import log_event, minutes_between, now, parse, setting, stamp
from scheduler import AGING_MINUTES, pick_before, pick_next, preemption_target, queue_key, reserve_doctor


def queue_policy(conn):
    """Admin can switch between risk-adaptive aging (default) and the same interval for everyone."""
    return "mediqueue" if setting(conn, "aging_mode", "adaptive") == "adaptive" else "mediqueue_fixed"


def as_job(row):
    """Convert a visits row into the dict the scheduler works with (times in minutes)."""
    done = row["consult_done_min"] or 0
    if row["status"] == "in_consult" and row["consult_started_at"]:
        done += minutes_between(row["consult_started_at"])
    return {
        "id": row["visit_id"],
        "arrived": parse(row["arrived_at"]).timestamp() / 60,
        "esi": row["esi"],
        "risk": row["ai_risk"] or 0.0,
        "predicted": row["predicted_minutes"],
        "consult_done": done,
    }


def is_away(row, at=None):
    """Stepped away and not back yet."""
    return bool(row["away_until"]) and row["away_until"] > (at or stamp())


def now_minutes():
    # same naive-IST convention as the stored timestamps, so differences are always correct
    return now().timestamp() / 60


class DispatchError(Exception):
    pass


class RoomHeld(DispatchError):
    """The doctor's room is held for an ambulance and nobody waiting would be finished in time."""


def minutes_of(text):
    return parse(text).timestamp() / 60


def held_alert(conn, doctor_id):
    """The incoming ambulance this doctor's room is held for, if any."""
    return conn.execute("SELECT * FROM prealerts WHERE doctor_id = ? AND status = 'incoming' ORDER BY eta_at",
                        (doctor_id,)).fetchone()


def hold_room(conn, alert_id):
    """Real-time reservation for an ESI 1-2 ambulance: choose whose room to hold (best fit before the ETA).
    Runs inside the caller's transaction."""
    alert = conn.execute("SELECT * FROM prealerts WHERE alert_id = ?", (alert_id,)).fetchone()
    if alert is None or alert["status"] != "incoming" or alert["esi"] > 2 or setting(conn, "hold_rooms", 1) != 1:
        conn.execute("UPDATE prealerts SET doctor_id = NULL WHERE alert_id = ?", (alert_id,))
        return None
    t = now_minutes()
    free_at = {}
    for d in conn.execute("SELECT user_id FROM users WHERE role = 'doctor' AND on_duty = 1").fetchall():
        cur = _current_patient(conn, d["user_id"])
        if cur is None:
            free_at[d["user_id"]] = t
        else:
            job = as_job(cur)
            free_at[d["user_id"]] = t + max(1, job["predicted"] - job["consult_done"])
    taken = {r["doctor_id"] for r in conn.execute(
        "SELECT doctor_id FROM prealerts WHERE status = 'incoming' AND doctor_id IS NOT NULL AND alert_id <> ?",
        (alert_id,)).fetchall()}
    doc = reserve_doctor(free_at, minutes_of(alert["eta_at"]), taken)
    conn.execute("UPDATE prealerts SET doctor_id = ? WHERE alert_id = ?", (doc, alert_id))
    return doc


def rehold_rooms(conn, doctor_id):
    """A doctor went off duty: move any room they were holding to someone else."""
    for a in conn.execute("SELECT alert_id FROM prealerts WHERE doctor_id = ? AND status = 'incoming'",
                          (doctor_id,)).fetchall():
        hold_room(conn, a["alert_id"])


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


def call_next(conn, doctor_id, skipped=None):
    """Assign the next patient to this doctor. If `skipped` is a list, the tokens of patients who would
    have come first but have stepped away are added to it."""
    aging = setting(conn, "aging_minutes", AGING_MINUTES)
    policy = queue_policy(conn)
    conn.execute("BEGIN IMMEDIATE")  # ---- enter critical section ----
    try:
        if _current_patient(conn, doctor_id):
            raise DispatchError("Finish or pause your current patient first.")
        everyone = conn.execute("SELECT * FROM visits WHERE status = 'waiting'").fetchall()
        rows = [r for r in everyone if not is_away(r)]  # blocked processes are not dispatched
        away = [r for r in everyone if is_away(r)]
        jobs = [as_job(r) for r in rows]
        mine = [r for r in rows if r["reserved_for"] == doctor_id and r["ambulance"]]
        alert = held_alert(conn, doctor_id)
        note = ""
        if mine:  # the ambulance we held the room for has arrived
            nxt = {"id": min(mine, key=lambda r: r["arrived_at"])["visit_id"]}
            note = "ambulance patient, room was held"
        elif alert is not None:
            # deadline-aware: only someone who will be finished before the ambulance arrives
            nxt = pick_before(jobs, now_minutes(), minutes_of(alert["eta_at"]), policy, aging)
            if nxt is None:
                eta = max(0, round(minutes_of(alert["eta_at"]) - now_minutes()))
                raise RoomHeld(f"Your room is held for ambulance {alert['code']} (ESI {alert['esi']}), arriving in "
                               f"about {eta} min. Nobody waiting would be finished before then.")
            note = f"fits before ambulance {alert['code']} arrives"
        else:
            nxt = pick_next(jobs, now_minutes(), policy, aging)
        if skipped is not None and away:
            t = now_minutes()
            if nxt is None:
                passed = away
            else:
                mine_key = queue_key(next(j for j in jobs if j["id"] == nxt["id"]), t, policy, aging)
                passed = [r for r in away if queue_key(as_job(r), t, policy, aging) < mine_key]
            skipped.extend(r["token"] for r in sorted(passed, key=lambda r: queue_key(as_job(r), t, policy, aging)))
        if nxt is None:
            conn.execute("COMMIT")
            return None
        if skipped:
            note = (note + "; " if note else "") + f"skipped {', '.join(skipped)} (stepped away)"
        _assign(conn, nxt["id"], doctor_id, doctor_id, note)
        conn.execute("COMMIT")  # ---- leave critical section ----
        return nxt["id"]
    except Exception:
        conn.execute("ROLLBACK")
        raise


def hand_over(conn, visit_id, doctor_id, user_id):
    """An ambulance arrived: if the doctor holding its room is on duty and free, assign it straight away.
    Runs inside the caller's transaction; returns True when the patient went straight in."""
    on_duty = conn.execute("SELECT 1 FROM users WHERE user_id = ? AND on_duty = 1", (doctor_id,)).fetchone()
    if not on_duty or _current_patient(conn, doctor_id):
        return False
    _assign(conn, visit_id, doctor_id, user_id, "ambulance arrived, taken straight to the held room")
    return True


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
