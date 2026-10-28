"""
Offline evaluation: replay many simulated 12-hour shifts (08:00-20:00) under different
dispatch policies, and train / test the waiting-time model.

    python simulate.py            -> prints the tables and writes models/simulation.json

Patients come from the same generator as the training data. The scheduler only sees what
the real app would see (AI-suggested ESI with safety rules, AI-predicted consult time),
but every policy is scored against the patient's TRUE urgency and TRUE consult time.
"""

import json
import sys

import numpy as np

import scheduler
from ai import wait_model
from ai.dataset import clinical_esi, consult_minutes, sample_patient
from ai.triage_model import MODEL_DIR, apply_safety, batch_minutes, batch_probabilities, red_flags
from scheduler import (AGING_MINUTES, TARGET_MINUTES, estimate_waits, pick_before, pick_next, preemption_target,
                       reserve_doctor, risk_of)

POLICIES = {
    "fcfs": "FCFS (token order)",
    "sjf": "Shortest predicted first",
    "priority": "Priority (triage only)",
    "mediqueue_fixed": "MediQueue, fixed aging",
    "mediqueue": "MediQueue, risk-adaptive (ours)",
}

# average arrivals per hour from 08:00 to 20:00 - a late-morning and an evening peak
HOURLY_ARRIVALS = [4, 5, 7, 8, 8, 6, 5, 5, 6, 8, 9, 7]


def make_day(rng, scale=1.0):
    vitals, arrivals = [], []
    for hour, rate in enumerate(HOURLY_ARRIVALS):
        for _ in range(rng.poisson(rate * scale)):
            vitals.append(sample_patient(rng))
            arrivals.append(hour * 60 + rng.uniform(0, 60))
    if not vitals:
        return []

    # one batched call per model is much faster than one call per patient
    probs = batch_probabilities(vitals)
    ai_esi = [1 if red_flags(v) else apply_safety(p) for v, p in zip(vitals, probs)]
    predicted = batch_minutes(vitals, ai_esi)

    patients = []
    for i, v in enumerate(vitals):
        true_esi = clinical_esi(v)
        patients.append({
            "id": i + 1,
            "arrived": arrivals[i],
            "esi": ai_esi[i],          # what the scheduler sees
            "risk": risk_of(probs[i], ai_esi[i]),
            "true_esi": true_esi,      # what we judge it by
            "predicted": predicted[i],
            "actual": consult_minutes(v, true_esi, rng),
        })
    patients.sort(key=lambda p: p["arrived"])
    return patients


def simulate(patients, policy, doctors=4, aging_minutes=AGING_MINUTES, collect=False, prealert=False):
    """Minute-by-minute replay. With collect=True it also records, for every arrival,
    the plain schedule estimate and the wait-model features.

    prealert=True: patients flagged "ambulance" are announced "lead" minutes before they arrive.
    The doctor predicted to be free first holds their room for them and meanwhile only takes
    patients whose predicted consultation ends before the ambulance does (scheduler.pick_before)."""
    visits = [dict(p, consult_done=0, first_seen=None) for p in patients]
    incoming = list(reversed(visits))
    alerts = sorted((v for v in visits if prealert and v.get("ambulance")), key=lambda v: v["arrived"] - v["lead"])
    held = {}  # doctor -> ambulance patient their room is held for
    waiting, busy = [], {}
    preemptions = 0
    rows = {}
    t = 0.0

    while incoming or waiting or busy:
        while incoming and incoming[-1]["arrived"] <= t:
            v = incoming.pop()
            waiting.append(v)
            if collect:
                busy_until = [t + max(1, b["predicted"] - b["consult_done"]) for b in busy.values()]
                eta = estimate_waits(waiting, busy_until, doctors - len(busy), t, aging_minutes, policy).get(v["id"], 0.0)
                recent = sum(1 for p in visits if t - 60 < p["arrived"] <= t)
                rows[v["id"]] = wait_model.features(v, waiting, t, len(busy), doctors, recent, eta,
                                                    hour=8 + t / 60, aging_minutes=aging_minutes)

        # pre-alerts: hold the room of the doctor predicted to be free first
        while alerts and alerts[0]["arrived"] - alerts[0]["lead"] <= t:
            amb = alerts.pop(0)
            free_at = {d: (t + max(1, busy[d]["predicted"] - busy[d]["consult_done"]) if d in busy else t)
                       for d in range(doctors)}
            doc = reserve_doctor(free_at, amb["arrived"], held)
            if doc is not None:
                held[doc] = amb

        if policy.startswith("mediqueue") and len(busy) == doctors:
            doc = preemption_target(waiting, list(busy.items()))
            if doc is not None:
                waiting.append(busy.pop(doc))
                preemptions += 1

        for doc in range(doctors):
            if doc in busy or not waiting:
                continue
            amb = held.get(doc)
            if amb is not None and amb in waiting:
                nxt = amb  # the ambulance is here and its room is free
            elif amb is not None and amb["first_seen"] is None and amb["arrived"] > t:
                nxt = pick_before(waiting, t, amb["arrived"], policy, aging_minutes)
                if nxt is None:
                    continue  # nobody short enough: keep the room free
            else:
                nxt = pick_next(waiting, t, policy, aging_minutes)
            waiting.remove(nxt)
            for d, a in list(held.items()):
                if a is nxt:
                    del held[d]
            if nxt["first_seen"] is None:
                nxt["first_seen"] = t
            busy[doc] = nxt

        t += 1.0
        for doc in list(busy):
            v = busy[doc]
            v["consult_done"] += 1
            if v["consult_done"] >= v["actual"]:
                del busy[doc]

    return {"visits": visits, "preemptions": preemptions, "rows": rows}


def compare_policies(days=40, doctors=4, seed=7, scale=1.0, aging_minutes=AGING_MINUTES, policies=POLICIES):
    rng = np.random.default_rng(seed)
    acc = {name: {"by_esi": [[] for _ in range(5)], "hit": [[] for _ in range(5)], "all": [], "max": [], "pre": [],
                  "hidden": [], "hidden_hit": []}
           for name in policies}

    for _ in range(days):
        patients = make_day(rng, scale)
        for policy in policies:
            run = simulate(patients, policy, doctors, aging_minutes)
            a = acc[policy]
            waits = []
            for p in run["visits"]:
                w = p["first_seen"] - p["arrived"]
                waits.append(w)
                on_time = w <= max(TARGET_MINUTES[p["true_esi"]], 1)
                a["by_esi"][p["true_esi"] - 1].append(w)
                a["hit"][p["true_esi"] - 1].append(on_time)
                if p["true_esi"] < p["esi"]:  # the AI under-triaged them: sicker than their level says
                    a["hidden"].append(w)
                    a["hidden_hit"].append(on_time)
            a["all"] += waits
            a["max"].append(max(waits) if waits else 0)
            a["pre"].append(run["preemptions"])

    out = {}
    for policy, a in acc.items():
        low = a["by_esi"][3] + a["by_esi"][4]
        out[policy] = {
            "label": POLICIES[policy],
            "mean_wait": float(np.mean(a["all"])),
            "wait_by_esi": [float(np.mean(w)) if w else 0.0 for w in a["by_esi"]],
            "within_target": [float(np.mean(h)) if h else 0.0 for h in a["hit"]],
            "critical_wait": float(np.mean(a["by_esi"][0] + a["by_esi"][1])),
            "low_priority_p95": float(np.percentile(low, 95)) if low else 0.0,
            "max_wait": float(np.mean(a["max"])),
            "preemptions_per_day": float(np.mean(a["pre"])),
            "patients": len(a["all"]),
            "hidden_urgent": len(a["hidden"]),
            "hidden_urgent_wait": float(np.mean(a["hidden"])) if a["hidden"] else 0.0,
            "hidden_urgent_on_time": float(np.mean(a["hidden_hit"])) if a["hidden_hit"] else 0.0,
        }
    return out


def wait_samples(days, seed, doctors=4, aging_minutes=AGING_MINUTES):
    rng = np.random.default_rng(seed)
    X, y, esi = [], [], []
    for _ in range(days):
        run = simulate(make_day(rng), "mediqueue", doctors, aging_minutes, collect=True)
        for p in run["visits"]:
            if p["id"] in run["rows"]:
                X.append(run["rows"][p["id"]])
                y.append(p["first_seen"] - p["arrived"])
                esi.append(p["esi"])
    return np.array(X), np.array(y), np.array(esi)


def evaluate_wait_model():
    X_train, y_train, _ = wait_samples(days=80, seed=101)
    X_test, y_test, esi_test = wait_samples(days=25, seed=202)
    model = wait_model.train(X_train, y_train)
    pred = np.maximum(0, model.predict(X_test))
    plain = X_test[:, 0]

    def report(est):
        err = np.abs(est - y_test)
        return {"mae": float(err.mean()), "median": float(np.median(err)), "within_10": float(np.mean(err <= 10)),
                "mae_by_esi": [float(err[esi_test == k].mean()) if np.any(esi_test == k) else 0.0 for k in range(1, 6)]}

    rng = np.random.default_rng(5)
    sample = rng.choice(len(y_test), size=min(400, len(y_test)), replace=False)
    return {
        "train_rows": int(len(y_train)), "test_rows": int(len(y_test)),
        "plain": report(plain), "model": report(pred),
        "importance": dict(zip(wait_model.WAIT_FEATURES, model.feature_importances_.round(4).tolist())),
        "scatter": [[round(float(plain[i]), 1), round(float(pred[i]), 1), round(float(y_test[i]), 1), int(esi_test[i])]
                    for i in sample],
    }


def aging_sweep(values=(5, 10, 15, 30, 60, None), days=25, seed=11):
    """How the aging interval trades critical-patient speed against low-priority fairness."""
    out = []
    for a in values:
        m = compare_policies(days=days, seed=seed, aging_minutes=a or 10 ** 6,
                             policies={"mediqueue_fixed": ""})["mediqueue_fixed"]
        out.append({"aging": a, "critical_wait": m["critical_wait"], "low_p95": m["low_priority_p95"],
                    "mean_wait": m["mean_wait"], "esi3_target": m["within_target"][2]})
    return out


def risk_sweep(values=(None, 0.4, 0.3, 0.25, 0.2), days=40, seed=7):
    """Which risk lets a patient age past level 3 into level 2 (None = never; the interval still
    shortens with risk). Judged on the patients the AI under-triaged, against what it costs the
    critical patients and the low-priority tail."""
    keep = scheduler.RISK_CAP_THRESHOLD
    out = []
    try:
        for theta in values:
            scheduler.RISK_CAP_THRESHOLD = theta
            m = compare_policies(days=days, seed=seed, policies={"mediqueue": ""})["mediqueue"]
            out.append({"threshold": theta, "hidden_wait": m["hidden_urgent_wait"],
                        "hidden_on_time": m["hidden_urgent_on_time"], "critical_wait": m["critical_wait"],
                        "low_p95": m["low_priority_p95"], "mean_wait": m["mean_wait"]})
    finally:
        scheduler.RISK_CAP_THRESHOLD = keep
    return out


def prealert_experiment(days=40, doctors=4, seed=7, share=0.5, lead=(5, 15)):
    """Half of the ESI 1-2 patients arrive by ambulance and are announced 5-15 minutes ahead.
    Door-to-doctor time for them, with and without holding a room, and what it costs everyone else."""
    rng = np.random.default_rng(seed)
    flags = np.random.default_rng(seed + 500)  # separate stream: the patients are the same as in compare_policies
    acc = {mode: {"amb": [], "amb_hit": [], "others": [], "critical": []} for mode in ("off", "on")}
    ambulances = 0
    for _ in range(days):
        patients = make_day(rng)
        for p in patients:
            if p["esi"] <= 2 and flags.random() < share:
                p["ambulance"], p["lead"] = True, float(flags.uniform(*lead))
                ambulances += 1
        for mode in ("off", "on"):
            run = simulate(patients, "mediqueue", doctors, prealert=(mode == "on"))
            a = acc[mode]
            for v in run["visits"]:
                w = v["first_seen"] - v["arrived"]
                if v.get("ambulance"):
                    a["amb"].append(w)
                    a["amb_hit"].append(w <= max(TARGET_MINUTES[v["true_esi"]], 1))
                else:
                    a["others"].append(w)
                if v["true_esi"] <= 2:
                    a["critical"].append(w)
    out = {"ambulances": ambulances, "days": days, "lead": list(lead)}
    for mode, a in acc.items():
        out[mode] = {"ambulance_wait": float(np.mean(a["amb"])), "ambulance_p90": float(np.percentile(a["amb"], 90)),
                     "ambulance_on_time": float(np.mean(a["amb_hit"])), "others_wait": float(np.mean(a["others"])),
                     "critical_wait": float(np.mean(a["critical"]))}
    return out


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    summary = {"policies": compare_policies(days=days), "config": {"days": days, "doctors": 4, "aging_minutes": AGING_MINUTES}}
    print(f"{'policy':26s} {'mean':>6s} {'ESI1-2':>7s} {'p95 4-5':>8s} {'max':>6s}   % within target ESI 1..5")
    for s in summary["policies"].values():
        tgt = " ".join(f"{x * 100:4.0f}" for x in s["within_target"])
        print(f"{s['label']:26s} {s['mean_wait']:6.1f} {s['critical_wait']:7.1f} {s['low_priority_p95']:8.1f} "
              f"{s['max_wait']:6.1f}   {tgt}   preempt/day {s['preemptions_per_day']:.1f}")

    summary["wait_model"] = evaluate_wait_model()
    w = summary["wait_model"]
    print(f"wait estimate  plain: MAE {w['plain']['mae']:.1f}  within10 {w['plain']['within_10'] * 100:.0f}%   "
          f"ML: MAE {w['model']['mae']:.1f}  within10 {w['model']['within_10'] * 100:.0f}%   "
          f"(train {w['train_rows']}, test {w['test_rows']})")
    print("  MAE by ESI plain", [round(x, 1) for x in w["plain"]["mae_by_esi"]],
          " ML", [round(x, 1) for x in w["model"]["mae_by_esi"]])

    summary["aging_sweep"] = aging_sweep()
    for row in summary["aging_sweep"]:
        print(f"  aging {str(row['aging']):>5s}: critical {row['critical_wait']:.1f}  low p95 {row['low_p95']:.0f}  "
              f"mean {row['mean_wait']:.1f}  ESI3 target {row['esi3_target'] * 100:.0f}%")

    summary["risk_sweep"] = risk_sweep()
    for row in summary["risk_sweep"]:
        print(f"  risk threshold {str(row['threshold']):>4s}: under-triaged wait {row['hidden_wait']:.1f} "
              f"on time {row['hidden_on_time'] * 100:.0f}%  critical {row['critical_wait']:.1f}  "
              f"low p95 {row['low_p95']:.0f}  mean {row['mean_wait']:.1f}")

    summary["prealert"] = pre = prealert_experiment(days=days)
    for mode in ("off", "on"):
        r = pre[mode]
        print(f"  pre-alert {mode:3s}: ambulance door-to-doctor {r['ambulance_wait']:.1f} (p90 {r['ambulance_p90']:.0f}, "
              f"on time {r['ambulance_on_time'] * 100:.0f}%)  others {r['others_wait']:.1f}  critical {r['critical_wait']:.1f}")
    (MODEL_DIR / "simulation.json").write_text(json.dumps(summary, indent=2))
