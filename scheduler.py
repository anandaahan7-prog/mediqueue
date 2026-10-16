"""
OS concept 1 - CPU scheduling, applied to "which patient does the next free doctor see?"

Doctors are the processors, waiting patients are the ready queue. The policy is a
multilevel priority queue (one level per ESI triage category) with three extra rules:

  * aging      - every AGING_MINUTES of waiting moves a patient up one level, but only
                 up to level 3. Levels 1 and 2 stay reserved for genuinely high-risk patients.
  * tie-break  - inside a level, whoever arrived first goes first (FCFS)
  * preemption - only for ESI 1. If a resuscitation case arrives and every doctor is busy,
                 the doctor with the least urgent patient (ESI 3 or lower) is asked to pause.

Dispatch is non-preemptive otherwise: a doctor is never pulled out of a normal
consultation, which is how a real emergency department works.

Everything here works on plain dicts, so the Flask app and the offline simulator share it.
"""

AGING_MINUTES = 30  # picked with the aging sweep in simulate.py
AGING_CAP = 3
TARGET_MINUTES = {1: 0, 2: 10, 3: 30, 4: 60, 5: 120}  # ESI handbook guidance


def effective_level(esi, waited, aging_minutes=AGING_MINUTES):
    if esi <= 2:
        return esi
    return max(AGING_CAP, esi - int(waited // aging_minutes))


def queue_key(visit, now, policy="mediqueue", aging_minutes=AGING_MINUTES):
    """Smaller key = seen sooner. `now` and `arrived` are minutes (floats)."""
    waited = now - visit["arrived"]
    if policy == "fcfs":
        return (visit["arrived"], visit["id"])
    if policy == "sjf":
        return (visit["predicted"], visit["arrived"], visit["id"])
    if policy == "priority":
        return (visit["esi"], visit["arrived"], visit["id"])
    level = effective_level(visit["esi"], waited, aging_minutes)
    return (level, visit["arrived"], visit["id"])


def order_queue(waiting, now, policy="mediqueue", aging_minutes=AGING_MINUTES):
    return sorted(waiting, key=lambda v: queue_key(v, now, policy, aging_minutes))


def pick_next(waiting, now, policy="mediqueue", aging_minutes=AGING_MINUTES):
    if not waiting:
        return None
    return min(waiting, key=lambda v: queue_key(v, now, policy, aging_minutes))


def preemption_target(waiting, busy):
    """
    busy = list of (doctor_id, visit) currently in consultation.
    Returns the doctor who should pause, or None.
    """
    if not any(v["esi"] == 1 for v in waiting) or not busy:
        return None
    candidates = [(doc, v) for doc, v in busy if v["esi"] >= 3]
    if not candidates:
        return None  # everyone is already treating an emergency - call for backup instead
    # least urgent patient first, then the one who has had the shortest consult so far
    doc, _ = max(candidates, key=lambda dv: (dv[1]["esi"], -dv[1].get("consult_done", 0)))
    return doc


def estimate_waits(waiting, busy_until, free_doctors, now, aging_minutes=AGING_MINUTES):
    """
    Estimated minutes until each waiting patient is called.

    We replay the dispatcher forward in time: every doctor becomes free at some moment
    (now, or when their current patient's predicted consult ends), and the queue is served
    in policy order using the AI-predicted consultation times.
    New arrivals are unknown, so the estimate is optimistic for low-priority patients;
    the waiting room board shows a range because of that.
    """
    free_at = [now] * free_doctors + [max(now + 2, t) for t in busy_until]
    if not free_at:
        return {}
    eta = {}
    for v in order_queue(waiting, now, aging_minutes=aging_minutes):
        i = min(range(len(free_at)), key=lambda k: free_at[k])
        start = free_at[i]
        eta[v["id"]] = max(0.0, start - now)
        free_at[i] = start + max(1, v["predicted"] - v.get("consult_done", 0))
    return eta
