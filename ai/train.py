"""
Train and evaluate the two models, then save them to models/.

    python -m ai.train

Prints the cross-validation results and writes models/metrics.json, which the admin
page and our report read from.
"""

import json
import time
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, mean_absolute_error
from sklearn.model_selection import KFold, StratifiedKFold

from ai.dataset import FEATURES, make_dataset, news2, to_vector

MODEL_DIR = Path(__file__).resolve().parent.parent / "models"


def news2_to_esi(score):
    # the "no AI" baseline: a straight mapping from the early-warning score
    if score >= 9:
        return 1
    if score >= 7:
        return 2
    if score >= 4:
        return 3
    if score >= 2:
        return 4
    return 5


def make_classifier(seed=42):
    return RandomForestClassifier(n_estimators=200, max_depth=12, min_samples_leaf=2,
                                  class_weight="balanced", random_state=seed, n_jobs=-1)


def make_regressor(seed=42):
    return RandomForestRegressor(n_estimators=150, max_depth=10, min_samples_leaf=4,
                                 random_state=seed, n_jobs=-1)


def triage_errors(true, pred):
    """Under-triage = AI says less urgent than it really is (the dangerous mistake)."""
    true, pred = np.asarray(true), np.asarray(pred)
    return float(np.mean(pred > true)), float(np.mean(pred < true))


def main(n=3000, seed=42):
    patients, esi, minutes = make_dataset(n=n, seed=seed)
    X = np.array([to_vector(p) for p in patients])
    X_time = np.column_stack([X, esi])  # the time model also knows the final triage level

    # ---- 5-fold cross validation ----
    folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=seed)
    cv_pred = np.zeros_like(esi)
    cv_proba = np.zeros((len(esi), 5))
    for train_idx, test_idx in folds.split(X, esi):
        model = make_classifier(seed).fit(X[train_idx], esi[train_idx])
        cv_pred[test_idx] = model.predict(X[test_idx])
        cv_proba[test_idx] = model.predict_proba(X[test_idx])

    # what the app actually suggests: safety margin + red-flag rules on top of the model
    from ai.triage_model import apply_safety, red_flags
    safe_pred = np.array([1 if red_flags(p) else apply_safety(pr) for p, pr in zip(patients, cv_proba)])

    time_pred = np.zeros(len(minutes), dtype=float)
    for train_idx, test_idx in KFold(5, shuffle=True, random_state=seed).split(X_time):
        reg = make_regressor(seed).fit(X_time[train_idx], minutes[train_idx])
        time_pred[test_idx] = reg.predict(X_time[test_idx])

    baseline = np.array([news2_to_esi(news2(p)) for p in patients])
    under, over = triage_errors(esi, cv_pred)
    safe_under, safe_over = triage_errors(esi, safe_pred)
    base_under, base_over = triage_errors(esi, baseline)

    # ---- final models on all the data ----
    started = time.perf_counter()
    classifier = make_classifier(seed).fit(X, esi)
    regressor = make_regressor(seed).fit(X_time, minutes)
    train_seconds = time.perf_counter() - started

    MODEL_DIR.mkdir(exist_ok=True)
    joblib.dump(classifier, MODEL_DIR / "esi_model.joblib")
    joblib.dump(regressor, MODEL_DIR / "time_model.joblib")

    metrics = {
        "samples": int(n),
        "class_counts": [int((esi == k).sum()) for k in range(1, 6)],
        "accuracy": accuracy_score(esi, cv_pred),
        "macro_f1": f1_score(esi, cv_pred, average="macro"),
        "within_one": float(np.mean(np.abs(cv_pred - esi) <= 1)),
        "under_triage": under,
        "over_triage": over,
        "safe_accuracy": accuracy_score(esi, safe_pred),
        "safe_macro_f1": f1_score(esi, safe_pred, average="macro"),
        "safe_under_triage": safe_under,
        "safe_over_triage": safe_over,
        "safe_confusion": confusion_matrix(esi, safe_pred, labels=[1, 2, 3, 4, 5]).tolist(),
        "baseline_accuracy": accuracy_score(esi, baseline),
        "baseline_macro_f1": f1_score(esi, baseline, average="macro"),
        "baseline_under_triage": base_under,
        "baseline_over_triage": base_over,
        "per_class_f1": f1_score(esi, cv_pred, average=None).tolist(),
        "confusion": confusion_matrix(esi, cv_pred, labels=[1, 2, 3, 4, 5]).tolist(),
        "time_mae": mean_absolute_error(minutes, time_pred),
        "time_mean": float(minutes.mean()),
        "time_within_5": float(np.mean(np.abs(time_pred - minutes) <= 5)),
        "importance": dict(zip(FEATURES, classifier.feature_importances_.round(4).tolist())),
        "time_importance": dict(zip(FEATURES + ["esi"], regressor.feature_importances_.round(4).tolist())),
        "train_seconds": train_seconds,
    }
    (MODEL_DIR / "metrics.json").write_text(json.dumps(metrics, indent=2))

    print(f"samples            {n}  (classes {metrics['class_counts']})")
    print(f"accuracy (5-fold)  {metrics['accuracy']:.3f}   macro F1 {metrics['macro_f1']:.3f}")
    print(f"under / over       {under:.3f} / {over:.3f}")
    print(f"with safety rules  {metrics['safe_accuracy']:.3f}   under {safe_under:.3f} / over {safe_over:.3f}")
    print(f"NEWS2 baseline     {metrics['baseline_accuracy']:.3f}   F1 {metrics['baseline_macro_f1']:.3f}  under {base_under:.3f}")
    print(f"time MAE           {metrics['time_mae']:.2f} min (mean {metrics['time_mean']:.1f}), within 5 min {metrics['time_within_5']:.2f}")
    print(f"trained in         {train_seconds:.2f}s")
    return metrics


if __name__ == "__main__":
    main()
