"""
MediQueue - AI-assisted emergency department queue.

    python seed.py      # once: creates mediqueue.db with demo staff, beds and patients
    python app.py       # then open http://localhost:5000

Logins (password demo123): nurse priya, doctors rao / mehta / iyer / khan, admin admin
"""

import json
import os
from functools import wraps
from pathlib import Path


from flask import Flask, flash, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

import beds
import dispatch
from ai import triage_model, wait_model
from ai.dataset import COMPLAINT_LABELS, COMPLAINTS, CONSCIOUSNESS
from datetime import timedelta

from db import connect, log_event, minutes_between, now, setting, stamp, today as today_date
from scheduler import AGING_MINUTES, TARGET_MINUTES, effective_level, estimate_waits, order_queue

BASE = Path(__file__).resolve().parent
# results written at runtime (the concurrency test) must go to a writable folder on Vercel
RESULTS_DIR = Path("/tmp") if os.environ.get("VERCEL") else BASE / "models"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "mediqueue-dev-secret-change-me")


def ensure_database():
    """Create and seed the demo database the first time the app starts (always the case on a fresh Vercel instance)."""
    from db import DB_PATH
    if not DB_PATH.exists():
        import seed
        seed.main()


ensure_database()

# each server process gets an id; handy for seeing which Vercel instance answered a request
INSTANCE_ID = os.urandom(4).hex()


@app.after_request
def tag_instance(response):
    response.headers["X-MediQueue-Instance"] = INSTANCE_ID
    return response

VITAL_FIELDS = ["heart_rate", "systolic_bp", "resp_rate", "spo2", "temperature", "consciousness", "pain"]


# ---------------------------------------------------------------- helpers

def get_db():
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def current_user():
    uid = session.get("user_id")
    if uid is None:
        return None
    return get_db().execute("SELECT * FROM users WHERE user_id = ?", (uid,)).fetchone()


def login_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if user is None:
                return redirect(url_for("login", next=request.path))
            if roles and user["role"] not in roles:
                flash("You don't have access to that page.", "error")
                return redirect(url_for("home"))
            g.user = user
            return view(*args, **kwargs)
        return wrapped
    return decorator


@app.context_processor
def inject_globals():
    return {"me": g.get("user"), "esi_targets": TARGET_MINUTES, "complaints": COMPLAINT_LABELS,
            "consciousness_levels": CONSCIOUSNESS}


def vitals_from(form):
    v = {"age": int(form["age"]), "complaint": form["complaint"]}
    for f in VITAL_FIELDS:
        v[f] = float(form[f]) if f == "temperature" else int(form[f])
    if v["complaint"] not in COMPLAINTS:
        raise ValueError("unknown complaint")
    return v


def round5(x):
    return int(5 * round(x / 5))


# ---------------------------------------------------------------- live state (used by every screen)

def build_state(conn):
    aging = setting(conn, "aging_minutes", AGING_MINUTES)
    now_min = dispatch.now_minutes()

    rows = conn.execute("SELECT * FROM v_queue").fetchall()
    waiting_rows = [r for r in rows if r["status"] == "waiting"]
    jobs = {r["visit_id"]: dispatch.as_job(r) for r in rows}
    waiting_jobs = [jobs[r["visit_id"]] for r in waiting_rows]

    doctors = conn.execute("SELECT * FROM users WHERE role = 'doctor' ORDER BY room").fetchall()
    on_duty = [d for d in doctors if d["on_duty"]]
    in_consult = {r["doctor_id"]: r for r in rows if r["status"] == "in_consult"}
    busy_until = [now_min + max(1, jobs[r["visit_id"]]["predicted"] - jobs[r["visit_id"]]["consult_done"])
                  for d_id, r in in_consult.items() if any(d["user_id"] == d_id for d in on_duty)]
    free_doctors = sum(1 for d in on_duty if d["user_id"] not in in_consult)

    # waiting-time estimate: scheduler replay, corrected by the ML model
    eta = {}
    if on_duty:
        plain = estimate_waits(waiting_jobs, busy_until, free_doctors, now_min, aging)
        recent = conn.execute("SELECT COUNT(*) FROM visits WHERE arrived_at >= ?",
                              (stamp(now() - timedelta(minutes=60)),)).fetchone()[0]
        hour = now().hour + now().minute / 60
        feats = [wait_model.features(j, waiting_jobs, now_min, len(in_consult), len(on_duty), recent,
                                     plain.get(j["id"], 0.0), hour, aging) for j in waiting_jobs]
        for j, ml in zip(waiting_jobs, wait_model.predict(feats)):
            eta[j["id"]] = {"plain": plain.get(j["id"], 0.0), "ml": ml}

    by_id = {r["visit_id"]: r for r in rows}
    queue = []
    for pos, job in enumerate(order_queue(waiting_jobs, now_min, aging_minutes=aging), start=1):
        r = by_id[job["id"]]
        waited = now_min - job["arrived"]
        est = eta.get(job["id"])
        queue.append({
            "id": r["visit_id"], "position": pos, "token": r["token"], "name": r["patient_name"],
            "age": r["age"], "sex": r["sex"], "esi": r["esi"], "ai_esi": r["ai_esi"],
            "overridden": r["esi"] != r["ai_esi"], "complaint": COMPLAINT_LABELS[r["complaint"]],
            "waited": round(waited), "effective": effective_level(r["esi"], waited, aging),
            "predicted": r["predicted_minutes"], "paused": r["paused_count"] > 0,
            "over_target": waited > max(TARGET_MINUTES[r["esi"]], 1),
            "recheck": waited > max(30, TARGET_MINUTES[r["esi"]]) and r["esi"] >= 3,
            "eta": None if est is None else round(est["ml"]),
            "eta_range": None if est is None else [round5(max(0, est["ml"] * 0.75)), round5(est["ml"] * 1.25 + 5)],
            "spo2": r["spo2"], "heart_rate": r["heart_rate"],
        })

    doctor_list = []
    for d in doctors:
        cur = in_consult.get(d["user_id"])
        doctor_list.append({
            "id": d["user_id"], "name": d["name"], "room": d["room"], "on_duty": bool(d["on_duty"]),
            "current": None if cur is None else {
                "id": cur["visit_id"], "token": cur["token"], "name": cur["patient_name"], "esi": cur["esi"],
                "complaint": COMPLAINT_LABELS[cur["complaint"]], "predicted": cur["predicted_minutes"],
                "done": round(jobs[cur["visit_id"]]["consult_done"]),
            },
        })

    bed_rows = conn.execute("""SELECT b.*, v.token, v.esi, p.name AS patient_name FROM beds b
                               LEFT JOIN visits v ON v.visit_id = b.visit_id
                               LEFT JOIN patients p ON p.patient_id = v.patient_id ORDER BY b.bed_id""").fetchall()
    boarding = conn.execute("""SELECT v.visit_id, v.token, v.esi, v.finished_at, p.name FROM visits v
                               JOIN patients p ON p.patient_id = v.patient_id
                               WHERE v.status = 'boarding' ORDER BY v.esi, v.finished_at""").fetchall()

    today = conn.execute("""
        SELECT COUNT(*) AS seen,
               AVG((julianday(first_seen_at) - julianday(arrived_at)) * 1440) AS avg_wait,
               AVG(CASE WHEN esi <= 2 THEN (julianday(first_seen_at) - julianday(arrived_at)) * 1440 END) AS critical_wait,
               (SELECT COUNT(*) FROM visits WHERE esi <> ai_esi AND date(arrived_at) = :today) AS overrides
        FROM visits WHERE first_seen_at IS NOT NULL AND date(arrived_at) = :today""", {"today": today_date()}).fetchone()
    left = conn.execute("SELECT COUNT(*) FROM visits WHERE status = 'left' AND date(arrived_at) = ?",
                        (today_date(),)).fetchone()[0]
    events = conn.execute("""SELECT e.at, e.kind, e.detail, v.token, u.name AS who FROM events e
                             LEFT JOIN visits v ON v.visit_id = e.visit_id
                             LEFT JOIN users u ON u.user_id = e.user_id ORDER BY e.event_id DESC LIMIT 14""").fetchall()

    return {
        "aging": aging,
        "queue": queue,
        "doctors": doctor_list,
        "preempt_doctor": dispatch.who_should_preempt(conn),
        "beds": [{"id": b["bed_id"], "label": b["label"], "ward": b["ward"], "status": b["status"],
                  "token": b["token"], "name": b["patient_name"], "esi": b["esi"]} for b in bed_rows],
        "semaphore": beds.semaphore_value(conn),
        "boarding": [{"id": b["visit_id"], "token": b["token"], "esi": b["esi"], "name": b["name"],
                      "since": round(minutes_between(b["finished_at"]))} for b in boarding],
        "stats": {
            "waiting": len(queue), "in_consult": len(in_consult), "seen_today": today["seen"] or 0,
            "avg_wait": round(today["avg_wait"] or 0, 1), "critical_wait": round(today["critical_wait"] or 0, 1),
            "overrides": today["overrides"] or 0, "left": left, "on_duty": len(on_duty),
        },
        "events": [{"at": e["at"][11:16], "kind": e["kind"], "detail": e["detail"], "token": e["token"], "who": e["who"]}
                   for e in events],
    }


# ---------------------------------------------------------------- auth

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = get_db().execute("SELECT * FROM users WHERE username = ?",
                                (request.form["username"].strip().lower(),)).fetchone()
        if user and check_password_hash(user["password_hash"], request.form["password"]):
            session.clear()
            session["user_id"] = user["user_id"]
            return redirect(request.args.get("next") or url_for("home"))
        flash("Wrong username or password.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required()
def home():
    return redirect(url_for({"nurse": "triage", "doctor": "doctor", "admin": "admin"}[g.user["role"]]))


@app.route("/api/state")
@login_required()
def api_state():
    return jsonify(build_state(get_db()))


# ---------------------------------------------------------------- triage desk (nurse)

@app.route("/triage", methods=["GET"])
@login_required("nurse", "admin")
def triage():
    retriage = None
    if request.args.get("retriage"):
        retriage = get_db().execute("SELECT v.*, p.name, p.age, p.sex FROM visits v JOIN patients p "
                                    "ON p.patient_id = v.patient_id WHERE visit_id = ?",
                                    (request.args["retriage"],)).fetchone()
    return render_template("triage.html", page="triage", retriage=retriage)


@app.route("/api/triage/preview", methods=["POST"])
@login_required("nurse", "admin")
def triage_preview():
    try:
        v = vitals_from(request.get_json())
    except (KeyError, ValueError, TypeError):
        return jsonify({"error": "incomplete"}), 400
    return jsonify(triage_model.suggest(v))


@app.route("/triage", methods=["POST"])
@login_required("nurse", "admin")
def triage_submit():
    conn = get_db()
    f = request.form
    try:
        v = vitals_from(f)
    except (KeyError, ValueError):
        flash("Please fill in every vital sign.", "error")
        return redirect(url_for("triage"))
    ai = triage_model.suggest(v)
    final = int(f.get("final_esi") or ai["esi"])
    reason = f.get("override_reason", "").strip() or None
    if final != ai["esi"] and not reason:
        flash("Please write a short reason when you change the AI suggestion.", "error")
        return redirect(url_for("triage"))
    minutes = ai["minutes"] if final == ai["esi"] else triage_model.predict_minutes(v, final)

    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute("INSERT INTO patients (name, age, sex, phone, created_at) VALUES (?, ?, ?, ?, ?)",
                           (f["name"].strip(), v["age"], f["sex"], f.get("phone") or None, stamp()))
        count = conn.execute("SELECT COUNT(*) FROM visits WHERE date(arrived_at) = ?", (today_date(),)).fetchone()[0]
        token = f"T{count + 1:03d}"
        while conn.execute("SELECT 1 FROM visits WHERE token = ?", (token,)).fetchone():
            count += 1
            token = f"T{count + 1:03d}"
        cur = conn.execute(
            """INSERT INTO visits (patient_id, token, arrived_at, complaint, notes, heart_rate, systolic_bp, resp_rate,
                                   spo2, temperature, consciousness, pain, ai_esi, ai_confidence, esi,
                                   override_reason, predicted_minutes, triaged_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (cur.lastrowid, token, stamp(), v["complaint"], f.get("notes"), v["heart_rate"], v["systolic_bp"],
             v["resp_rate"], v["spo2"], v["temperature"], v["consciousness"], v["pain"], ai["esi"],
             ai["confidence"], final, reason, minutes, g.user["user_id"]))
        detail = f"ESI {final} (AI suggested {ai['esi']}{', overridden: ' + reason if reason else ''})"
        log_event(conn, "triaged", detail, cur.lastrowid, g.user["user_id"])
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    flash(f"Token {token} issued - ESI {final}, expected consultation about {minutes} min.", "success")
    return redirect(url_for("triage"))


@app.route("/visits/<int:visit_id>/retriage", methods=["POST"])
@login_required("nurse", "admin")
def retriage(visit_id):
    conn = get_db()
    visit = conn.execute("SELECT * FROM visits WHERE visit_id = ? AND status = 'waiting'", (visit_id,)).fetchone()
    if visit is None:
        flash("That patient is no longer waiting.", "error")
        return redirect(url_for("triage"))
    v = vitals_from(request.form)
    ai = triage_model.suggest(v)
    new_esi = min(visit["esi"], ai["esi"])  # re-triage can raise urgency, never quietly lower it
    if request.form.get("final_esi"):
        new_esi = int(request.form["final_esi"])
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("""UPDATE visits SET heart_rate = ?, systolic_bp = ?, resp_rate = ?, spo2 = ?, temperature = ?,
                    consciousness = ?, pain = ?, ai_esi = ?, ai_confidence = ?, esi = ?, predicted_minutes = ?
                    WHERE visit_id = ?""",
                 (v["heart_rate"], v["systolic_bp"], v["resp_rate"], v["spo2"], v["temperature"], v["consciousness"],
                  v["pain"], ai["esi"], ai["confidence"], new_esi, triage_model.predict_minutes(v, new_esi), visit_id))
    log_event(conn, "retriaged", f"ESI {visit['esi']} -> {new_esi} after new vitals", visit_id, g.user["user_id"])
    conn.execute("COMMIT")
    flash(f"{visit['token']} re-triaged: ESI {visit['esi']} → {new_esi}.", "success")
    return redirect(url_for("triage"))


@app.route("/visits/<int:visit_id>/left", methods=["POST"])
@login_required("nurse", "admin")
def left_without_seen(visit_id):
    conn = get_db()
    conn.execute("BEGIN IMMEDIATE")
    cur = conn.execute("UPDATE visits SET status = 'left', finished_at = ? WHERE visit_id = ? AND status = 'waiting'",
                       (stamp(), visit_id))
    if cur.rowcount:
        log_event(conn, "left", "left without being seen", visit_id, g.user["user_id"])
    conn.execute("COMMIT")
    return redirect(url_for("triage"))


# ---------------------------------------------------------------- doctor console

@app.route("/doctor")
@login_required("doctor")
def doctor():
    return render_template("doctor.html", page="doctor")


@app.route("/api/doctor/patient")
@login_required("doctor")
def doctor_patient():
    row = get_db().execute("""SELECT v.*, p.name, p.age, p.sex FROM visits v JOIN patients p ON p.patient_id = v.patient_id
                              WHERE v.doctor_id = ? AND v.status = 'in_consult'""", (g.user["user_id"],)).fetchone()
    if row is None:
        return jsonify(None)
    data = {k: row[k] for k in row.keys()}
    data["complaint_label"] = COMPLAINT_LABELS[row["complaint"]]
    data["consciousness_label"] = CONSCIOUSNESS[row["consciousness"]]
    data["waited"] = round(minutes_between(row["arrived_at"], None) - minutes_between(row["first_seen_at"], None))
    data["done"] = round((row["consult_done_min"] or 0) + minutes_between(row["consult_started_at"]))
    return jsonify(data)


@app.route("/doctor/duty", methods=["POST"])
@login_required("doctor")
def doctor_duty():
    conn = get_db()
    conn.execute("UPDATE users SET on_duty = 1 - on_duty WHERE user_id = ?", (g.user["user_id"],))
    return redirect(url_for("doctor"))


@app.route("/doctor/<action>", methods=["POST"])
@login_required("doctor")
def doctor_action(action):
    conn = get_db()
    me = g.user["user_id"]
    try:
        if action == "call-next":
            visit_id = dispatch.call_next(conn, me)
            if visit_id is None:
                flash("Nobody is waiting right now.", "info")
            else:
                token = conn.execute("SELECT token FROM visits WHERE visit_id = ?", (visit_id,)).fetchone()[0]
                flash(f"Please call token {token} to {g.user['room']}.", "success")
        elif action == "preempt":
            visit_id = dispatch.preempt_for_critical(conn, me)
            token = conn.execute("SELECT token FROM visits WHERE visit_id = ?", (visit_id,)).fetchone()[0]
            flash(f"Current patient paused and kept at the front of their level. Attend {token} now.", "error")
        elif action in ("discharge", "admit"):
            msg = dispatch.finish(conn, me, "admit" if action == "admit" else "discharged")
            flash(f"Patient {msg}.", "success")
    except dispatch.DispatchError as e:
        flash(str(e), "error")
    return redirect(url_for("doctor"))


# ---------------------------------------------------------------- beds

@app.route("/beds")
@login_required()
def bed_board():
    return render_template("beds.html", page="beds")


@app.route("/beds/<int:bed_id>/<action>", methods=["POST"])
@login_required()
def bed_action(bed_id, action):
    conn = get_db()
    if action == "release":
        beds.release_bed(conn, bed_id, g.user["user_id"])
    elif action == "clean":
        got = beds.signal_bed(conn, bed_id, g.user["user_id"])
        if got:
            token = conn.execute("SELECT token FROM visits WHERE visit_id = ?", (got,)).fetchone()[0]
            flash(f"Bed given straight to boarding patient {token}.", "success")
    return redirect(url_for("bed_board"))


# ---------------------------------------------------------------- waiting room display (no login, no names)

@app.route("/board")
def board():
    return render_template("board.html", page="board")


@app.route("/api/board")
def api_board():
    state = build_state(get_db())
    calling = [{"token": d["current"]["token"], "room": d["room"]} for d in state["doctors"] if d["current"]]
    waiting = [{"token": q["token"], "eta_range": q["eta_range"], "urgent": q["esi"] <= 2} for q in state["queue"]]
    return jsonify({"calling": calling, "waiting": waiting, "doctors_on_duty": state["stats"]["on_duty"]})


# ---------------------------------------------------------------- admin

def _load_json(name):
    for folder in (RESULTS_DIR, BASE / "models"):
        path = folder / name
        if path.exists():
            return json.loads(path.read_text())
    return None


@app.route("/admin")
@login_required("admin")
def admin():
    return render_template("admin.html", page="admin", aging=setting(get_db(), "aging_minutes", AGING_MINUTES),
                           metrics=_load_json("metrics.json"),
                           simulation=_load_json("simulation.json"), race=_load_json("race.json"))


@app.route("/api/admin/reports")
@login_required("admin")
def admin_reports():
    conn = get_db()
    by_esi = conn.execute("""SELECT esi, COUNT(*) AS n,
                                    AVG((julianday(first_seen_at) - julianday(arrived_at)) * 1440) AS wait
                             FROM visits WHERE first_seen_at IS NOT NULL GROUP BY esi ORDER BY esi""").fetchall()
    hourly = conn.execute("""SELECT CAST(strftime('%H', arrived_at) AS INTEGER) AS hour, COUNT(*) AS n
                             FROM visits WHERE date(arrived_at) = ? GROUP BY hour""", (today_date(),)).fetchall()
    agreement = conn.execute("SELECT ai_esi, esi, COUNT(*) AS n FROM visits GROUP BY ai_esi, esi").fetchall()
    return jsonify({
        "by_esi": [dict(r) for r in by_esi],
        "hourly": [dict(r) for r in hourly],
        "agreement": [dict(r) for r in agreement],
        "simulation": _load_json("simulation.json"),
        "metrics": _load_json("metrics.json"),
        "race": _load_json("race.json"),
    })


@app.route("/admin/settings", methods=["POST"])
@login_required("admin")
def admin_settings():
    minutes = max(5, min(240, int(request.form["aging_minutes"])))
    conn = get_db()
    conn.execute("INSERT INTO settings (key, value) VALUES ('aging_minutes', ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(minutes),))
    flash(f"Aging interval set to {minutes} minutes.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/race-test", methods=["POST"])
@login_required("admin")
def admin_race_test():
    import race_test
    result = race_test.run_all(trials=int(request.form.get("trials", 15)))
    (RESULTS_DIR / "race.json").write_text(json.dumps(result, indent=2))
    flash("Concurrency test finished.", "success")
    return redirect(url_for("admin") + "#race")


if __name__ == "__main__":
    app.run(debug=False, threaded=True, port=5000)
