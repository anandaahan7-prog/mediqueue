"""
OS concept 2 - the observation beds are a counting semaphore.

    value of the semaphore  = number of beds with status 'free'
    wait()  / P             = a doctor admits a patient -> take a free bed, or join the
                              boarding queue (the semaphore's blocked queue) if there is none
    signal() / V            = housekeeping marks a bed clean -> give it straight to the most
                              urgent boarding patient, or put it back in the free pool

Both operations must run while holding the database write lock (BEGIN IMMEDIATE), otherwise
two admissions at the same moment could both see "1 bed free" and take it.
wait_bed() is called from inside dispatch.finish(), which already holds the lock.
"""

from db import log_event, stamp


def semaphore_value(conn):
    return conn.execute("SELECT COUNT(*) FROM beds WHERE status = 'free'").fetchone()[0]


def wait_bed(conn, visit_id, user_id=None):
    """P(beds). Caller must already be inside BEGIN IMMEDIATE. Returns the bed label or None."""
    bed = conn.execute("SELECT bed_id, label FROM beds WHERE status = 'free' ORDER BY bed_id LIMIT 1").fetchone()
    if bed is None:
        conn.execute("UPDATE visits SET status = 'boarding' WHERE visit_id = ?", (visit_id,))
        log_event(conn, "boarding", "no free bed - waiting in the boarding queue", visit_id, user_id)
        return None
    cur = conn.execute("UPDATE beds SET status = 'occupied', visit_id = ?, updated_at = ? "
                       "WHERE bed_id = ? AND status = 'free'", (visit_id, stamp(), bed["bed_id"]))
    assert cur.rowcount == 1, "bed vanished inside the critical section"
    conn.execute("UPDATE visits SET status = 'admitted' WHERE visit_id = ?", (visit_id,))
    log_event(conn, "bed", f"admitted to {bed['label']}", visit_id, user_id)
    return bed["label"]


def _next_boarding(conn):
    # most urgent first, then whoever has been waiting for a bed the longest
    return conn.execute("SELECT * FROM visits WHERE status = 'boarding' "
                        "ORDER BY esi, finished_at LIMIT 1").fetchone()


def release_bed(conn, bed_id, user_id=None):
    """Patient moved to a ward or went home: the bed needs cleaning before it can be reused."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        bed = conn.execute("SELECT * FROM beds WHERE bed_id = ?", (bed_id,)).fetchone()
        if bed is None or bed["status"] != "occupied":
            conn.execute("ROLLBACK")
            return False
        conn.execute("UPDATE visits SET status = 'discharged' WHERE visit_id = ?", (bed["visit_id"],))
        conn.execute("UPDATE beds SET status = 'cleaning', visit_id = NULL, updated_at = ? WHERE bed_id = ?",
                     (stamp(), bed_id))
        log_event(conn, "bed_released", f"{bed['label']} released, sent for cleaning", bed["visit_id"], user_id)
        conn.execute("COMMIT")
        return True
    except Exception:
        conn.execute("ROLLBACK")
        raise


def signal_bed(conn, bed_id, user_id=None):
    """V(beds): bed is clean. Returns the visit_id that got it, or None if it went back to the pool."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        bed = conn.execute("SELECT * FROM beds WHERE bed_id = ?", (bed_id,)).fetchone()
        if bed is None or bed["status"] != "cleaning":
            conn.execute("ROLLBACK")
            return None
        waiting = _next_boarding(conn)
        if waiting:
            conn.execute("UPDATE beds SET status = 'occupied', visit_id = ?, updated_at = ? WHERE bed_id = ?",
                         (waiting["visit_id"], stamp(), bed_id))
            conn.execute("UPDATE visits SET status = 'admitted' WHERE visit_id = ?", (waiting["visit_id"],))
            log_event(conn, "bed", f"{bed['label']} handed straight to boarding patient", waiting["visit_id"], user_id)
            result = waiting["visit_id"]
        else:
            conn.execute("UPDATE beds SET status = 'free', updated_at = ? WHERE bed_id = ?", (stamp(), bed_id))
            log_event(conn, "bed_free", f"{bed['label']} is free", None, user_id)
            result = None
        conn.execute("COMMIT")
        return result
    except Exception:
        conn.execute("ROLLBACK")
        raise
