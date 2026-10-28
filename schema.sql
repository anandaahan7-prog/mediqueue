-- MediQueue database schema (SQLite)
PRAGMA foreign_keys = ON;

DROP VIEW IF EXISTS v_queue;
DROP VIEW IF EXISTS v_today_stats;
DROP TABLE IF EXISTS events;
DROP TABLE IF EXISTS patient_reports;
DROP TABLE IF EXISTS prealerts;
DROP TABLE IF EXISTS beds;
DROP TABLE IF EXISTS visits;
DROP TABLE IF EXISTS patients;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS settings;

CREATE TABLE users (
    user_id       INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('nurse', 'doctor', 'admin')),
    room          TEXT,                       -- consultation room for doctors
    on_duty       INTEGER NOT NULL DEFAULT 0 CHECK (on_duty IN (0, 1))
);

CREATE TABLE patients (
    patient_id INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    age        INTEGER NOT NULL CHECK (age BETWEEN 0 AND 120),
    sex        TEXT NOT NULL CHECK (sex IN ('M', 'F', 'O')),
    phone      TEXT,
    created_at TEXT NOT NULL
);

-- one row per emergency visit; this is the "process control block" of the scheduler
CREATE TABLE visits (
    visit_id           INTEGER PRIMARY KEY,
    patient_id         INTEGER NOT NULL REFERENCES patients(patient_id),
    token              TEXT NOT NULL UNIQUE,
    arrived_at         TEXT NOT NULL,
    complaint          TEXT NOT NULL,
    notes              TEXT,
    heart_rate         INTEGER NOT NULL,
    systolic_bp        INTEGER NOT NULL,
    resp_rate          INTEGER NOT NULL,
    spo2               INTEGER NOT NULL CHECK (spo2 BETWEEN 50 AND 100),
    temperature        REAL NOT NULL,
    consciousness      INTEGER NOT NULL CHECK (consciousness BETWEEN 0 AND 3),
    pain               INTEGER NOT NULL CHECK (pain BETWEEN 0 AND 10),
    ai_esi             INTEGER NOT NULL CHECK (ai_esi BETWEEN 1 AND 5),
    ai_confidence      REAL,
    esi                INTEGER NOT NULL CHECK (esi BETWEEN 1 AND 5),   -- final level, nurse can override
    override_reason    TEXT,
    ai_risk            REAL NOT NULL DEFAULT 0,  -- AI probability an ESI 3-5 patient is really ESI 1-2 (drives aging)
    predicted_minutes  INTEGER NOT NULL,
    public_code        TEXT UNIQUE,              -- secret part of the patient's "why am I waiting?" link
    ambulance          INTEGER NOT NULL DEFAULT 0 CHECK (ambulance IN (0, 1)),
    reserved_for       INTEGER REFERENCES users(user_id),  -- doctor who held a room for this ambulance
    status             TEXT NOT NULL DEFAULT 'waiting'
                       CHECK (status IN ('waiting', 'in_consult', 'boarding', 'admitted', 'discharged', 'left')),
    doctor_id          INTEGER REFERENCES users(user_id),
    first_seen_at      TEXT,
    consult_started_at TEXT,
    consult_done_min   REAL NOT NULL DEFAULT 0,  -- minutes already spent with a doctor (kept across a pause)
    paused_count       INTEGER NOT NULL DEFAULT 0,
    away_until         TEXT,                     -- patient stepped away: skipped by "Call next" until then
    away_count         INTEGER NOT NULL DEFAULT 0,
    finished_at        TEXT,
    triaged_by         INTEGER REFERENCES users(user_id)
);

CREATE TABLE beds (
    bed_id     INTEGER PRIMARY KEY,
    label      TEXT NOT NULL UNIQUE,
    ward       TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'free' CHECK (status IN ('free', 'occupied', 'cleaning')),
    visit_id   INTEGER UNIQUE REFERENCES visits(visit_id),   -- UNIQUE: one patient can never hold two beds
    updated_at TEXT
);

-- ambulance pre-alerts: triaged before arrival, a doctor's room is held until the ETA
CREATE TABLE prealerts (
    alert_id          INTEGER PRIMARY KEY,
    code              TEXT NOT NULL UNIQUE,      -- A01, A02, ...
    created_at        TEXT NOT NULL,
    eta_at            TEXT NOT NULL,
    patient_name      TEXT,
    age               INTEGER NOT NULL CHECK (age BETWEEN 0 AND 120),
    sex               TEXT NOT NULL CHECK (sex IN ('M', 'F', 'O')),
    complaint         TEXT NOT NULL,
    notes             TEXT,
    heart_rate        INTEGER NOT NULL,
    systolic_bp       INTEGER NOT NULL,
    resp_rate         INTEGER NOT NULL,
    spo2              INTEGER NOT NULL,
    temperature       REAL NOT NULL,
    consciousness     INTEGER NOT NULL,
    pain              INTEGER NOT NULL,
    ai_esi            INTEGER NOT NULL,
    ai_confidence     REAL,
    esi               INTEGER NOT NULL,
    override_reason   TEXT,
    ai_risk           REAL NOT NULL DEFAULT 0,
    predicted_minutes INTEGER NOT NULL,
    doctor_id         INTEGER REFERENCES users(user_id),  -- whose room is held (ESI 1-2 only)
    status            TEXT NOT NULL DEFAULT 'incoming' CHECK (status IN ('incoming', 'arrived', 'cancelled')),
    visit_id          INTEGER REFERENCES visits(visit_id),
    created_by        INTEGER REFERENCES users(user_id)
);

-- "I feel worse" reports sent from the patient's own page; the nurse re-triages or marks them checked
CREATE TABLE patient_reports (
    report_id  INTEGER PRIMARY KEY,
    visit_id   INTEGER NOT NULL REFERENCES visits(visit_id),
    at         TEXT NOT NULL,
    symptoms   TEXT NOT NULL,                    -- comma-separated keys, see SYMPTOMS in app.py
    status     TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'checked', 'retriaged', 'closed')),
    handled_by INTEGER REFERENCES users(user_id),
    handled_at TEXT
);

-- audit trail of every scheduling / bed decision
CREATE TABLE events (
    event_id INTEGER PRIMARY KEY,
    visit_id INTEGER REFERENCES visits(visit_id),
    user_id  INTEGER REFERENCES users(user_id),
    at       TEXT NOT NULL,
    kind     TEXT NOT NULL,
    detail   TEXT
);

CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX idx_visits_status ON visits(status, esi, arrived_at);
CREATE INDEX idx_visits_doctor ON visits(doctor_id, status);
CREATE INDEX idx_events_visit ON events(visit_id, at);
CREATE INDEX idx_reports_visit ON patient_reports(visit_id, status);

CREATE VIEW v_queue AS
SELECT v.*, p.name AS patient_name, p.age, p.sex
FROM visits v JOIN patients p ON p.patient_id = v.patient_id
WHERE v.status IN ('waiting', 'in_consult', 'boarding');
