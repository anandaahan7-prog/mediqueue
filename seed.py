"""
Creates mediqueue.db with demo data, with timestamps relative to "now" so the queue
always looks like a busy afternoon whenever you run it.

    python seed.py
"""

import random
import secrets
from datetime import timedelta

import numpy as np
from werkzeug.security import generate_password_hash

from ai import triage_model
from ai.dataset import sample_patient
from db import DB_PATH, init_db, now, stamp
from scheduler import risk_of

PASSWORD = "demo123"

STAFF = [
    ("Priya Nair", "priya", "nurse", None, 0),
    ("Arun Joseph", "arun", "nurse", None, 0),
    ("Dr. Kavita Rao", "rao", "doctor", "Room 1", 1),
    ("Dr. Sameer Mehta", "mehta", "doctor", "Room 2", 1),
    ("Dr. Anand Iyer", "iyer", "doctor", "Room 3", 1),
    ("Dr. Farah Khan", "khan", "doctor", "Room 4", 0),
    ("ED Admin", "admin", "admin", None, 0),
]

NAMES = ["Aarav Shah", "Diya Kulkarni", "Kabir Singh", "Ananya Rao", "Rohan Das", "Meera Pillai", "Vihaan Gupta",
         "Isha Menon", "Arjun Reddy", "Sana Qureshi", "Kunal Patil", "Neha Joshi", "Aditya Verma", "Tara Fernandes",
         "Farhan Ali", "Pooja Hegde", "Siddharth Jain", "Nisha Bose", "Yash Malhotra", "Aisha Khan", "Dev Chauhan",
         "Kavya Iyer", "Imran Sheikh", "Riya Desai", "Manav Sethi", "Zoya Mirza", "Harsh Agarwal", "Lakshmi Nair",
         "Varun Kapoor", "Fatima Sayyed", "Nikhil Saxena", "Sneha Kamath", "Om Prakash", "Jaya Bhat", "Tanvi Shetty",
         "Rehan Ansari", "Gauri Kulkarni", "Ayaan Thomas", "Rahul Mishra", "Bhavna Rawat"]


# made-up mobile numbers, so patients can find their visit with token + last 4 digits.
# A separate generator, so the vitals drawn from `rng` stay exactly the same.
PHONES = random.Random(41)


def insert_visit(conn, rng, name, arrived, status, vitals=None, complaint=None, final_esi=None, override=None,
                 doctor=None, started=None, finished=None, seen=None, nurse=1):
    v = vitals or sample_patient(rng, complaint)
    ai = triage_model.suggest(v, with_reasons=False)  # explanations are only shown live, never stored
    esi = final_esi or ai["esi"]
    minutes = ai["minutes"] if esi == ai["esi"] else triage_model.predict_minutes(v, esi)
    sex = "F" if name.split()[0] in {"Diya", "Ananya", "Meera", "Isha", "Sana", "Neha", "Tara", "Pooja", "Nisha",
                                     "Aisha", "Kavya", "Riya", "Zoya", "Lakshmi", "Fatima", "Sneha", "Jaya",
                                     "Tanvi", "Gauri", "Bhavna"} else "M"
    phone = f"9{PHONES.randrange(10**8, 10**9)}"
    pid = conn.execute("INSERT INTO patients (name, age, sex, phone, created_at) VALUES (?, ?, ?, ?, ?)",
                       (name, v["age"], sex, phone, stamp(arrived))).lastrowid
    count = conn.execute("SELECT COUNT(*) FROM visits").fetchone()[0]
    vid = conn.execute(
        """INSERT INTO visits (patient_id, token, arrived_at, complaint, heart_rate, systolic_bp, resp_rate, spo2,
                               temperature, consciousness, pain, ai_esi, ai_confidence, esi, override_reason,
                               ai_risk, predicted_minutes, public_code, status, doctor_id, first_seen_at,
                               consult_started_at, consult_done_min, finished_at, triaged_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (pid, f"T{count + 1:03d}", stamp(arrived), v["complaint"], v["heart_rate"], v["systolic_bp"], v["resp_rate"],
         v["spo2"], v["temperature"], v["consciousness"], v["pain"], ai["esi"], ai["confidence"], esi, override,
         risk_of(ai["probs"], esi), minutes, secrets.token_urlsafe(6), status, doctor, stamp(seen) if seen else None,
         stamp(started) if started else None,
         (finished - seen).total_seconds() / 60 if finished and seen else 0, stamp(finished) if finished else None,
         nurse)).lastrowid
    conn.execute("INSERT INTO events (visit_id, user_id, at, kind, detail) VALUES (?, ?, ?, 'triaged', ?)",
                 (vid, nurse, stamp(arrived), f"ESI {esi} (AI suggested {ai['esi']})"))
    return vid, minutes


def main():
    PHONES.seed(41)
    conn = init_db()  # schema.sql drops and recreates every table, so this also works while the app is running
    rng = np.random.default_rng(2026)
    t0 = now()

    conn.execute("INSERT INTO settings VALUES ('aging_minutes', '30')")
    conn.execute("INSERT INTO settings VALUES ('aging_mode', 'adaptive')")
    conn.execute("INSERT INTO settings VALUES ('hold_rooms', '1')")
    for name, username, role, room, duty in STAFF:
        conn.execute("INSERT INTO users (name, username, password_hash, role, room, on_duty) VALUES (?, ?, ?, ?, ?, ?)",
                     (name, username, generate_password_hash(PASSWORD), role, room, duty))
    for i in range(1, 9):
        conn.execute("INSERT INTO beds (label, ward) VALUES (?, 'Observation')", (f"OBS-{i}",))

    names = iter(NAMES)
    doctors = [3, 4, 5]

    # ---- earlier today: 26 finished visits spread over the last 6 hours ----
    admitted = []
    for k in range(26):
        arrived = t0 - timedelta(minutes=360 - k * 11 + rng.uniform(0, 6))
        seen = arrived + timedelta(minutes=float(rng.uniform(1, 40)))
        finished = seen + timedelta(minutes=float(rng.uniform(8, 35)))
        status = "admitted" if k % 5 == 0 else "discharged"
        vid, _ = insert_visit(conn, rng, next(names), arrived, status, doctor=doctors[k % 3],
                              seen=seen, started=seen, finished=finished)
        if status == "admitted":
            admitted.append(vid)

    # beds: the admitted patients hold beds, one bed is being cleaned
    for bed_id, vid in enumerate(admitted[:5], start=1):
        conn.execute("UPDATE beds SET status = 'occupied', visit_id = ?, updated_at = ? WHERE bed_id = ?",
                     (vid, stamp(), bed_id))
    conn.execute("UPDATE beds SET status = 'cleaning', updated_at = ? WHERE bed_id = 6", (stamp(),))

    # ---- right now: two doctors busy, Dr. Iyer free ----
    for doc, (mins_ago, started_ago, complaint) in zip([3, 4], [(42, 12, "abdominal"), (25, 6, "trauma")]):
        arrived = t0 - timedelta(minutes=mins_ago)
        started = t0 - timedelta(minutes=started_ago)
        insert_visit(conn, rng, next(names), arrived, "in_consult", complaint=complaint, doctor=doc,
                     seen=started, started=started)

    # ---- the waiting room ----
    waiting = [(78, "minor", None), (64, "fever", None), (51, "abdominal", None), (38, "minor", None),
               (30, "breathing", None), (22, "fever", None), (15, "trauma", None), (9, "chest", None),
               (4, "minor", None)]
    for mins_ago, complaint, _ in waiting:
        insert_visit(conn, rng, next(names), t0 - timedelta(minutes=mins_ago), "waiting", complaint=complaint)

    # one visit where the nurse disagreed with the AI
    vitals = {"age": 61, "heart_rate": 96, "systolic_bp": 138, "resp_rate": 18, "spo2": 96, "temperature": 36.9,
              "consciousness": 0, "pain": 5, "complaint": "abdominal"}
    insert_visit(conn, rng, next(names), t0 - timedelta(minutes=12), "waiting", vitals=vitals, final_esi=3,
                 override="Known diabetic, vomiting since morning")

    # a borderline ESI 3: the model gives ~31% that this is really an emergency, so risk-adaptive
    # aging moves them up every ~11 minutes and lets them reach level 2
    vitals = {"age": 47, "heart_rate": 104, "systolic_bp": 123, "resp_rate": 30, "spo2": 98, "temperature": 37.2,
              "consciousness": 0, "pain": 5, "complaint": "breathing"}
    insert_visit(conn, rng, next(names), t0 - timedelta(minutes=18), "waiting", vitals=vitals)

    conn.close()
    print(f"Created {DB_PATH.name}: 7 staff, 8 beds, {26 + 2 + len(waiting) + 2} visits. Password for everyone: {PASSWORD}")


if __name__ == "__main__":
    main()
