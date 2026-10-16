# MediQueue – AI-assisted emergency department queue

A web app for a hospital casualty ward. Nurses triage patients with help from an AI model, doctors press
**“Call next patient”** and a priority scheduler decides who they see, and the waiting room gets a live display
with estimated waits.

The OS part is deliberately small and real:

| OS concept | Where it is used |
|---|---|
| Priority scheduling with aging (multiprocessor) | `scheduler.py`, `dispatch.py` – who the next free doctor sees |
| Preemption (ESI 1 only) | `dispatch.preempt_for_critical` |
| Critical section / mutex | `BEGIN IMMEDIATE … COMMIT` around dispatch and bed allocation |
| Counting semaphore | `beds.py` – observation beds, boarding queue = blocked queue |

AI part (`ai/`): random-forest triage suggestion with safety margin and red-flag rules, random-forest
consultation-time model, gradient-boosting waiting-time correction.

## Run it

```bash
pip install -r requirements.txt
python seed.py          # creates mediqueue.db with demo staff, beds and patients
python app.py           # http://localhost:5000
```

Password for every account is `demo123`:

| user | role |
|---|---|
| `priya`, `arun` | nurse (triage desk) |
| `rao`, `mehta`, `iyer` (on duty), `khan` (off duty) | doctor |
| `admin` | reports & settings |

The waiting-room display is at http://localhost:5000/board (no login).

The trained models are already in `models/`. To rebuild everything from scratch:

```bash
python -m ai.train      # triage + consult-time models, writes models/metrics.json
python simulate.py      # policy comparison, aging sweep, wait-time model (~30 s)
python race_test.py     # concurrency stress test
python -m unittest tests.test_system -v
```

## Suggested demo (8–10 minutes)

1. **Triage desk** (log in as `priya`): click *Fill example*, show the AI suggestion, the reasons and the
   red outlines on abnormal vitals. Change the level to show that an override needs a reason. Issue the token.
2. **Queue** below the form: point out the ESI 5 patient who has *aged to 3* and the re-check reminders.
3. **Doctor console** (log in as `iyer` in a second browser or incognito window): *Call next patient*.
4. **Preemption**: as the nurse, triage a patient with SpO₂ 82 and “responds to pain” → red flag, ESI 1.
   The doctor treating the least urgent patient gets the red *Pause & attend* banner.
5. **Beds**: admit a patient from the doctor console, then on the bed board release a bed and *Mark clean* –
   the semaphore value and boarding queue update.
6. **Waiting-room display** (`/board`): tokens and estimated ranges, no names.
7. **Reports** (log in as `admin`): scheduler comparison, aging sweep, model accuracy, and *Run test again*
   for the concurrency test (no lock → double assignments, with lock → 0).

## Project layout

```
app.py            Flask routes and the live state API
scheduler.py      pure scheduling logic (shared with the simulator)
dispatch.py       "call next" + ESI 1 preemption, inside a critical section
beds.py           observation beds as a counting semaphore
race_test.py      concurrency stress test
simulate.py       40 simulated shifts: FCFS vs SJF vs priority vs MediQueue
seed.py           demo data, timestamps relative to "now"
schema.sql        tables, constraints, indexes, queue view
ai/               dataset generator, training, runtime models, wait-time model
templates/ static/  the user interface
tests/            14 end-to-end tests
```
