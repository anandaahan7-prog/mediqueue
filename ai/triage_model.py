"""
Runtime side of the AI: loads the trained models and answers three questions for the app.

  suggest(vitals)   -> what ESI level should this patient get, and why?
  minutes(vitals)   -> how long will the consultation probably take?
  red_flags(vitals) -> anything that must be ESI 1 no matter what the model thinks?

The nurse always has the final say; the app stores both the AI suggestion and the
final level so we can later measure how often nurses disagreed.
"""

from pathlib import Path

import joblib
import numpy as np

from ai.dataset import COMPLAINT_LABELS, CONSCIOUSNESS, NUMERIC_FEATURES, news2, to_vector

MODEL_DIR = Path(__file__).resolve().parent.parent / "models"

# value used for the "what if this vital were normal?" explanation
NORMAL = {"age": 35, "heart_rate": 78, "systolic_bp": 120, "resp_rate": 16, "spo2": 98,
          "temperature": 36.8, "consciousness": 0, "pain": 2}
LABELS = {"age": "Age", "heart_rate": "Heart rate", "systolic_bp": "Systolic BP", "resp_rate": "Resp. rate",
          "spo2": "SpO2", "temperature": "Temperature", "consciousness": "Consciousness", "pain": "Pain score",
          "complaint": "Complaint"}
UNITS = {"age": " yrs", "heart_rate": " bpm", "systolic_bp": " mmHg", "resp_rate": "/min", "spo2": "%",
         "temperature": " °C", "pain": "/10"}

# If the model gives the next more urgent level at least this much probability, we suggest
# that level instead. Over-triage costs a few minutes; under-triage can cost a life.
SAFETY_MARGIN = 0.30

_esi_model = None
_time_model = None


def _models():
    global _esi_model, _time_model
    if _esi_model is None:
        if not (MODEL_DIR / "esi_model.joblib").exists():
            from ai import train
            train.main()
        _esi_model = joblib.load(MODEL_DIR / "esi_model.joblib")
        _time_model = joblib.load(MODEL_DIR / "time_model.joblib")
        # one patient at a time: spinning up worker threads costs more than it saves
        _esi_model.n_jobs = _time_model.n_jobs = 1
    return _esi_model, _time_model


def probabilities(vitals):
    model, _ = _models()
    proba = model.predict_proba([to_vector(vitals)])[0]
    out = np.zeros(5)
    for cls, p in zip(model.classes_, proba):
        out[int(cls) - 1] = p
    return out


def batch_probabilities(vitals_list):
    model, _ = _models()
    proba = model.predict_proba([to_vector(v) for v in vitals_list])
    out = np.zeros((len(vitals_list), 5))
    for j, cls in enumerate(model.classes_):
        out[:, int(cls) - 1] = proba[:, j]
    return out


def batch_minutes(vitals_list, levels):
    _, model = _models()
    rows = [to_vector(v) + [lvl] for v, lvl in zip(vitals_list, levels)]
    return [int(round(m)) for m in model.predict(rows)]


def apply_safety(probs):
    """argmax, but lean towards the more urgent level when the model is unsure."""
    level = int(np.argmax(probs)) + 1
    if level > 1 and probs[level - 2] >= SAFETY_MARGIN:
        level -= 1
    return level


def red_flags(v):
    flags = []
    if v["spo2"] < 85:
        flags.append(f"SpO2 {v['spo2']}% (below 85)")
    if v["systolic_bp"] < 80:
        flags.append(f"Systolic BP {v['systolic_bp']} (shock range)")
    if v["consciousness"] >= 2:
        flags.append(f"Responds only to {CONSCIOUSNESS[v['consciousness']].lower()}")
    if v["heart_rate"] > 150 or v["heart_rate"] < 40:
        flags.append(f"Heart rate {v['heart_rate']}")
    if v["resp_rate"] >= 35 or v["resp_rate"] <= 8:
        flags.append(f"Resp. rate {v['resp_rate']}")
    return flags


def _urgency(probs):
    # single number: 5 = surely ESI 1, 1 = surely ESI 5
    return float(sum(p * (5 - k) for k, p in enumerate(probs)))


def explain(vitals, probs):
    """Reset each abnormal input to a healthy value and see how much the urgency drops."""
    base = _urgency(probs)
    keys = [k for k in NUMERIC_FEATURES if float(vitals[k]) != NORMAL[k]]
    variants = [{**vitals, k: NORMAL[k]} for k in keys]
    if vitals["complaint"] != "minor":
        keys.append("complaint")
        variants.append({**vitals, "complaint": "minor"})
    if not variants:
        return []
    # all the "what if" patients in one batch - one model call instead of ten
    reasons = [(k, base - _urgency(p)) for k, p in zip(keys, batch_probabilities(variants))]

    reasons = [r for r in reasons if abs(r[1]) >= 0.08]
    reasons.sort(key=lambda r: -abs(r[1]))
    out = []
    for key, delta in reasons[:4]:
        if key == "complaint":
            shown = COMPLAINT_LABELS[vitals["complaint"]]
        elif key == "consciousness":
            shown = CONSCIOUSNESS[int(vitals[key])]
        else:
            shown = f"{vitals[key]}{UNITS.get(key, '')}"
        out.append({"feature": LABELS[key], "value": shown, "effect": round(delta, 2)})
    return out


def predict_minutes(vitals, esi):
    _, model = _models()
    return int(round(model.predict([to_vector(vitals) + [esi]])[0]))


def suggest(vitals, with_reasons=True):
    probs = probabilities(vitals)
    model_level = int(np.argmax(probs)) + 1
    level = apply_safety(probs)
    flags = red_flags(vitals)
    if flags:
        level = 1
    return {
        "esi": level,
        "model_esi": model_level,
        "safety_bumped": level < model_level and not flags,
        "red_flags": flags,
        "probs": [round(float(p), 3) for p in probs],
        "confidence": round(float(probs[level - 1]), 3),
        "news2": news2(vitals),
        "minutes": predict_minutes(vitals, level),
        "reasons": explain(vitals, probs) if with_reasons else [],
    }
