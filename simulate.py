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

from ai import wait_model
from ai.dataset import clinical_esi, consult_minutes, sample_patient
from ai.triage_model import MODEL_DIR, apply_safety, batch_minutes, batch_probabilities, red_flags
from scheduler import AGING_MINUTES, TARGET_MINUTES, estimate_waits, pick_next, preemption_target

POLICIES = {
    "fcfs": "FCFS (token order)",
    "sjf": "Shortest predicted first",
    "priority": "Priority (triage only)",
    "mediqueue": "MediQueue (ours)",
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
            "true_esi": true_esi,      # what we judge it by
            "predicted": predicted[i],
            "actual": consult_minutes(v, true_esi, rng),
        })
    patients.sort(key=lambda p: p["arrived"])
    return patients


def simulate(patients, policy, doctors=4, aging_minutes=AGING_MINUTES, collect=False):
    """Minute-by-minute replay. With collect=True it also records, for every arrival,
    the plain schedule estimate and the wait-model features."""
    visits = [dict(p, consult_done=0, first_seen=None) for p in patients]
    incoming = list(reversed(visits))
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
                eta = estimate_waits(waiting, busy_until, doctors - len(busy), t, aging_minutes).get(v["id"], 0.0)
                recent = sum(1 for p in visits if t - 60 < p["arrived"] <= t)
                rows[v["id"]] = wait_model.features(v, waiting, t, len(busy), doctors, recent, eta,
                                                    hour=8 + t / 60, aging_minutes=aging_minutes)

        if policy == "mediqueue" and len(busy) == doctors:
            doc = preemption_target(waiting, list(busy.items()))
            if doc is not None:
                waiting.append(busy.pop(doc))
                preemptions += 1

        for doc in range(doctors):
            if doc not in busy and waiting:
                nxt = pick_next(waiting, t, policy, aging_minutes)
                waiting.remove(nxt)
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


def compare_policies(days=40, doctors=4, seed=7, scale=1.0, aging_minutes=AGING_MINUTES):
    rng = np.random.default_rng(seed)
    acc = {name: {"by_esi": [[] for _ in range(5)], "hit": [[] for _ in range(5)], "all": [], "max": [], "pre": []}
           for name in POLICIES}

    for _ in range(days):
        patients = make_day(rng, scale)
        for policy in POLICIES:
            run = simulate(patients, policy, doctors, aging_minutes)
            a = acc[policy]
            waits = []
            for p in run["visits"]:
                w = p["first_seen"] - p["arrived"]
                waits.append(w)
                a["by_esi"][p["true_esi"] - 1].append(w)
                a["hit"][p["true_esi"] - 1].append(w <= max(TARGET_MINUTES[p["true_esi"]], 1))
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
        m = compare_policies(days=days, seed=seed, aging_minutes=a or 10 ** 6)["mediqueue"]
        out.append({"aging": a, "critical_wait": m["critical_wait"], "low_p95": m["low_priority_p95"],
                    "mean_wait": m["mean_wait"], "esi3_target": m["within_target"][2]})
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
    (MODEL_DIR / "simulation.json").write_text(json.dumps(summary, indent=2))
