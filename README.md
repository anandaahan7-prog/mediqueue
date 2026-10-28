# MediQueue – AI-assisted emergency department queue

**Live demo:** https://mediqueue-eight-tan.vercel.app &nbsp;·&nbsp; password for every account: `demo123`

Operating Systems course project.

| Team member | Register number |
|---|---|
| Aahan Anand | 24BCE2564 |
| Ayush Anupam Sahu | 24BAI0123 |
| Ankit Dhupal | 24BCB0142 |

A web app for a hospital casualty ward. Nurses triage patients with help from an AI model, doctors press
**“Call next patient”** and a priority scheduler decides who they see, and the waiting room gets a live display
with estimated waits.

The OS part is deliberately small and real:

| OS concept | Where it is used |
|---|---|
| Priority scheduling with aging (multiprocessor) | `scheduler.py`, `dispatch.py` – who the next free doctor sees |
| Preemption (ESI 1 only) | `dispatch.preempt_for_critical` |
| Dynamic priority (risk-adaptive aging) | `scheduler.effective_level` – the aging interval and cap depend on the AI's emergency risk |
| Real-time reservation, deadline-aware dispatch | `dispatch.hold_room`, `scheduler.reserve_doctor` (best fit), `scheduler.pick_before` (backfilling) |
| Critical section / mutex | `BEGIN IMMEDIATE … COMMIT` around dispatch and bed allocation |
| Counting semaphore | `beds.py` – observation beds, boarding queue = blocked queue |

AI part (`ai/`): random-forest triage suggestion with safety margin and red-flag rules, random-forest
consultation-time model, gradient-boosting waiting-time correction.

### What is new in this project

| Feature | Idea | Result in simulation (40 shifts) |
|---|---|---|
| **Risk-adaptive aging** | The AI's probability that an ESI 3–5 patient is really an emergency (their *emergency risk*) shortens their aging interval, and from 30% lets them age into level 2. Low-risk patients still never pass level 3. | Patients the AI under-triaged wait **60 → 50 min**; ESI 1–2 wait unchanged (**9.4 min**); ESI 4–5 95th percentile 206 → 216 min |
| **Ambulance pre-alert with a held room** | A crew calls ahead; the patient is triaged before arrival. For ESI 1–2 the room of the doctor free closest to (but before) the ETA is held (best-fit reservation), and that doctor's “Call next” only gives patients whose predicted consult ends before the ambulance does (deadline-aware dispatch, like backfilling). On arrival the patient goes straight in. | Ambulance door-to-doctor **8.7 → 4.6 min**, on time 73% → 85%; everyone else waits 43.9 → 49.0 min (the price of a reservation) |
| **“Why am I waiting?” page** | Each token slip has a QR code. It opens the patient's own page: their place in the queue, the expected range, when their priority rises, and plain reasons (who came later but is more urgent, ambulances on the way, doctors on duty). Counts only, never anyone else's details. | Tested: no other patient's name ever appears |
| **The patient's phone** | No passwords: the QR link, or the token plus the last 4 digits of the phone (8 wrong tries per 10 min). **I feel worse** puts an alert on the triage desk, red flags first, until the nurse re-triages or marks it checked. **Step away** (10 or 20 min, twice per visit, not for ESI 1–2 or high-risk patients) makes the patient a *blocked process*: still in the queue with their waiting time, but “Call next” skips them until “I'm back” or the timer ends. When called, the phone shows a full-screen “Please go to Room 3” with a beep and a buzz. **I'm leaving** closes the visit and counts in the reports. | Tested: skipped while away and first again after, step-away rules, report → checked / re-triaged, called alert, leaving, sign-in limits |

Both scheduling features can be switched off in **Reports & settings**, so the trade-offs can be shown live.

## Run it

```bash
pip install -r requirements.txt
python seed.py          # creates mediqueue.db with demo staff, beds and patients
python app.py           # http://localhost:5000
```

To scan the token-slip QR with a phone, start the app so other devices on the same Wi-Fi can reach it and open it
through the laptop's IP address (e.g. `http://192.168.1.20:5000`); the QR then points at that address too:

```bash
HOST=0.0.0.0 python app.py
```

Password for every account is `demo123`:

| user | role |
|---|---|
| `priya`, `arun` | nurse (triage desk) |
| `rao`, `mehta`, `iyer` (on duty), `khan` (off duty) | doctor |
| `admin` | reports & settings |

The waiting-room display is at http://localhost:5000/board (no login). Patients use http://localhost:5000/patient
(token + last 4 digits of their phone); it lists three demo patients from the queue to pick from.

The trained models are already in `models/`. To rebuild everything from scratch:

```bash
python -m ai.train      # triage + consult-time models, writes models/metrics.json
python simulate.py      # policy comparison, aging + risk sweeps, pre-alert experiment, wait-time model
python race_test.py     # concurrency stress test
python -m unittest tests.test_system -v
```

## Deployment (Vercel)

The app is deployed on Vercel as a Python (Flask) serverless function, using `requirements.txt`, `.python-version`
and `.vercelignore` in this repo. Vercel only allows writing to `/tmp`, so each server instance creates and seeds its
own demo database there on start-up (see `ensure_database()` in `app.py` and `DB_PATH` in `db.py`).

Limitation: if Vercel starts more than one instance (for example under several simultaneous requests), the instances
do not share data, so the live link is meant for a single-user walkthrough. For the full multi-screen demo
(nurse, doctor and waiting room at the same time) run it locally with `python app.py`, or point `DB_PATH` at a shared
database. Every response carries an `X-MediQueue-Instance` header showing which instance answered.

```bash
npx vercel deploy --prod
```

## Suggested demo (about 14 minutes)

1. **Triage desk** (log in as `priya`): the door plates at the top show each room as Busy / Free / Off. Click
   *Fill an example patient*, show the AI level plate, the reasons and the red "out of range" vitals. Pick a
   different level to show the signed delta ("AI suggested ESI 2 · you chose ESI 1 · up 1 level") and the required reason.
   Click *Issue token and add to queue*: the token slip with its QR code appears; open *the patient's view* in a new tab.
2. **Queue** below the form: each bar is time waited drawn to scale against the ESI target tick. Point out the
   ESI 5 patient tagged *now 3* (aging), T039 and T035 tagged *risk 31%* / *risk 57%* that have aged into level 2
   (risk-adaptive aging), and the *AI 4 · up 1* override tag.
3. **Doctor console** (log in as `iyer` in a second browser or incognito window): *Call next patient*. Watch the
   nurse's queue rows slide up, Room 3's slider glide to Busy, and the new token glow on the waiting-room display.
4. **Preemption**: as the nurse, triage a patient with SpO₂ 82 and “responds to pain” → red flag, ESI 1.
   The doctor treating the least urgent patient gets the red *Pause and see T0xx* alarm, which drops in and pulses twice.
5. **Beds**: click *Admit to observation* on the doctor console, then on the bed board click *Release bed* and then *Mark clean* –
   the semaphore value and boarding queue update.
6. **Ambulance pre-alert**: as the nurse open *Ambulances*, enter age 63, HR 118, BP 96, RR 26, SpO₂ 91, temp 37.0,
   pain 8, chest pain, arriving in 6 min → ESI 2 and a held room. The held doctor's “Call next” is refused because
   nobody waiting would be finished in time; the waiting-room display shows the ambulance band. Press *Arrived*:
   the patient goes straight into the held room.
7. **Waiting-room display** (`/board`) and **the patient's phone**: open `/patient` on a phone and pick the demo
   patient marked *may step away*. Press *I feel worse* (chest pain): the triage desk shows the red *Patients asking
   for help* panel; press *Checked, no change* and the phone says a nurse checked. *Step away* for 10 minutes: the
   queue tags them *away till …* and the TV tile turns dashed. Then sign in as *Next in line*, and on the doctor console
   press *Call next patient*: the phone turns into a blue “Please go to Room 3” sign.
8. **Reports** (log in as `admin`): scheduler comparison (fixed vs risk-adaptive aging), the new section with the
   risk-threshold sweep and the pre-alert trade-off, model accuracy, and *Run test again* for the concurrency test
   (no lock → double assignments, with lock → 0).

The interface design ("Ward Corridor": green dado rail, cobalt enamel signs, door plates with an IN/OUT slider)
is documented in `DESIGN.md`; motion only marks state changes and respects the reduce-motion setting.

## Project layout

```
app.py            Flask routes and the live state API
scheduler.py      pure scheduling logic (shared with the simulator)
dispatch.py       "call next" + ESI 1 preemption + held rooms for ambulances, inside a critical section
beds.py           observation beds as a counting semaphore
race_test.py      concurrency stress test
simulate.py       40 simulated shifts: FCFS vs SJF vs priority vs MediQueue (fixed / risk-adaptive), pre-alerts
seed.py           demo data, timestamps relative to "now"
schema.sql        tables, constraints, indexes, queue view
ai/               dataset generator, training, runtime models, wait-time model
templates/ public/static/  the user interface (public/ is served by the Vercel CDN)
tests/            26 end-to-end tests
```
