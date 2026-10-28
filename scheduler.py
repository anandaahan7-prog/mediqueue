"""
OS concept 1 - CPU scheduling, applied to "which patient does the next free doctor see?"

Doctors are the processors, waiting patients are the ready queue. The policy is a
multilevel priority queue (one level per ESI triage category) with three extra rules:

  * aging      - every AGING_MINUTES of waiting moves a patient up one level, but only
                 up to level 3. Levels 1 and 2 stay reserved for genuinely high-risk patients.
  * risk-adaptive aging - the interval is not the same for everyone. The triage model's
                 probability that an ESI 3-5 patient is really an emergency (ESI 1-2), their
                 "emergency risk", shortens their interval, and a patient with risk >= 30% may
                 age one step further, into level 2. A borderline patient therefore climbs
                 sooner than a clearly minor one who arrived at the same time, while
                 low-risk patients still can never reach the emergency levels.
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

# risk-adaptive aging: interval = AGING_MINUTES x max(MIN_FACTOR, 1 - RISK_SPEEDUP x risk)
RISK_SPEEDUP = 2.0
MIN_FACTOR = 1 / 3        # nobody ages faster than one level per 10 minutes (at the default 30)
RISK_CAP_THRESHOLD = 0.30  # risk at which aging may go on to level 2; picked with the risk sweep in simulate.py


def risk_of(probs, esi):
    """Emergency risk: the model's probability that a patient given ESI 3-5 is really ESI 1-2."""
    return float(probs[0] + probs[1]) if esi >= 3 else 0.0


def aging_interval(risk=0.0, aging_minutes=AGING_MINUTES, adaptive=True):
    """Minutes of waiting that move this patient up one level."""
    if not adaptive or not risk:
        return aging_minutes
    return aging_minutes * max(MIN_FACTOR, 1 - RISK_SPEEDUP * risk)


def aging_cap(risk=0.0, adaptive=True):
    """Highest level waiting alone can earn: 3, or 2 for a patient the model thinks may be sicker."""
    return AGING_CAP - 1 if adaptive and RISK_CAP_THRESHOLD is not None and risk >= RISK_CAP_THRESHOLD else AGING_CAP


def effective_level(esi, waited, aging_minutes=AGING_MINUTES, risk=0.0, adaptive=True):
    if esi <= 2:
        return esi
    return max(aging_cap(risk, adaptive), esi - int(waited // aging_interval(risk, aging_minutes, adaptive)))


def queue_key(visit, now, policy="mediqueue", aging_minutes=AGING_MINUTES):
    """Smaller key = seen sooner. `now` and `arrived` are minutes (floats).

    policy "mediqueue" uses risk-adaptive aging, "mediqueue_fixed" the same interval for everyone."""
    waited = now - visit["arrived"]
    if policy == "fcfs":
        return (visit["arrived"], visit["id"])
    if policy == "sjf":
        return (visit["predicted"], visit["arrived"], visit["id"])
    if policy == "priority":
        return (visit["esi"], visit["arrived"], visit["id"])
    level = effective_level(visit["esi"], waited, aging_minutes, visit.get("risk", 0.0), policy != "mediqueue_fixed")
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


def pick_before(waiting, now, deadline, policy="mediqueue", aging_minutes=AGING_MINUTES):
    """Deadline-aware pick for a doctor whose room is held for an incoming ambulance.

    Like backfilling in a batch scheduler: take the most urgent waiting patient whose predicted
    consultation ends before the ambulance arrives; if nobody fits, the doctor waits."""
    for v in order_queue(waiting, now, policy, aging_minutes):
        if v["esi"] == 1 or now + max(1, v["predicted"] - v.get("consult_done", 0)) <= deadline:
            return v
    return None


def reserve_doctor(free_at, deadline, taken=()):
    """Whose room to hold for an ambulance arriving at `deadline` (minutes).

    free_at = {doctor_id: minute the doctor is predicted to be free}. Doctors already holding a
    room for another ambulance are skipped. Best fit: of the doctors free in time, the one who
    frees up LAST, so the room stands empty for the shortest time; if nobody is free in time,
    the one free soonest after. Ties go to the lowest id."""
    options = [(t, doc) for doc, t in free_at.items() if doc not in taken]
    if not options:
        return None
    in_time = [(t, -doc) for t, doc in options if t <= deadline]
    if in_time:
        return -max(in_time)[1]
    return min(options)[1]


def estimate_waits(waiting, busy_until, free_doctors, now, aging_minutes=AGING_MINUTES, policy="mediqueue"):
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
    for v in order_queue(waiting, now, policy, aging_minutes):
        i = min(range(len(free_at)), key=lambda k: free_at[k])
        start = free_at[i]
        eta[v["id"]] = max(0.0, start - now)
        free_at[i] = start + max(1, v["predicted"] - v.get("consult_done", 0))
    return eta
