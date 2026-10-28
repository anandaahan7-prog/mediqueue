"""
End-to-end checks of the main flows, run against a fresh throwaway database.

    python -m unittest tests.test_system -v
"""

import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

TMP = Path(tempfile.mkdtemp()) / "test.db"
os.environ["MEDIQUEUE_DB"] = str(TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as webapp  # noqa: E402
import dispatch  # noqa: E402
import seed  # noqa: E402
from db import connect  # noqa: E402

HEART_ATTACK = dict(age=58, heart_rate=124, systolic_bp=92, resp_rate=24, spo2=93, temperature=36.9,
                    consciousness=0, pain=8, complaint="chest")
SPRAIN = dict(age=24, heart_rate=80, systolic_bp=122, resp_rate=16, spo2=99, temperature=36.8,
              consciousness=0, pain=4, complaint="minor")
COLLAPSE = dict(age=66, heart_rate=140, systolic_bp=76, resp_rate=30, spo2=82, temperature=36.2,
                consciousness=2, pain=6, complaint="chest")


def client_as(username):
    c = webapp.app.test_client()
    c.post("/login", data={"username": username, "password": "demo123"})
    return c


def triage(c, name, vitals, **extra):
    form = {"name": name, "sex": "M", **vitals, **extra}
    return c.post("/triage", data=form, follow_redirects=True)


class MediQueueTests(unittest.TestCase):
    def setUp(self):
        seed.main()
        webapp._find_failures.clear()
        self.db = connect()

    def tearDown(self):
        self.db.close()

    # ---- login and roles ----
    def test_wrong_password(self):
        c = webapp.app.test_client()
        r = c.post("/login", data={"username": "priya", "password": "nope"}, follow_redirects=True)
        self.assertIn(b"Wrong username or password", r.data)
        with c.session_transaction() as s:
            self.assertNotIn("user_id", s)

    def test_nurse_cannot_open_admin(self):
        r = client_as("priya").get("/admin", follow_redirects=True)
        self.assertIn(b"have access", r.data)

    # ---- AI triage ----
    def test_preview_heart_attack_is_urgent(self):
        r = client_as("priya").post("/api/triage/preview", json=HEART_ATTACK).get_json()
        self.assertLessEqual(r["esi"], 2)

    def test_red_flag_forces_esi_1(self):
        r = client_as("priya").post("/api/triage/preview", json=COLLAPSE).get_json()
        self.assertEqual(r["esi"], 1)
        self.assertTrue(r["red_flags"])

    def test_override_needs_reason(self):
        c = client_as("priya")
        r = triage(c, "Test One", SPRAIN, final_esi=2)
        self.assertIn(b"short reason", r.data)
        r = triage(c, "Test One", SPRAIN, final_esi=3, override_reason="looks pale")
        row = self.db.execute("SELECT ai_esi, esi, override_reason FROM visits v JOIN patients p USING(patient_id) "
                              "WHERE p.name = 'Test One'").fetchone()
        self.assertEqual(row["esi"], 3)
        self.assertNotEqual(row["ai_esi"], 3)

    # ---- scheduling ----
    def test_call_next_takes_head_of_queue(self):
        head = webapp.build_state(self.db)["queue"][0]["id"]
        client_as("iyer").post("/doctor/call-next")
        got = self.db.execute("SELECT visit_id FROM visits WHERE doctor_id = 5 AND status = 'in_consult'").fetchone()
        self.assertEqual(got["visit_id"], head)

    def test_buttons_answer_background_requests(self):
        c = client_as("iyer")
        r = c.post("/doctor/call-next", headers={"X-Requested-With": "fetch"})
        self.assertEqual(r.status_code, 200)  # JSON, not a redirect and a page reload
        self.assertIn("Please call token", r.get_json()["message"])
        r = client_as("admin").post("/beds/6/clean", headers={"X-Requested-With": "fetch"})
        self.assertEqual(r.get_json()["category"], "success")
        self.assertEqual(c.post("/doctor/discharge").status_code, 302)  # a plain form post still redirects

    def test_cannot_call_twice(self):
        c = client_as("iyer")
        c.post("/doctor/call-next")
        r = c.post("/doctor/call-next", follow_redirects=True)
        self.assertIn(b"Finish or pause", r.data)

    def test_aged_patient_overtakes_newer_same_level(self):
        state = webapp.build_state(self.db)
        old_minor = next(q for q in state["queue"] if q["esi"] == 5 and q["waited"] > 70)
        self.assertEqual(old_minor["effective"], 3)
        # newer ESI 3 patients at the same level stay behind; high-risk ones may legitimately be ahead (level 2)
        newer_esi3 = [q for q in state["queue"] if q["esi"] == 3 and q["effective"] == 3 and q["waited"] < old_minor["waited"]]
        self.assertTrue(all(old_minor["position"] < q["position"] for q in newer_esi3))

    def test_two_doctors_never_get_same_patient(self):
        self.db.execute("UPDATE users SET on_duty = 1")
        for vid in (27, 28):  # free Dr. Rao and Dr. Mehta first
            self.db.execute("UPDATE visits SET status = 'discharged' WHERE visit_id = ?", (vid,))
        picked, barrier = [], threading.Barrier(4)

        def doctor(doc_id):
            conn = connect()
            barrier.wait()
            picked.append(dispatch.call_next(conn, doc_id))
            conn.close()

        threads = [threading.Thread(target=doctor, args=(d,)) for d in (3, 4, 5, 6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(picked), len(set(picked)))

    def test_esi1_preemption(self):
        self.db.execute("UPDATE users SET on_duty = 0 WHERE user_id = 5")  # Dr. Iyer off, everyone else busy
        triage(client_as("priya"), "Collapse Case", COLLAPSE)
        target = dispatch.who_should_preempt(self.db)
        self.assertIsNotNone(target)
        before = self.db.execute("SELECT visit_id FROM visits WHERE doctor_id = ? AND status = 'in_consult'",
                                 (target,)).fetchone()["visit_id"]
        dispatch.preempt_for_critical(self.db, target)
        paused = self.db.execute("SELECT status, paused_count, consult_done_min FROM visits WHERE visit_id = ?",
                                 (before,)).fetchone()
        self.assertEqual(paused["status"], "waiting")
        self.assertEqual(paused["paused_count"], 1)
        self.assertGreater(paused["consult_done_min"], 0)

    def test_retriage_raises_urgency(self):
        c = client_as("priya")
        triage(c, "Gets Worse", SPRAIN)
        vid = self.db.execute("SELECT visit_id, esi FROM visits ORDER BY visit_id DESC").fetchone()
        c.post(f"/visits/{vid['visit_id']}/retriage", data=HEART_ATTACK)
        new = self.db.execute("SELECT esi FROM visits WHERE visit_id = ?", (vid["visit_id"],)).fetchone()["esi"]
        self.assertLess(new, vid["esi"])

    # ---- risk-adaptive aging ----
    def test_risk_adaptive_aging_rules(self):
        from scheduler import effective_level
        self.assertEqual(effective_level(3, 15, 30, risk=0.35), 2)   # ages every 10 min and may reach level 2
        self.assertEqual(effective_level(3, 15, 30, risk=0.10), 3)   # low risk: never above level 3
        self.assertEqual(effective_level(5, 40, 30, risk=0.0), 4)    # no risk: plain 30-minute aging
        self.assertEqual(effective_level(3, 15, 30, risk=0.35, adaptive=False), 3)
        self.assertEqual(effective_level(2, 99, 30, risk=0.9), 2)    # ESI 1-2 never change
        borderline = next(q for q in webapp.build_state(self.db)["queue"] if q["high_risk"])
        self.assertEqual(borderline["effective"], 2)

    def test_admin_can_switch_to_fixed_aging(self):
        client_as("admin").post("/admin/settings", data={"aging_minutes": 30, "aging_mode": "fixed"})
        state = webapp.build_state(self.db)
        self.assertEqual(state["aging_mode"], "fixed")
        self.assertFalse(state["hold_rooms"])  # the checkbox was left out of the form
        self.assertTrue(all(q["effective"] >= 3 for q in state["queue"] if q["esi"] >= 3))

    # ---- ambulance pre-alerts (deadline-aware dispatch) ----
    def prealert(self, vitals, eta):
        return client_as("priya").post("/prealert", data={"sex": "M", "eta_minutes": eta, **vitals}, follow_redirects=True)

    def test_room_choice_and_backfill(self):
        from scheduler import pick_before, reserve_doctor
        self.assertEqual(reserve_doctor({3: 0, 4: 8, 5: 30}, 10), 4)  # free closest before the ETA: least idle time
        self.assertEqual(reserve_doctor({3: 12, 4: 30}, 10), 3)       # nobody in time: the soonest after
        self.assertEqual(reserve_doctor({3: 0, 4: 8}, 10, taken={4}), 3)
        waiting = [{"id": 1, "arrived": 0, "esi": 2, "predicted": 40}, {"id": 2, "arrived": 5, "esi": 4, "predicted": 8}]
        self.assertEqual(pick_before(waiting, 10, 25)["id"], 2)  # the ESI 2 would still be with the doctor at 25
        self.assertIsNone(pick_before(waiting, 10, 15))

    def test_prealert_holds_room_and_hands_over(self):
        self.prealert(HEART_ATTACK, eta=5)
        alert = self.db.execute("SELECT * FROM prealerts").fetchone()
        self.assertLessEqual(alert["esi"], 2)
        self.assertEqual(alert["doctor_id"], 5)  # Dr. Iyer is the only doctor free before it arrives
        r = client_as("iyer").post("/doctor/call-next", follow_redirects=True)
        self.assertIn(b"held for ambulance", r.data)  # nobody waiting would be finished within 5 minutes
        self.assertIsNone(self.db.execute("SELECT 1 FROM visits WHERE doctor_id = 5 AND status = 'in_consult'").fetchone())
        client_as("priya").post(f"/prealerts/{alert['alert_id']}/arrived")
        visit = self.db.execute("SELECT * FROM visits WHERE ambulance = 1").fetchone()
        self.assertEqual((visit["status"], visit["doctor_id"]), ("in_consult", 5))
        self.assertEqual(self.db.execute("SELECT status FROM prealerts").fetchone()["status"], "arrived")

    def test_low_urgency_ambulance_holds_no_room_and_cancel(self):
        self.prealert(SPRAIN, eta=10)
        alert = self.db.execute("SELECT * FROM prealerts").fetchone()
        self.assertIsNone(alert["doctor_id"])
        client_as("priya").post(f"/prealerts/{alert['alert_id']}/cancel")
        self.assertEqual(self.db.execute("SELECT status FROM prealerts").fetchone()["status"], "cancelled")

    # ---- the patient's own page ----
    def test_wait_page_explains_without_names(self):
        r = triage(client_as("priya"), "Slip Patient", SPRAIN)
        self.assertIn(b'class="qr"', r.data)  # QR code on the token slip
        code = self.db.execute("SELECT public_code FROM visits v JOIN patients p USING(patient_id) "
                               "WHERE p.name = 'Slip Patient'").fetchone()[0]
        anon = webapp.app.test_client()
        self.assertEqual(anon.get(f"/wait/{code}").status_code, 200)
        data = anon.get(f"/api/wait/{code}").get_json()
        self.assertEqual(data["status"], "waiting")
        self.assertGreaterEqual(data["position"], 1)
        self.assertTrue(data["reasons"])
        names = [row["name"] for row in self.db.execute("SELECT name FROM patients")]
        self.assertFalse(any(n in json.dumps(data) for n in names))
        self.assertEqual(anon.get("/api/wait/not-a-code").status_code, 404)

    # ---- the patient side: sign-in and actions ----
    def visit(self, visit_id):
        return self.db.execute("SELECT v.*, p.phone FROM visits v JOIN patients p USING(patient_id) WHERE visit_id = ?",
                               (visit_id,)).fetchone()

    def test_patient_finds_visit_with_token_and_phone(self):
        v = self.visit(35)
        anon = webapp.app.test_client()
        page = anon.get("/patient").get_data(as_text=True)
        self.assertIn("Demo patients", page)
        names = [row["name"] for row in self.db.execute("SELECT name FROM patients")]
        self.assertFalse(any(n in page for n in names))
        r = anon.post("/patient", data={"token": "t35", "phone": v["phone"][-4:]})  # "t35" means T035
        self.assertEqual(r.headers["Location"], f"/wait/{v['public_code']}")
        r = anon.post("/patient", data={"token": "T035", "phone": "0000" if v["phone"][-4:] != "0000" else "1111"})
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"could not find", r.data)
        for _ in range(webapp.FIND_LIMIT):
            anon.post("/patient", data={"token": "T035", "phone": "12"})
        r = anon.post("/patient", data={"token": "T035", "phone": v["phone"][-4:]})
        self.assertEqual(r.status_code, 429)  # too many wrong tries: even the right answer waits

    def test_stepped_away_patient_is_skipped_and_keeps_place(self):
        head = webapp.build_state(self.db)["queue"][0]
        self.db.execute("UPDATE visits SET away_until = datetime('now', '+5 hours', '+30 minutes', '+10 minutes') "
                        "WHERE visit_id = ?", (head["id"],))
        r = client_as("iyer").post("/doctor/call-next", headers={"X-Requested-With": "fetch"}).get_json()
        self.assertIn(f"Skipped {head['token']}", r["message"])
        self.assertEqual(self.visit(head["id"])["status"], "waiting")
        later = webapp.build_state(self.db)["queue"]
        self.assertEqual(later[0]["id"], head["id"])  # still first in line, just not dispatched
        self.assertTrue(later[0]["away"])

    def test_step_away_rules(self):
        queue = webapp.build_state(self.db)["queue"]
        urgent = next(q for q in queue if q["esi"] <= 2)
        calm = next(q for q in queue if q["esi"] >= 4)
        anon = webapp.app.test_client()
        r = anon.post(f"/api/wait/{self.visit(urgent['id'])['public_code']}/away", json={"minutes": 10})
        self.assertEqual(r.status_code, 409)  # ESI 1-2 are asked to stay
        code = self.visit(calm["id"])["public_code"]
        for _ in range(webapp.STEP_AWAY_LIMIT):
            self.assertEqual(anon.post(f"/api/wait/{code}/away", json={"minutes": 20}).status_code, 200)
            self.assertTrue(anon.get(f"/api/wait/{code}").get_json()["away"])
            anon.post(f"/api/wait/{code}/back")
            self.assertNotIn("away", anon.get(f"/api/wait/{code}").get_json())
        self.assertEqual(anon.post(f"/api/wait/{code}/away", json={"minutes": 10}).status_code, 409)

    def test_feel_worse_reaches_the_nurse(self):
        calm = next(q for q in webapp.build_state(self.db)["queue"] if q["esi"] >= 4)
        code = self.visit(calm["id"])["public_code"]
        anon = webapp.app.test_client()
        anon.post(f"/api/wait/{code}/worse", json={"symptoms": ["chest", "made-up"]})
        nurse = client_as("priya")
        help_ = nurse.get("/api/state").get_json()["help"]
        self.assertEqual([(h["visit_id"], h["red_flag"]) for h in help_], [(calm["id"], True)])
        nurse.post(f"/patient-reports/{help_[0]['id']}/checked")
        self.assertEqual(nurse.get("/api/state").get_json()["help"], [])
        self.assertEqual(anon.get(f"/api/wait/{code}").get_json()["report"]["status"], "checked")
        anon.post(f"/api/wait/{code}/worse", json={"symptoms": ["breathing"]})
        nurse.post(f"/visits/{calm['id']}/retriage", data=HEART_ATTACK)  # re-triage answers the report too
        self.assertEqual(anon.get(f"/api/wait/{code}").get_json()["report"]["status"], "retriaged")

    def test_called_alert_and_leaving(self):
        head = webapp.build_state(self.db)["queue"][0]
        code = self.visit(head["id"])["public_code"]
        anon = webapp.app.test_client()
        client_as("iyer").post("/doctor/call-next")
        w = anon.get(f"/api/wait/{code}").get_json()
        self.assertEqual((w["status"], w["room"]), ("in_consult", "Room 3"))
        self.assertTrue(w["called_at"])  # the page shows "Please go to Room 3" once per call
        other = webapp.build_state(self.db)["queue"][-1]
        code = self.visit(other["id"])["public_code"]
        self.assertEqual(anon.post(f"/api/wait/{code}/leave").status_code, 200)
        w = anon.get(f"/api/wait/{code}").get_json()
        self.assertEqual((w["status"], w["left_by_you"]), ("left", True))
        self.assertEqual(anon.post(f"/api/wait/{code}/away").status_code, 409)  # no longer in the queue
        state = client_as("priya").get("/api/state").get_json()
        self.assertEqual(state["stats"]["left_told"], 1)
        self.assertEqual(state["events"][0]["who"], "patient's page")

    # ---- beds (counting semaphore) ----
    def test_no_bed_means_boarding_then_signal(self):
        self.db.execute("UPDATE beds SET status = 'occupied', visit_id = NULL WHERE status = 'free'")
        c = client_as("rao")
        c.post("/doctor/admit")
        status = self.db.execute("SELECT status FROM visits WHERE visit_id = 27").fetchone()["status"]
        self.assertEqual(status, "boarding")
        client_as("admin").post("/beds/6/clean")  # bed 6 is being cleaned in the seed
        status = self.db.execute("SELECT status FROM visits WHERE visit_id = 27").fetchone()["status"]
        self.assertEqual(status, "admitted")

    def test_bed_never_doubled(self):
        rows = self.db.execute("SELECT visit_id, COUNT(*) n FROM beds WHERE visit_id IS NOT NULL GROUP BY visit_id "
                               "HAVING n > 1").fetchall()
        self.assertEqual(rows, [])

    # ---- privacy ----
    def test_board_has_no_names(self):
        data = webapp.app.test_client().get("/api/board").get_data(as_text=True)
        names = [r["name"] for r in self.db.execute("SELECT name FROM patients")]
        self.assertFalse(any(n in data for n in names))


if __name__ == "__main__":
    unittest.main(verbosity=2)
