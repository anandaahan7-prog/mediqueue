"""
Synthetic triage dataset.

We could not get access to real ER records (MIMIC-IV-ED needs credentialed access and an
ethics course, which we did not have time for), so we generate patients ourselves:

  1. pick a type of visit (cardiac, trauma, fever ...) with realistic frequencies
  2. sample vitals around typical values for that visit type
  3. label the urgency with the NEWS2 score + the main ESI decision points
  4. flip about 6% of the labels by one level, because real nurses don't always agree

The consultation time is generated from the complaint, the urgency level and the age.
Everything is seeded so the numbers in our report can be reproduced.
"""

import numpy as np

COMPLAINTS = ["minor", "fever", "abdominal", "trauma", "breathing", "chest", "stroke"]
COMPLAINT_LABELS = {
    "minor": "Minor injury / sprain",
    "fever": "Fever / infection",
    "abdominal": "Abdominal pain",
    "trauma": "Trauma / accident",
    "breathing": "Breathing difficulty",
    "chest": "Chest pain",
    "stroke": "Stroke symptoms",
}
# AVPU scale: Alert, responds to Voice, responds to Pain, Unresponsive
CONSCIOUSNESS = ["Alert", "Voice", "Pain", "Unresponsive"]

NUMERIC_FEATURES = ["age", "heart_rate", "systolic_bp", "resp_rate", "spo2", "temperature", "consciousness", "pain"]
FEATURES = NUMERIC_FEATURES + [f"complaint_{c}" for c in COMPLAINTS]

# (share of visits, age, HR, SBP, RR, SpO2, temp, pain as (mean, sd), chance of each AVPU level)
PROFILES = {
    "minor":     (0.24, (32, 16), (82, 10), (124, 12), (16, 2), (98, 1.0), (36.8, 0.3), (4, 2), (0.99, 0.01, 0, 0)),
    "fever":     (0.18, (30, 20), (102, 12), (118, 14), (20, 3), (96, 2.0), (38.9, 0.7), (3, 2), (0.92, 0.06, 0.02, 0)),
    "abdominal": (0.15, (40, 17), (92, 14), (120, 16), (18, 3), (97, 1.5), (37.4, 0.6), (7, 2), (0.96, 0.04, 0, 0)),
    "trauma":    (0.14, (34, 17), (104, 20), (114, 22), (21, 4), (95, 3.0), (36.6, 0.4), (7, 2), (0.80, 0.10, 0.06, 0.04)),
    "breathing": (0.12, (45, 22), (108, 16), (126, 18), (27, 5), (90, 4.0), (37.3, 0.7), (3, 2), (0.85, 0.12, 0.03, 0)),
    "chest":     (0.11, (58, 13), (104, 22), (130, 28), (21, 4), (95, 3.0), (36.9, 0.4), (7, 2), (0.88, 0.08, 0.03, 0.01)),
    "stroke":    (0.06, (67, 11), (90, 15), (165, 25), (18, 3), (95, 2.5), (36.9, 0.4), (2, 2), (0.45, 0.30, 0.15, 0.10)),
}

BASE_MINUTES = {"minor": 10, "fever": 16, "abdominal": 24, "trauma": 28, "breathing": 22, "chest": 32, "stroke": 36}


def sample_patient(rng, complaint=None):
    if complaint is None:
        shares = [PROFILES[c][0] for c in COMPLAINTS]
        complaint = COMPLAINTS[rng.choice(len(COMPLAINTS), p=shares)]
    _, age, hr, sbp, rr, spo2, temp, pain, avpu = PROFILES[complaint]

    consciousness = int(rng.choice(4, p=avpu))
    worse = 1 if consciousness >= 2 else 0  # someone barely responding is usually unwell in other ways too

    return {
        "age": int(np.clip(rng.normal(*age), 1, 95)),
        "heart_rate": int(np.clip(rng.normal(hr[0] + 15 * worse, hr[1]), 35, 190)),
        "systolic_bp": int(np.clip(rng.normal(sbp[0] - 20 * worse, sbp[1]), 60, 230)),
        "resp_rate": int(np.clip(rng.normal(*rr), 6, 45)),
        "spo2": int(np.clip(rng.normal(spo2[0] - 4 * worse, spo2[1]), 70, 100)),
        "temperature": round(float(np.clip(rng.normal(*temp), 34, 42)), 1),
        "consciousness": consciousness,
        "pain": int(np.clip(rng.normal(*pain), 0, 10)),
        "complaint": complaint,
    }


def news2(v):
    """National Early Warning Score 2 (Royal College of Physicians). Higher = sicker."""
    s = 0
    rr = v["resp_rate"]
    s += 3 if rr <= 8 else 1 if rr <= 11 else 3 if rr >= 25 else 2 if rr >= 21 else 0
    sp = v["spo2"]
    s += 3 if sp <= 91 else 2 if sp <= 93 else 1 if sp <= 95 else 0
    bp = v["systolic_bp"]
    s += 3 if bp <= 90 or bp >= 220 else 2 if bp <= 100 else 1 if bp <= 110 else 0
    hr = v["heart_rate"]
    s += 3 if hr <= 40 or hr >= 131 else 2 if hr >= 111 else 1 if hr <= 50 or hr >= 91 else 0
    s += 3 if v["consciousness"] > 0 else 0
    t = v["temperature"]
    s += 3 if t <= 35 else 2 if t >= 39.1 else 1 if t <= 36 or t >= 38.1 else 0
    return s


def clinical_esi(v):
    """The 'ground truth' labeller: NEWS2 plus the main ESI decision points."""
    score = news2(v)
    c = v["complaint"]
    if v["consciousness"] >= 2 or v["spo2"] < 86 or v["systolic_bp"] < 80 or v["heart_rate"] > 150 or score >= 10:
        return 1
    if score >= 7 or c == "stroke" or v["pain"] >= 9 or v["consciousness"] == 1 or \
            (c == "chest" and (v["age"] >= 40 or v["heart_rate"] > 110)):
        return 2
    if score >= 4 or c in ("breathing", "chest") or (v["age"] >= 70 and score >= 2) or \
            (c == "trauma" and v["pain"] >= 6) or (c == "abdominal" and v["pain"] >= 7):
        return 3
    if score >= 2 or v["pain"] >= 5 or c in ("fever", "abdominal", "trauma"):
        return 4
    return 5


def consult_minutes(v, esi, rng):
    minutes = BASE_MINUTES[v["complaint"]] * (1 + (5 - esi) * 0.2) + max(0, v["age"] - 50) * 0.25
    minutes *= np.clip(rng.normal(1, 0.15), 0.65, 1.45)
    return int(np.clip(round(minutes), 4, 90))


def to_vector(v):
    row = [float(v[f]) for f in NUMERIC_FEATURES]
    row += [1.0 if v["complaint"] == c else 0.0 for c in COMPLAINTS]
    return row


def make_dataset(n=3000, seed=42, noise=0.06):
    rng = np.random.default_rng(seed)
    rows, esi, minutes = [], [], []
    for _ in range(n):
        v = sample_patient(rng)
        level = clinical_esi(v)
        if rng.random() < noise:
            level = int(np.clip(level + rng.choice([-1, 1]), 1, 5))
        rows.append(v)
        esi.append(level)
        minutes.append(consult_minutes(v, level, rng))
    return rows, np.array(esi), np.array(minutes)
