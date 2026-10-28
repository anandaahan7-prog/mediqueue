"""
MediQueue - AI-assisted emergency department queue.

    python seed.py      # once: creates mediqueue.db with demo staff, beds and patients
    python app.py       # then open http://localhost:5000

Logins (password demo123): nurse priya, doctors rao / mehta / iyer / khan, admin admin
"""

import hashlib
import hmac
import io
import json
import math
import os
import re
import secrets
import threading
import time
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
from scheduler import (AGING_MINUTES, RISK_CAP_THRESHOLD, TARGET_MINUTES, aging_cap, aging_interval, effective_level,
                       estimate_waits, order_queue, risk_of)

BASE = Path(__file__).resolve().parent
# results written at runtime (the concurrency test) must go to a writable folder on Vercel
RESULTS_DIR = Path("/tmp") if os.environ.get("VERCEL") else BASE / "models"

# Static files live in public/static: on Vercel everything under public/ is served by the CDN,
# so the CSS, JavaScript and fonts never wake up (or multiply) the Python function.
app = Flask(__name__, static_folder="public/static", static_url_path="/static")
app.secret_key = os.environ.get("SECRET_KEY", "mediqueue-dev-secret-change-me")


def ensure_database():
    """Create and seed the demo database the first time the app starts (always the case on a fresh Vercel instance)."""
    from db import DB_PATH
    if not DB_PATH.exists():
        import seed
        seed.main()


ensure_database()
wait_model.predict([[0] * 8])  # load the models now, not on the first poll after a cold start

def asset_version():
    """Changes whenever the CSS/JS change, so the CDN and browsers can cache static files for a year.
    On Vercel the public/ folder goes to the CDN and is not in the function bundle, so there the
    deployment id (new on every deploy) does the same job."""
    try:
        return hashlib.sha1(b"".join((BASE / "public/static" / f).read_bytes()
                                     for f in ("app.css", "app.js", "charts.js"))).hexdigest()[:10]
    except OSError:
        return (os.environ.get("VERCEL_DEPLOYMENT_ID") or os.environ.get("VERCEL_GIT_COMMIT_SHA") or "1")[-10:]


ASSET_VERSION = asset_version()


@app.url_defaults
def versioned_static(endpoint, values):
    if endpoint == "static":
        values.setdefault("v", ASSET_VERSION)


def wants_json():
    """Buttons on live screens post with fetch() and only need a short answer, not a redirect."""
    return request.headers.get("X-Requested-With") == "fetch"


def reply(message, category, url):
    """Answer an action: JSON for fetch(), otherwise flash + redirect (works without JavaScript)."""
    if wants_json():
        return jsonify({"message": message, "category": category})
    if message:
        flash(message, category)
    return redirect(url)

# each server process gets an id; handy for seeing which Vercel instance answered a request
INSTANCE_ID = os.urandom(4).hex()


@app.after_request
def tag_instance(response):
    response.headers["X-MediQueue-Instance"] = INSTANCE_ID
    return response

VITAL_FIELDS = ["heart_rate", "systolic_bp", "resp_rate", "spo2", "temperature", "consciousness", "pain"]

# what a patient can report from their own page ("I feel worse"); the first three are red flags for the nurse
SYMPTOMS = {"breathing": "Harder to breathe", "chest": "Chest pain or tightness", "faint": "Dizzy or feeling faint",
            "pain": "Pain is worse", "bleeding": "Bleeding more", "other": "Something else"}
RED_FLAGS = {"breathing", "chest", "faint"}
STEP_AWAY_MINUTES = (10, 20)
STEP_AWAY_LIMIT = 2  # times per visit
PATIENT_KINDS = {"worse", "away", "back", "left"}  # events a patient can cause from their own page


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


def shift_start(hours=12):
    """Reports cover the last 12 hours rather than the calendar day, so they still make sense after midnight."""
    return stamp(now() - timedelta(hours=hours))


def round5(x):
    return int(5 * round(x / 5))


def qr_svg(url):
    """QR code for the patient's wait page, as inline SVG (no image files, works offline)."""
    import segno
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="svg", scale=5, border=2, dark="#14201b", light="#ffffff",
                                    xmldecl=False, svgclass="qr", lineclass=None, omitsize=True,
                                    title="Scan to follow your wait")
    return buf.getvalue().decode()


def new_visit(conn, v, ai, final, reason, minutes, name, sex, phone, notes, user_id, ambulance=False, reserved_for=None):
    """Insert patient + visit and issue the next token. Call inside BEGIN IMMEDIATE ... COMMIT."""
    cur = conn.execute("INSERT INTO patients (name, age, sex, phone, created_at) VALUES (?, ?, ?, ?, ?)",
                       (name, v["age"], sex, phone or None, stamp()))
    count = conn.execute("SELECT COUNT(*) FROM visits WHERE date(arrived_at) = ?", (today_date(),)).fetchone()[0]
    token = f"T{count + 1:03d}"
    while conn.execute("SELECT 1 FROM visits WHERE token = ?", (token,)).fetchone():
        count += 1
        token = f"T{count + 1:03d}"
    cur = conn.execute(
        """INSERT INTO visits (patient_id, token, arrived_at, complaint, notes, heart_rate, systolic_bp, resp_rate,
                               spo2, temperature, consciousness, pain, ai_esi, ai_confidence, esi, override_reason,
                               ai_risk, predicted_minutes, public_code, ambulance, reserved_for, triaged_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (cur.lastrowid, token, stamp(), v["complaint"], notes, v["heart_rate"], v["systolic_bp"], v["resp_rate"],
         v["spo2"], v["temperature"], v["consciousness"], v["pain"], ai["esi"], ai["confidence"], final, reason,
         risk_of(ai["probs"], final), minutes, secrets.token_urlsafe(6), int(ambulance), reserved_for, user_id))
    detail = f"ESI {final} (AI suggested {ai['esi']}{', overridden: ' + reason if reason else ''})"
    if ambulance:
        detail = "ambulance arrival, " + detail
    log_event(conn, "triaged", detail, cur.lastrowid, user_id)
    return cur.lastrowid, token


def triage_decision(form, v):
    """AI suggestion + the nurse's final level for a submitted form, or an error message."""
    ai = triage_model.suggest(v)
    final = int(form.get("final_esi") or ai["esi"])
    reason = form.get("override_reason", "").strip() or None
    if final != ai["esi"] and not reason:
        return None, "Please write a short reason when you change the AI suggestion."
    minutes = ai["minutes"] if final == ai["esi"] else triage_model.predict_minutes(v, final)
    return (ai, final, reason, minutes), None


# ---------------------------------------------------------------- live state (used by every screen)

def build_state(conn):
    aging = setting(conn, "aging_minutes", AGING_MINUTES)
    policy = dispatch.queue_policy(conn)
    adaptive = policy == "mediqueue"
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
        plain = estimate_waits(waiting_jobs, busy_until, free_doctors, now_min, aging, policy)
        recent = conn.execute("SELECT COUNT(*) FROM visits WHERE arrived_at >= ?",
                              (stamp(now() - timedelta(minutes=60)),)).fetchone()[0]
        hour = now().hour + now().minute / 60
        feats = [wait_model.features(j, waiting_jobs, now_min, len(in_consult), len(on_duty), recent,
                                     plain.get(j["id"], 0.0), hour, aging) for j in waiting_jobs]
        for j, ml in zip(waiting_jobs, wait_model.predict(feats)):
            eta[j["id"]] = {"plain": plain.get(j["id"], 0.0), "ml": ml}

    by_id = {r["visit_id"]: r for r in rows}
    ts = stamp()
    reports = conn.execute("""SELECT r.*, v.token, v.esi, p.name FROM patient_reports r
                              JOIN visits v ON v.visit_id = r.visit_id JOIN patients p ON p.patient_id = v.patient_id
                              WHERE r.status = 'open' AND v.status = 'waiting' ORDER BY r.at""").fetchall()
    help_list = sorted(({"id": r["report_id"], "visit_id": r["visit_id"], "token": r["token"], "esi": r["esi"],
                         "name": r["name"], "symptoms": [SYMPTOMS[k] for k in r["symptoms"].split(",")],
                         "red_flag": bool(RED_FLAGS & set(r["symptoms"].split(","))),
                         "ago": round(minutes_between(r["at"]))} for r in reports),
                       key=lambda h: (not h["red_flag"], -h["ago"]))
    feels_worse = {h["visit_id"] for h in help_list}
    queue = []
    for pos, job in enumerate(order_queue(waiting_jobs, now_min, policy, aging), start=1):
        r = by_id[job["id"]]
        waited = now_min - job["arrived"]
        est = eta.get(job["id"])
        risk = r["ai_risk"] or 0.0
        queue.append({
            "id": r["visit_id"], "position": pos, "token": r["token"], "name": r["patient_name"],
            "age": r["age"], "sex": r["sex"], "esi": r["esi"], "ai_esi": r["ai_esi"],
            "overridden": r["esi"] != r["ai_esi"], "complaint": COMPLAINT_LABELS[r["complaint"]],
            "waited": round(waited), "effective": effective_level(r["esi"], waited, aging, risk, adaptive),
            "arrived_min": job["arrived"], "risk": round(risk, 2), "ambulance": bool(r["ambulance"]),
            "high_risk": adaptive and r["esi"] >= 3 and risk >= RISK_CAP_THRESHOLD,
            "aging_every": round(aging_interval(risk, aging, adaptive)) if r["esi"] >= 3 else None,
            "aging_cap": aging_cap(risk, adaptive),
            "predicted": r["predicted_minutes"], "paused": r["paused_count"] > 0,
            "over_target": waited > max(TARGET_MINUTES[r["esi"]], 1),
            "recheck": waited > max(30, TARGET_MINUTES[r["esi"]]) and r["esi"] >= 3,
            "eta": None if est is None else round(est["ml"]),
            "eta_range": None if est is None else [round5(max(0, est["ml"] * 0.75)), round5(est["ml"] * 1.25 + 5)],
            "spo2": r["spo2"], "heart_rate": r["heart_rate"],
            "away": dispatch.is_away(r, ts), "away_until": r["away_until"][11:16] if dispatch.is_away(r, ts) else None,
            "worse": r["visit_id"] in feels_worse,
        })

    alerts = conn.execute("""SELECT a.*, u.name AS doctor_name, u.room FROM prealerts a
                            LEFT JOIN users u ON u.user_id = a.doctor_id
                            WHERE a.status = 'incoming' ORDER BY a.eta_at""").fetchall()
    incoming = [{"id": a["alert_id"], "code": a["code"], "esi": a["esi"], "age": a["age"], "sex": a["sex"],
                 "complaint": COMPLAINT_LABELS[a["complaint"]], "eta_min": round(dispatch.minutes_of(a["eta_at"]) - now_min),
                 "doctor_id": a["doctor_id"], "doctor": a["doctor_name"], "room": a["room"]} for a in alerts]
    held = {a["doctor_id"]: a for a in incoming if a["doctor_id"]}

    doctor_list = []
    for d in doctors:
        cur = in_consult.get(d["user_id"])
        h = held.get(d["user_id"])
        doctor_list.append({
            "id": d["user_id"], "name": d["name"], "room": d["room"], "on_duty": bool(d["on_duty"]),
            "held": None if h is None else {"code": h["code"], "esi": h["esi"], "eta_min": h["eta_min"]},
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
               (SELECT COUNT(*) FROM visits WHERE esi <> ai_esi AND arrived_at >= :since) AS overrides
        FROM visits WHERE first_seen_at IS NOT NULL AND arrived_at >= :since""", {"since": shift_start()}).fetchone()
    left = conn.execute("SELECT COUNT(*) FROM visits WHERE status = 'left' AND arrived_at >= ?",
                        (shift_start(),)).fetchone()[0]
    patient_side = conn.execute("""
        SELECT (SELECT COUNT(*) FROM patient_reports WHERE at >= :since) AS reports,
               (SELECT AVG((julianday(handled_at) - julianday(at)) * 1440) FROM patient_reports
                 WHERE at >= :since AND handled_at IS NOT NULL) AS response,
               (SELECT COUNT(*) FROM events WHERE kind = 'away' AND at >= :since) AS stepped_away,
               (SELECT COUNT(*) FROM events WHERE kind = 'left' AND user_id IS NULL AND at >= :since) AS left_told""",
                                {"since": shift_start()}).fetchone()
    events = conn.execute("""SELECT e.at, e.kind, e.detail, e.user_id, v.token, u.name AS who FROM events e
                             LEFT JOIN visits v ON v.visit_id = e.visit_id
                             LEFT JOIN users u ON u.user_id = e.user_id ORDER BY e.at DESC, e.event_id DESC LIMIT 14""").fetchall()

    return {
        "aging": aging,
        "aging_mode": "adaptive" if adaptive else "fixed",
        "hold_rooms": setting(conn, "hold_rooms", 1) == 1,
        "incoming": incoming,
        "help": help_list,
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
            "reports": patient_side["reports"], "stepped_away": patient_side["stepped_away"],
            "left_told": patient_side["left_told"],
            "report_response": None if patient_side["response"] is None else round(patient_side["response"], 1),
        },
        "events": [{"at": e["at"][11:16], "kind": e["kind"], "detail": e["detail"], "token": e["token"],
                    "who": e["who"] or ("patient's page" if e["user_id"] is None and e["kind"] in PATIENT_KINDS else None)}
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
    conn = get_db()
    state = build_state(conn)
    if g.user["role"] == "doctor":
        # sent in the same response so the console never mixes two different snapshots
        state["my_patient"] = current_patient_of(conn, g.user["user_id"])
    return jsonify(state)


# ---------------------------------------------------------------- triage desk (nurse)

@app.route("/triage", methods=["GET"])
@login_required("nurse", "admin")
def triage():
    retriage = slip = None
    if request.args.get("retriage"):
        retriage = get_db().execute("SELECT v.*, p.name, p.age, p.sex FROM visits v JOIN patients p "
                                    "ON p.patient_id = v.patient_id WHERE visit_id = ?",
                                    (request.args["retriage"],)).fetchone()
    if request.args.get("slip"):
        row = get_db().execute("""SELECT v.token, v.esi, v.public_code, p.phone FROM visits v
                                  JOIN patients p ON p.patient_id = v.patient_id WHERE v.visit_id = ?""",
                               (request.args["slip"],)).fetchone()
        if row is not None and row["public_code"]:
            url = url_for("wait_page", code=row["public_code"], _external=True)
            slip = {"token": row["token"], "esi": row["esi"], "url": url, "qr": qr_svg(url),
                    "find_url": url_for("patient_login", _external=True).split("://", 1)[-1] if row["phone"] else None}
    return render_template("triage.html", page="triage", retriage=retriage, slip=slip)


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
    decision, error = triage_decision(f, v)
    if error:
        flash(error, "error")
        return redirect(url_for("triage"))
    ai, final, reason, minutes = decision

    conn.execute("BEGIN IMMEDIATE")
    try:
        visit_id, token = new_visit(conn, v, ai, final, reason, minutes, f["name"].strip(), f["sex"], f.get("phone"),
                                    f.get("notes"), g.user["user_id"])
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    flash(f"Token {token} issued - ESI {final}, expected consultation about {minutes} min.", "success")
    return redirect(url_for("triage", slip=visit_id))


# ---------------------------------------------------------------- ambulance pre-alerts

@app.route("/prealert", methods=["GET"])
@login_required("nurse", "admin")
def prealert():
    return render_template("prealert.html", page="prealert")


@app.route("/prealert", methods=["POST"])
@login_required("nurse", "admin")
def prealert_submit():
    conn = get_db()
    f = request.form
    try:
        v = vitals_from(f)
        eta = max(1, min(90, int(f["eta_minutes"])))
    except (KeyError, ValueError):
        flash("Please fill in the vital signs and the arrival time.", "error")
        return redirect(url_for("prealert"))
    decision, error = triage_decision(f, v)
    if error:
        flash(error, "error")
        return redirect(url_for("prealert"))
    ai, final, reason, minutes = decision

    conn.execute("BEGIN IMMEDIATE")
    try:
        count = conn.execute("SELECT COUNT(*) FROM prealerts WHERE date(created_at) = ?", (today_date(),)).fetchone()[0]
        code = f"A{count + 1:02d}"
        while conn.execute("SELECT 1 FROM prealerts WHERE code = ?", (code,)).fetchone():
            count += 1
            code = f"A{count + 1:02d}"
        alert_id = conn.execute(
            """INSERT INTO prealerts (code, created_at, eta_at, patient_name, age, sex, complaint, notes, heart_rate,
                                     systolic_bp, resp_rate, spo2, temperature, consciousness, pain, ai_esi,
                                     ai_confidence, esi, override_reason, ai_risk, predicted_minutes, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (code, stamp(), stamp(now() + timedelta(minutes=eta)), f.get("name", "").strip() or None, v["age"],
             f.get("sex", "O"), v["complaint"], f.get("notes"), v["heart_rate"], v["systolic_bp"], v["resp_rate"],
             v["spo2"], v["temperature"], v["consciousness"], v["pain"], ai["esi"], ai["confidence"], final, reason,
             risk_of(ai["probs"], final), minutes, g.user["user_id"])).lastrowid
        doc = dispatch.hold_room(conn, alert_id)
        room = conn.execute("SELECT name, room FROM users WHERE user_id = ?", (doc,)).fetchone() if doc else None
        held = f"{room['room']} ({room['name']}) is held" if room else "no room held"
        log_event(conn, "prealert", f"ambulance {code}: ESI {final}, arriving in {eta} min, {held}", None,
                  g.user["user_id"])
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    if room:
        flash(f"Pre-alert {code}: ESI {final}, arriving in {eta} min. {room['room']} ({room['name']}) is held for it.",
              "success")
    else:
        why = "only ESI 1-2 ambulances get a held room" if final > 2 else "room holding is off or no doctor is on duty"
        flash(f"Pre-alert {code}: ESI {final}, arriving in {eta} min. No room held ({why}).", "info")
    return redirect(url_for("prealert"))


@app.route("/prealerts/<int:alert_id>/<action>", methods=["POST"])
@login_required("nurse", "admin")
def prealert_action(alert_id, action):
    conn = get_db()
    back = request.form.get("back", "")
    if not back.startswith("/") or back.startswith("//"):  # only ever redirect within the app
        back = url_for("prealert")
    conn.execute("BEGIN IMMEDIATE")
    try:
        a = conn.execute("SELECT * FROM prealerts WHERE alert_id = ? AND status = 'incoming'", (alert_id,)).fetchone()
        if a is None:
            conn.execute("COMMIT")
            return reply("That ambulance is no longer expected.", "error", back)
        if action == "cancel":
            conn.execute("UPDATE prealerts SET status = 'cancelled', doctor_id = NULL WHERE alert_id = ?", (alert_id,))
            log_event(conn, "prealert", f"ambulance {a['code']} cancelled", None, g.user["user_id"])
            conn.execute("COMMIT")
            return reply(f"Pre-alert {a['code']} cancelled; any held room is free again.", "info", back)
        v = {k: a[k] for k in ("age", "complaint", *VITAL_FIELDS)}
        ai = {"esi": a["ai_esi"], "confidence": a["ai_confidence"], "probs": triage_model.probabilities(v)}
        visit_id, token = new_visit(conn, v, ai, a["esi"], a["override_reason"], a["predicted_minutes"],
                                    a["patient_name"] or f"Ambulance {a['code']}", a["sex"], None, a["notes"],
                                    g.user["user_id"], ambulance=True, reserved_for=a["doctor_id"])
        conn.execute("UPDATE prealerts SET status = 'arrived', visit_id = ? WHERE alert_id = ?", (visit_id, alert_id))
        straight_in = bool(a["doctor_id"]) and dispatch.hand_over(conn, visit_id, a["doctor_id"], g.user["user_id"])
        room = conn.execute("SELECT room FROM users WHERE user_id = ?", (a["doctor_id"],)).fetchone() if a["doctor_id"] else None
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    if straight_in:
        return reply(f"Ambulance {a['code']} arrived as {token} and went straight to {room['room']}.", "success", back)
    return reply(f"Ambulance {a['code']} arrived as {token}; it is at the front of the queue.", "success", back)


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
                    consciousness = ?, pain = ?, ai_esi = ?, ai_confidence = ?, esi = ?, predicted_minutes = ?,
                    ai_risk = ? WHERE visit_id = ?""",
                 (v["heart_rate"], v["systolic_bp"], v["resp_rate"], v["spo2"], v["temperature"], v["consciousness"],
                  v["pain"], ai["esi"], ai["confidence"], new_esi, triage_model.predict_minutes(v, new_esi),
                  risk_of(ai["probs"], new_esi), visit_id))
    log_event(conn, "retriaged", f"ESI {visit['esi']} -> {new_esi} after new vitals", visit_id, g.user["user_id"])
    conn.execute("UPDATE patient_reports SET status = 'retriaged', handled_by = ?, handled_at = ? "
                 "WHERE visit_id = ? AND status = 'open'", (g.user["user_id"], stamp(), visit_id))
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
        conn.execute("UPDATE patient_reports SET status = 'closed' WHERE visit_id = ? AND status = 'open'", (visit_id,))
    conn.execute("COMMIT")
    return reply("Marked as left without being seen." if cur.rowcount else "That patient is no longer waiting.",
                 "info" if cur.rowcount else "error", url_for("triage"))


@app.route("/patient-reports/<int:report_id>/checked", methods=["POST"])
@login_required("nurse", "admin")
def report_checked(report_id):
    """The nurse went to the patient who pressed "I feel worse" and found no change in urgency."""
    conn = get_db()
    conn.execute("BEGIN IMMEDIATE")
    r = conn.execute("SELECT r.*, v.token FROM patient_reports r JOIN visits v ON v.visit_id = r.visit_id "
                     "WHERE r.report_id = ? AND r.status = 'open'", (report_id,)).fetchone()
    if r is None:
        conn.execute("COMMIT")
        return reply("That report was already handled.", "info", url_for("triage"))
    conn.execute("UPDATE patient_reports SET status = 'checked', handled_by = ?, handled_at = ? WHERE report_id = ?",
                 (g.user["user_id"], stamp(), report_id))
    log_event(conn, "checked", "nurse checked the patient's report, no change in urgency", r["visit_id"],
              g.user["user_id"])
    conn.execute("COMMIT")
    return reply(f"{r['token']} checked: no change in urgency.", "success", url_for("triage"))


# ---------------------------------------------------------------- doctor console

@app.route("/doctor")
@login_required("doctor")
def doctor():
    return render_template("doctor.html", page="doctor")


def current_patient_of(conn, doctor_id):
    row = conn.execute("""SELECT v.*, p.name, p.age, p.sex FROM visits v JOIN patients p ON p.patient_id = v.patient_id
                          WHERE v.doctor_id = ? AND v.status = 'in_consult'""", (doctor_id,)).fetchone()
    if row is None:
        return None
    data = {k: row[k] for k in row.keys()}
    data["complaint_label"] = COMPLAINT_LABELS[row["complaint"]]
    data["consciousness_label"] = CONSCIOUSNESS[row["consciousness"]]
    data["waited"] = round(minutes_between(row["arrived_at"], None) - minutes_between(row["first_seen_at"], None))
    data["done"] = round((row["consult_done_min"] or 0) + minutes_between(row["consult_started_at"]))
    return data


@app.route("/api/doctor/patient")
@login_required("doctor")
def doctor_patient():
    return jsonify(current_patient_of(get_db(), g.user["user_id"]))


@app.route("/doctor/duty", methods=["POST"])
@login_required("doctor")
def doctor_duty():
    conn = get_db()
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("UPDATE users SET on_duty = 1 - on_duty WHERE user_id = ?", (g.user["user_id"],))
    dispatch.rehold_rooms(conn, g.user["user_id"])  # an ambulance's held room moves to a doctor still on duty
    conn.execute("COMMIT")
    return redirect(url_for("doctor"))


@app.route("/doctor/<action>", methods=["POST"])
@login_required("doctor")
def doctor_action(action):
    conn = get_db()
    me = g.user["user_id"]
    back = url_for("doctor")
    try:
        if action == "call-next":
            skipped = []
            try:
                visit_id = dispatch.call_next(conn, me, skipped)
            except dispatch.RoomHeld as held:
                return reply(str(held), "info", back)
            who = " and ".join(", ".join(skipped).rsplit(", ", 1))
            if visit_id is None:
                if skipped:
                    return reply(f"Everyone waiting has stepped away for a few minutes ({who}). Try again shortly.",
                                 "info", back)
                return reply("Nobody is waiting right now.", "info", back)
            token = conn.execute("SELECT token FROM visits WHERE visit_id = ?", (visit_id,)).fetchone()[0]
            extra = (f" Skipped {who}: stepped away, {'keeps their' if len(skipped) == 1 else 'keep their'} place."
                     if skipped else "")
            return reply(f"Please call token {token} to {g.user['room']}.{extra}", "success", back)
        if action == "preempt":
            visit_id = dispatch.preempt_for_critical(conn, me)
            token = conn.execute("SELECT token FROM visits WHERE visit_id = ?", (visit_id,)).fetchone()[0]
            return reply(f"Current patient paused and kept at the front of their level. Attend {token} now.", "error", back)
        if action in ("discharge", "admit"):
            msg = dispatch.finish(conn, me, "admit" if action == "admit" else "discharged")
            return reply(f"Patient {msg}.", "success", back)
    except dispatch.DispatchError as e:
        return reply(str(e), "error", back)
    return reply("Unknown action.", "error", back)


# ---------------------------------------------------------------- beds

@app.route("/beds")
@login_required()
def bed_board():
    return render_template("beds.html", page="beds")


@app.route("/beds/<int:bed_id>/<action>", methods=["POST"])
@login_required()
def bed_action(bed_id, action):
    conn = get_db()
    label = conn.execute("SELECT label FROM beds WHERE bed_id = ?", (bed_id,)).fetchone()
    label = label["label"] if label else "Bed"
    if action == "release":
        beds.release_bed(conn, bed_id, g.user["user_id"])
        return reply(f"{label} released; it goes to cleaning.", "info", url_for("bed_board"))
    if action == "clean":
        got = beds.signal_bed(conn, bed_id, g.user["user_id"])
        if got:
            token = conn.execute("SELECT token FROM visits WHERE visit_id = ?", (got,)).fetchone()[0]
            return reply(f"{label} is clean and went straight to boarding patient {token}.", "success", url_for("bed_board"))
        return reply(f"{label} is clean and free again.", "success", url_for("bed_board"))
    return reply("Unknown action.", "error", url_for("bed_board"))


# ---------------------------------------------------------------- waiting room display (no login, no names)

@app.route("/board")
def board():
    return render_template("board.html", page="board")


@app.route("/api/board")
def api_board():
    state = build_state(get_db())
    calling = [{"token": d["current"]["token"], "room": d["room"]} for d in state["doctors"] if d["current"]]
    waiting = [{"token": q["token"], "eta_range": q["eta_range"], "urgent": q["esi"] <= 2, "away": q["away"]}
               for q in state["queue"]]
    incoming = [{"eta_min": a["eta_min"], "room": a["room"]} for a in state["incoming"]]
    return jsonify({"calling": calling, "waiting": waiting, "incoming": incoming,
                    "doctors_on_duty": state["stats"]["on_duty"]})


# ---------------------------------------------------------------- the patient's own page (QR on the token slip)
#
# Patients never get a password. The secret code in the QR link is their key; a patient who lost
# the slip signs in with their token and the last 4 digits of the phone number given at the desk.

FIND_LIMIT, FIND_WINDOW = 8, 600  # wrong tries allowed per address in 10 minutes
_find_failures = {}
_find_lock = threading.Lock()


def client_address():
    return (request.headers.get("X-Forwarded-For") or request.remote_addr or "?").split(",")[0].strip()


def too_many_tries(addr):
    with _find_lock:
        recent = [t for t in _find_failures.get(addr, []) if time.monotonic() - t < FIND_WINDOW]
        _find_failures[addr] = recent
        return len(recent) >= FIND_LIMIT


def note_failure(addr):
    with _find_lock:
        _find_failures.setdefault(addr, []).append(time.monotonic())


def normalise_token(text):
    """'t32', 'T 032' and '32' all mean T032."""
    t = re.sub(r"\s+", "", text or "").upper()
    digits = t[1:] if t.startswith("T") else t
    return f"T{int(digits):03d}" if digits.isdigit() else t


def demo_patients(conn):
    """A few made-up patients to try the patient page with, picked live from the queue
    (shown on the sign-in page like the demo staff accounts)."""
    queue = build_state(conn)["queue"]
    calm = [q for q in queue[1:-1] if q["esi"] >= 3 and not q["high_risk"] and not q["away"]][:1]
    picks = {}
    for q, label in ((queue[:1], "Next in line"), (calm, "{n} in line · may step away"), (queue[1:][-1:], "At the back")):
        for item in q:
            n = item["position"]
            picks.setdefault(item["id"], (label.format(n=f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"), item))
    if not picks:
        return []
    marks = ",".join("?" * len(picks))
    phones = {r["visit_id"]: re.sub(r"\D", "", r["phone"] or "") for r in conn.execute(
        f"SELECT v.visit_id, p.phone FROM visits v JOIN patients p ON p.patient_id = v.patient_id "
        f"WHERE v.visit_id IN ({marks})", list(picks)).fetchall()}
    return [{"token": q["token"], "esi": q["esi"], "label": label, "last4": phones[vid][-4:],
             "complaint": q["complaint"]} for vid, (label, q) in picks.items() if len(phones.get(vid, "")) >= 4]


@app.route("/patient", methods=["GET", "POST"])
def patient_login():
    conn = get_db()
    error, status = None, 200
    if request.method == "POST":
        addr = client_address()
        if too_many_tries(addr):
            error, status = "Too many tries. Please wait 10 minutes, or ask the nurse at the desk.", 429
        else:
            row = conn.execute("""SELECT v.public_code, p.phone FROM visits v JOIN patients p ON p.patient_id = v.patient_id
                                  WHERE v.token = ?""", (normalise_token(request.form.get("token")),)).fetchone()
            given = re.sub(r"\D", "", request.form.get("phone", ""))
            phone = re.sub(r"\D", "", (row["phone"] or "") if row else "")
            if row and len(given) == 4 and len(phone) >= 4 and hmac.compare_digest(phone[-4:], given):
                return redirect(url_for("wait_page", code=row["public_code"]))
            note_failure(addr)
            error = "We could not find that token with that phone number. Check your slip, or ask the nurse at the desk."
    return render_template("patient.html", page="patient", error=error, demo=demo_patients(conn),
                           token=request.form.get("token", "")), status


@app.route("/wait/<code>")
def wait_page(code):
    if get_db().execute("SELECT 1 FROM visits WHERE public_code = ?", (code,)).fetchone() is None:
        return render_template("wait.html", page="wait", code=None), 404
    return render_template("wait.html", page="wait", code=code, symptoms=SYMPTOMS, away_minutes=STEP_AWAY_MINUTES)


def can_step_away(conn, visit):
    """(allowed, reason). Urgent patients, and those the AI thinks may be an emergency, are asked to stay."""
    adaptive = dispatch.queue_policy(conn) == "mediqueue"
    if visit["esi"] <= 2 or (adaptive and (visit["ai_risk"] or 0) >= RISK_CAP_THRESHOLD):
        return False, "You may need a doctor soon, so please stay in the waiting area."
    if visit["away_count"] >= STEP_AWAY_LIMIT:
        return False, f"You have already stepped away {STEP_AWAY_LIMIT} times. Please stay close: your turn is coming."
    return True, None


def why_waiting(me, queue, state):
    """Plain reasons for a waiting patient's place in the queue. Only counts, never anyone else's details."""
    ahead = queue[:me["position"] - 1]
    reasons = []
    if me["away"]:
        reasons.append("You have stepped away, so doctors skip your token until you are back. You keep your place.")
    later_urgent = sum(1 for q in ahead if q["arrived_min"] > me["arrived_min"])
    if later_urgent:
        reasons.append(f"{later_urgent} {'person' if later_urgent == 1 else 'people'} who came in after you "
                       f"{'needs' if later_urgent == 1 else 'need'} a doctor more urgently.")
    more_urgent = sum(1 for q in ahead if q["effective"] < me["effective"] and q["arrived_min"] <= me["arrived_min"])
    if more_urgent:
        reasons.append(f"{more_urgent} more urgent {'patient' if more_urgent == 1 else 'patients'} arrived before you.")
    same = sum(1 for q in ahead if q["effective"] == me["effective"] and q["arrived_min"] <= me["arrived_min"])
    if same:
        reasons.append(f"{same} {'patient' if same == 1 else 'patients'} with the same priority arrived before you.")
    if any(q["paused"] for q in ahead):
        reasons.append("A doctor paused a consultation for a life-threatening case; that patient continues first.")
    if state["incoming"]:
        n = len(state["incoming"])
        reasons.append(f"{n} {'ambulance is' if n == 1 else 'ambulances are'} on the way, and a room may be kept free.")
    busy = sum(1 for d in state["doctors"] if d["current"])
    reasons.append(f"{state['stats']['on_duty']} {'doctor is' if state['stats']['on_duty'] == 1 else 'doctors are'} "
                   f"on duty, {busy} with a patient right now.")
    return reasons


@app.route("/api/wait/<code>")
def api_wait(code):
    conn = get_db()
    row = conn.execute("""SELECT v.*, u.room FROM visits v LEFT JOIN users u ON u.user_id = v.doctor_id
                           WHERE v.public_code = ?""", (code,)).fetchone()
    if row is None:
        return jsonify({"error": "unknown"}), 404
    out = {"token": row["token"], "status": row["status"], "arrived": row["arrived_at"][11:16]}
    if row["status"] == "waiting":
        state = build_state(conn)
        me = next(q for q in state["queue"] if q["id"] == row["visit_id"])
        out.update({"position": me["position"], "waiting": len(state["queue"]), "waited": me["waited"],
                    "eta_range": me["eta_range"], "reasons": why_waiting(me, state["queue"], state)})
        if me["aging_every"] and me["effective"] > me["aging_cap"]:
            steps = int(me["waited"] // me["aging_every"]) + 1
            out["next_step_in"] = max(1, round(steps * me["aging_every"] - me["waited"]))
            out["aging_every"] = me["aging_every"]
        if me["away"]:
            out["away"] = {"until": me["away_until"], "left": max(1, math.ceil(-minutes_between(row["away_until"])))}
        ok, why = can_step_away(conn, row)
        out["step_away"] = {"ok": ok, "why": why, "left": max(0, STEP_AWAY_LIMIT - row["away_count"])}
        report = conn.execute("SELECT * FROM patient_reports WHERE visit_id = ? ORDER BY report_id DESC LIMIT 1",
                              (row["visit_id"],)).fetchone()
        if report is not None:
            out["report"] = {"at": report["at"][11:16], "status": report["status"],
                             "handled_at": report["handled_at"][11:16] if report["handled_at"] else None}
    elif row["status"] == "in_consult":
        out["room"] = row["room"]
        out["called_at"] = row["consult_started_at"]  # a new call (e.g. after a pause) shows the alert again
    elif row["status"] == "left":
        out["left_by_you"] = conn.execute("SELECT 1 FROM events WHERE visit_id = ? AND kind = 'left' AND user_id IS NULL",
                                          (row["visit_id"],)).fetchone() is not None
    return jsonify(out)


@app.route("/api/wait/<code>/<action>", methods=["POST"])
def patient_action(code, action):
    """Buttons on the patient's own page. The secret code in the link is the only key."""
    conn = get_db()
    data = request.get_json(silent=True) or {}
    conn.execute("BEGIN IMMEDIATE")
    try:
        v = conn.execute("SELECT * FROM visits WHERE public_code = ?", (code,)).fetchone()
        if v is None:
            conn.execute("COMMIT")
            return jsonify({"error": "unknown"}), 404
        if v["status"] != "waiting":
            conn.execute("COMMIT")
            return jsonify({"message": "You are not in the waiting queue any more.", "category": "info"}), 409
        vid = v["visit_id"]
        if action == "worse":
            keys = [k for k in data.get("symptoms", []) if k in SYMPTOMS] or ["other"]
            open_report = conn.execute("SELECT * FROM patient_reports WHERE visit_id = ? AND status = 'open'",
                                       (vid,)).fetchone()
            if open_report:  # still waiting for the nurse: add to the same report
                keys = list(dict.fromkeys(open_report["symptoms"].split(",") + keys))
                conn.execute("UPDATE patient_reports SET symptoms = ? WHERE report_id = ?",
                             (",".join(keys), open_report["report_id"]))
            else:
                conn.execute("INSERT INTO patient_reports (visit_id, at, symptoms) VALUES (?, ?, ?)",
                             (vid, stamp(), ",".join(keys)))
            log_event(conn, "worse", "feels worse: " + ", ".join(SYMPTOMS[k].lower() for k in keys), vid)
            answer = ("The nurse has been told. Stay where you are: someone will come to check on you.", "success")
        elif action == "away":
            ok, why = can_step_away(conn, v)
            if not ok or dispatch.is_away(v):
                conn.execute("COMMIT")
                return jsonify({"message": why or "You have already stepped away.", "category": "error"}), 409
            minutes = int(data.get("minutes", 0) or 0)
            minutes = minutes if minutes in STEP_AWAY_MINUTES else STEP_AWAY_MINUTES[0]
            until = now() + timedelta(minutes=minutes)
            conn.execute("UPDATE visits SET away_until = ?, away_count = away_count + 1 WHERE visit_id = ?",
                         (stamp(until), vid))
            log_event(conn, "away", f"stepped away for {minutes} min, back by {stamp(until)[11:16]}", vid)
            answer = (f"You can step away until {stamp(until)[11:16]}. You keep your place in the queue.", "success")
        elif action == "back":
            conn.execute("UPDATE visits SET away_until = NULL WHERE visit_id = ?", (vid,))
            if dispatch.is_away(v):
                log_event(conn, "back", "back in the waiting area", vid)
            answer = ("Welcome back. A doctor can call your token again.", "success")
        elif action == "leave":
            conn.execute("UPDATE visits SET status = 'left', finished_at = ?, away_until = NULL WHERE visit_id = ?",
                         (stamp(), vid))
            conn.execute("UPDATE patient_reports SET status = 'closed' WHERE visit_id = ? AND status = 'open'", (vid,))
            log_event(conn, "left", "left without being seen (told us on their page)", vid)
            answer = ("You have left the queue. Thank you for letting us know.", "info")
        else:
            conn.execute("COMMIT")
            return jsonify({"error": "unknown action"}), 400
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return jsonify({"message": answer[0], "category": answer[1]})


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
    conn = get_db()
    return render_template("admin.html", page="admin", aging=setting(conn, "aging_minutes", AGING_MINUTES),
                           aging_mode=setting(conn, "aging_mode", "adaptive"), hold_rooms=setting(conn, "hold_rooms", 1),
                           metrics=_load_json("metrics.json"),
                           simulation=_load_json("simulation.json"), race=_load_json("race.json"))


@app.route("/api/admin/reports")
@login_required("admin")
def admin_reports():
    conn = get_db()
    by_esi = conn.execute("""SELECT esi, COUNT(*) AS n,
                                    AVG((julianday(first_seen_at) - julianday(arrived_at)) * 1440) AS wait
                             FROM visits WHERE first_seen_at IS NOT NULL GROUP BY esi ORDER BY esi""").fetchall()
    hourly = conn.execute("""SELECT strftime('%Y-%m-%d %H', arrived_at) AS slot, COUNT(*) AS n
                             FROM visits WHERE arrived_at >= ? GROUP BY slot""", (shift_start(),)).fetchall()
    # 12 one-hour buckets ending with the current hour, so the chart is never empty just after midnight
    counts = {r["slot"]: r["n"] for r in hourly}
    current = now().replace(minute=0, second=0, microsecond=0)
    hours = [current - timedelta(hours=k) for k in range(11, -1, -1)]
    hourly = [{"hour": h.hour, "n": counts.get(h.strftime("%Y-%m-%d %H"), 0)} for h in hours]
    agreement = conn.execute("SELECT ai_esi, esi, COUNT(*) AS n FROM visits GROUP BY ai_esi, esi").fetchall()
    return jsonify({
        "by_esi": [dict(r) for r in by_esi],
        "hourly": hourly,
        "agreement": [dict(r) for r in agreement],
        "simulation": _load_json("simulation.json"),
        "metrics": _load_json("metrics.json"),
        "race": _load_json("race.json"),
    })


@app.route("/admin/settings", methods=["POST"])
@login_required("admin")
def admin_settings():
    minutes = max(5, min(240, int(request.form["aging_minutes"])))
    mode = "fixed" if request.form.get("aging_mode") == "fixed" else "adaptive"
    hold = "1" if request.form.get("hold_rooms") else "0"
    conn = get_db()
    for key, value in (("aging_minutes", str(minutes)), ("aging_mode", mode), ("hold_rooms", hold)):
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
    flash(f"Saved: aging every {minutes} min ({'risk-adaptive' if mode == 'adaptive' else 'fixed'}), "
          f"rooms {'held' if hold == '1' else 'not held'} for ambulances.", "success")
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
    # HOST=0.0.0.0 lets phones on the same Wi-Fi open the app (and scan the token-slip QR) via the laptop's IP
    app.run(debug=False, threaded=True, host=os.environ.get("HOST", "127.0.0.1"), port=5000)
