"""
AI part 3 - waiting-time prediction for the waiting-room display.

The scheduler can already "replay" the queue forward with the predicted consult times
(scheduler.estimate_waits). That number is good for urgent patients, but a low-priority
patient keeps getting overtaken by people who haven't even arrived yet, so the plain
estimate is far too optimistic for them.

So we learn the correction from history: a gradient-boosting model gets the plain
estimate plus a few facts about the department at that moment, and predicts the wait
that actually happened. In the real system the history comes from the visits table;
for the project we train it on simulated shifts (simulate.py).
"""

from pathlib import Path

import joblib
import numpy as np

from scheduler import effective_level

MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "wait_model.joblib"

WAIT_FEATURES = ["schedule_eta", "esi", "ahead_higher", "ahead_same", "doctors_busy", "doctors_total",
                 "arrivals_last_hour", "hour"]

_model = None


def features(visit, waiting, now, doctors_busy, doctors_total, arrivals_last_hour, schedule_eta, hour,
             aging_minutes=15):
    level = effective_level(visit["esi"], now - visit["arrived"], aging_minutes, visit.get("risk", 0.0))
    others = [w for w in waiting if w["id"] != visit["id"]]
    levels = [effective_level(w["esi"], now - w["arrived"], aging_minutes, w.get("risk", 0.0)) for w in others]
    return [
        schedule_eta,
        visit["esi"],
        sum(1 for l in levels if l < level),
        sum(1 for l in levels if l == level),
        doctors_busy,
        doctors_total,
        arrivals_last_hour,
        hour,
    ]


def train(X, y):
    from sklearn.ensemble import GradientBoostingRegressor
    model = GradientBoostingRegressor(n_estimators=300, max_depth=3, learning_rate=0.05,
                                      subsample=0.8, loss="absolute_error", random_state=3)
    model.fit(np.asarray(X), np.asarray(y))
    MODEL_PATH.parent.mkdir(exist_ok=True)
    joblib.dump(model, MODEL_PATH)
    global _model
    _model = model
    return model


def available():
    return MODEL_PATH.exists()


def predict(rows):
    """rows = list of feature lists. Falls back to the plain schedule estimate if untrained."""
    global _model
    if not rows:
        return []
    if _model is None:
        if not MODEL_PATH.exists():
            return [r[0] for r in rows]
        _model = joblib.load(MODEL_PATH)
    return [max(0.0, float(p)) for p in _model.predict(np.asarray(rows))]
