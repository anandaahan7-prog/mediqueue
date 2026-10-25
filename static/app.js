/* MediQueue front end. Plain JavaScript: every screen polls the server and re-draws itself. */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function escapeHtml(value) {
  return String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function fmt(n, digits = 1) {
  return Number.isFinite(n) ? Number(n.toFixed(digits)).toString() : '–';
}
const pct = x => `${Math.round(x * 100)}%`;
const ESI_NAMES = { 1: 'Resuscitation', 2: 'Emergent', 3: 'Urgent', 4: 'Less urgent', 5: 'Non-urgent' };
const ESI_TARGET = { 1: 0, 2: 10, 3: 30, 4: 60, 5: 120 };
const esiBadge = (l, name = false) => `<span class="esi-badge esi-${l}">ESI ${l}${name ? ' · ' + ESI_NAMES[l] : ''}</span>`;

// small inline icon set (one stroke style everywhere)
const ICONS = {
  up: '<path d="M12 19V5M5 12l7-7 7 7"/>',
  alert: '<path d="M12 3 2 20h20z"/><path d="M12 10v4M12 17h.01"/>',
  pause: '<path d="M9 5v14M15 5v14"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 8h.01M11 12h1v5h1"/>',
};
const icon = (name, cls = 'ico') => `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name]}</svg>`;
const agedTag = (q, short = false) => q.effective < q.esi
  ? `<span class="aged" title="Waited long enough to move up from ESI ${q.esi}">${icon('up')}${short ? q.effective : `aged to ${q.effective}`}</span>` : '';

async function getJSON(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) throw new Error(res.status);
  return res.json();
}

// Only touch the DOM when the markup really changed, so a keyboard user's focus
// is not thrown away by a refresh that changed nothing.
function setHTML(el, html) {
  if (el && el.__html !== html) {
    el.innerHTML = html;
    el.__html = html;
  }
}

// connection state shown in the clock pill
let online = true;
function setOnline(ok) {
  if (ok === online) return;
  online = ok;
  const pill = $('#live-pill');
  if (!pill) return;
  pill.classList.toggle('offline', !ok);
  $('#live-status').textContent = ok ? 'Connection restored' : 'Connection lost, retrying';
  if (!ok) $('#live-clock').textContent = 'Reconnecting…';
  else tickClock();
}

function poll(fn, every = 5000) {
  const run = () => fn().then(() => setOnline(true)).catch(err => { console.warn('refresh failed', err); setOnline(false); });
  run();
  return setInterval(run, every);
}

function postButton(action, label, cls = 'btn btn-sm', { confirm = '', aria = '' } = {}) {
  const onsubmit = confirm ? ` onsubmit="return confirm('${confirm.replace(/'/g, '&#39;')}')"` : '';
  const ariaAttr = aria ? ` aria-label="${escapeHtml(aria)}"` : '';
  return `<form method="post" action="${action}" class="inline-form"${onsubmit}><button type="submit" class="${cls}"${ariaAttr}>${label}</button></form>`;
}

function tickClock() {
  const el = $('#live-clock') || $('#board-clock');
  if (el && online) el.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

// ---------------------------------------------------------------- shell
(function shell() {
  tickClock();
  setInterval(tickClock, 15000);

  $('#theme-btn')?.addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('mq-theme', next); } catch (e) { /* private mode */ }
  });

  const menu = $('#menu-btn');
  const setMenu = open => {
    $('#sidebar')?.classList.toggle('open', open);
    $('#sidebar-backdrop')?.classList.toggle('show', open);
    menu?.setAttribute('aria-expanded', String(open));
    if (open) $('#sidebar a')?.focus();
  };
  menu?.addEventListener('click', () => setMenu(true));
  $('#sidebar-backdrop')?.addEventListener('click', () => setMenu(false));
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && $('#sidebar')?.classList.contains('open')) { setMenu(false); menu?.focus(); }
  });

  // flash messages fade out on their own
  $$('.flash').forEach(el => setTimeout(() => {
    el.classList.add('fade');
    setTimeout(() => el.remove(), 450);
  }, 6000));

  // every POST button shows a loading state and can't be pressed twice
  document.addEventListener('submit', e => {
    const form = e.target;
    if (e.defaultPrevented || (form.method || '').toLowerCase() !== 'post') return;
    const btn = e.submitter || $('button[type="submit"], button:not([type])', form);
    if (!btn) return;
    setTimeout(() => {
      btn.disabled = true;
      btn.setAttribute('aria-busy', 'true');
      if (form.dataset.loadingText) btn.textContent = form.dataset.loadingText;
      else btn.classList.add('is-loading');
    }, 0);
  });
})();

// ---------------------------------------------------------------- login
function initLogin() {
  $$('[data-demo]').forEach(chip => chip.addEventListener('click', () => {
    $('#username').value = chip.dataset.demo;
    $('#password').value = 'demo123';
    $('.login-form button[type="submit"]').focus();
  }));
}

// ---------------------------------------------------------------- triage desk
function initTriage() {
  const form = $('#triage-form');
  const isRetriage = form.action.includes('/retriage');
  let latest = null;
  let timer = null;

  const values = () => {
    const data = {};
    ['age', 'heart_rate', 'systolic_bp', 'resp_rate', 'spo2', 'temperature', 'consciousness', 'pain', 'complaint']
      .forEach(k => { data[k] = form.elements[k].value; });
    return data;
  };

  const markRanges = () => {
    $$('#vitals [data-range]').forEach(field => {
      const [lo, hi] = field.dataset.range.split(',').map(Number);
      const input = $('input, select', field);
      const v = Number(input.value);
      const bad = input.value !== '' && (v < lo || v > hi);
      field.classList.toggle('abnormal', bad);
      input.setAttribute('aria-invalid', String(bad));
    });
    $('#pain-val').textContent = `${form.elements.pain.value}/10`;
  };

  const syncOverride = () => {
    const chosen = Number(form.elements.final_esi.value || 0);
    const changed = latest && chosen && chosen !== latest.esi;
    $('#override-field').classList.toggle('hidden', !changed || isRetriage);
    const reason = form.elements.override_reason;
    if (reason) reason.required = Boolean(changed && !isRetriage);
  };

  const render = s => {
    latest = s;
    const flags = s.red_flags.length
      ? `<div class="callout danger" style="margin-bottom:12px">${icon('alert')}<div><strong>Red flag: ESI 1 whatever the model says</strong><p>${s.red_flags.map(escapeHtml).join(' · ')}</p></div></div>` : '';
    const bumped = s.safety_bumped
      ? `<p class="hint" style="margin-top:6px">The model's first choice was ESI ${s.model_esi}. It gave ESI ${s.esi} at least 30%, so the safety margin moved it one level up.</p>` : '';
    const reasons = s.reasons.length ? s.reasons.map(r => `
        <div class="reason ${r.effect > 0 ? 'up' : 'down'}"><span>${escapeHtml(r.feature)}: <b>${escapeHtml(r.value)}</b></span>
          <span>${r.effect > 0 ? 'raises' : 'lowers'} urgency</span></div>`).join('')
      : '<p class="hint">The vitals are close to normal, so nothing in particular pushed the level.</p>';
    const bars = s.probs.map((p, i) => `
        <div class="prob-row"><span>${esiBadge(i + 1)}</span><div class="prob-track" role="img" aria-label="ESI ${i + 1}: ${pct(p)}"><span class="bg-esi-${i + 1}" style="width:${p * 100}%"></span></div><b>${pct(p)}</b></div>`).join('');

    $('#ai-result').innerHTML = `
      ${flags}
      <div class="ai-hero">
        <div class="ai-level bg-esi-${s.esi}" aria-hidden="true">${s.esi}</div>
        <div><h3 style="font-size:19px">ESI ${s.esi} · ${ESI_NAMES[s.esi]}</h3>
          <p class="muted">${pct(s.confidence)} confident · see a doctor ${ESI_TARGET[s.esi] ? 'within ' + ESI_TARGET[s.esi] + ' min' : 'immediately'}</p>${bumped}</div>
      </div>
      <div class="mini-stats">
        <div class="mini-stat"><span>Expected consult</span><b>${s.minutes} min</b></div>
        <div class="mini-stat"><span>NEWS2 score</span><b>${s.news2}</b></div>
        <div class="mini-stat"><span>Model's pick</span><b>ESI ${s.model_esi}</b></div>
      </div>
      <div class="section-title">Why this level</div>${reasons}
      <div class="section-title">Probability of each level</div>${bars}`;

    if (!form.elements.final_esi.value || !form.dataset.touched) {
      const radio = $(`input[name="final_esi"][value="${s.esi}"]`, form);
      if (radio && !isRetriage) radio.checked = true;
    }
    syncOverride();
  };

  const preview = () => {
    markRanges();
    const data = values();
    if (Object.values(data).some(v => v === '')) return;
    clearTimeout(timer);
    timer = setTimeout(async () => {
      try {
        render(await getJSON('/api/triage/preview', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
        }));
      } catch (e) { /* half-typed value */ }
    }, 250);
  };

  form.addEventListener('input', e => {
    if (e.target.name === 'final_esi') { form.dataset.touched = '1'; syncOverride(); return; }
    if (e.target.name === 'override_reason') return;
    preview();
  });

  $('#fill-example')?.addEventListener('click', () => {
    const examples = [
      { name: 'Ramesh Kulkarni', age: 58, heart_rate: 124, systolic_bp: 92, resp_rate: 24, spo2: 93, temperature: 36.9, pain: 8, consciousness: 0, complaint: 'chest', sex: 'M', notes: 'Crushing chest pain for 30 min, sweating' },
      { name: 'Sneha Pawar', age: 24, heart_rate: 82, systolic_bp: 122, resp_rate: 16, spo2: 99, temperature: 36.8, pain: 5, consciousness: 0, complaint: 'minor', sex: 'F', notes: 'Twisted ankle playing football' },
      { name: 'Mohd. Arif', age: 19, heart_rate: 128, systolic_bp: 108, resp_rate: 34, spo2: 86, temperature: 37.2, pain: 4, consciousness: 1, complaint: 'breathing', sex: 'M', notes: 'Known asthmatic, cannot finish sentences' },
      { name: 'Kamala Devi', age: 72, heart_rate: 92, systolic_bp: 188, resp_rate: 18, spo2: 95, temperature: 36.9, pain: 2, consciousness: 1, complaint: 'stroke', sex: 'F', notes: 'Slurred speech since 1 hour' },
    ];
    const ex = examples[Math.floor(Math.random() * examples.length)];
    Object.entries(ex).forEach(([k, v]) => { if (form.elements[k]) form.elements[k].value = v; });
    delete form.dataset.touched;
    preview();
  });

  preview();
  if (!isRetriage) poll(refreshQueueTable);
}

async function refreshQueueTable() {
  const s = await getJSON('/api/state');
  setHTML($('#queue-sub'), `${s.queue.length} waiting · low-priority patients move up a level every ${s.aging} min (up to level 3)`);
  if (!s.queue.length) {
    setHTML($('#queue-table'), '<div class="empty"><strong>Nobody is waiting</strong><p>Patients appear here, in calling order, as soon as you issue a token.</p></div>');
    return;
  }
  setHTML($('#queue-table'), `<table class="stack-table">
    <thead><tr><th>#</th><th>Token</th><th>Patient</th><th>Complaint</th><th>Triage</th><th class="num">Waiting</th>
      <th class="num">Est. wait</th><th>Flags</th><th><span class="sr-only">Actions</span></th></tr></thead>
    <tbody>${s.queue.map(q => `<tr class="${q.over_target ? 'row-warn' : ''}">
      <td class="muted pos-cell" data-label="Position">${q.position}</td><td data-label="Token" class="token-cell"><span><span class="m-pos">#${q.position} · </span><b>${q.token}</b></span></td>
      <td data-label="Patient">${escapeHtml(q.name)} <span class="muted">${q.age}${q.sex}</span></td>
      <td data-label="Complaint">${escapeHtml(q.complaint)}</td>
      <td data-label="Triage"><span class="notes-cell">${esiBadge(q.esi)} ${agedTag(q)}
          ${q.overridden ? `<span class="badge info" title="The AI suggested ESI ${q.ai_esi}">nurse override</span>` : ''}</span></td>
      <td class="num" data-label="Waiting">${q.waited} min</td>
      <td class="num" data-label="Est. wait">${q.eta_range ? `${q.eta_range[0]}–${q.eta_range[1]} min` : '–'}</td>
      <td data-label="Flags" class="${!q.paused && !q.recheck && !q.over_target ? 'no-flags' : ''}"><span class="notes-cell">${q.paused ? `<span class="badge warn">${icon('pause')}paused, goes first</span>` : ''}${q.recheck ? '<span class="badge bad">re-check vitals</span>' : ''}${q.over_target ? '<span class="badge warn">over target</span>' : ''}${!q.paused && !q.recheck && !q.over_target ? '<span class="muted">–</span>' : ''}</span></td>
      <td class="no-label"><div class="row-actions"><a class="btn btn-sm" href="/triage?retriage=${q.id}" aria-label="Re-triage ${q.token}">Re-triage</a>
        ${postButton(`/visits/${q.id}/left`, 'Mark left', 'btn btn-sm btn-ghost', { confirm: `Mark ${q.token} as left without being seen?`, aria: `Mark ${q.token} as left without being seen` })}</div></td>
    </tr>`).join('')}</tbody></table>`);
}

// ---------------------------------------------------------------- doctor console
function initDoctor() {
  const me = Number(document.body.dataset.me);
  poll(async () => {
    const s = await getJSON('/api/state');
    const patient = s.my_patient;
    const mine = s.doctors.find(d => d.id === me);

    // ESI 1 preemption banner
    const critical = s.queue.find(q => q.esi === 1);
    const banner = $('#preempt-banner');
    if (critical && s.preempt_doctor === me && patient) {
      banner.classList.remove('hidden');
      setHTML(banner, `${icon('alert')}<div style="flex:1"><strong>Resuscitation patient waiting: ${critical.token}</strong>
        <p>Every doctor is busy and your patient (ESI ${patient.esi}) is the least urgent one being seen. Their time so far is kept and they go back to the front of their level.</p></div>
        ${postButton('/doctor/preempt', `Pause and see ${critical.token}`, 'btn btn-danger')}`);
    } else if (critical && !patient) {
      banner.classList.remove('hidden');
      setHTML(banner, `${icon('alert')}<div style="flex:1"><strong>ESI 1 patient waiting: ${critical.token}</strong><p>You are free, so they will be your next patient. Press “Call next patient”.</p></div>`);
    } else {
      banner.classList.add('hidden');
    }

    const card = $('#current-card');
    if (patient) {
      const progress = Math.min(100, (patient.done / patient.predicted_minutes) * 100);
      setHTML(card, `
        <div class="card-head"><h2>Current patient</h2><span class="badge teal">${patient.token}</span></div>
        <div class="patient-head">
          <div class="ai-level bg-esi-${patient.esi}" aria-label="ESI ${patient.esi}">${patient.esi}</div>
          <div><h3 style="font-size:20px">${escapeHtml(patient.name)}</h3><p class="muted">${patient.age} yrs · ${patient.sex} · ${escapeHtml(patient.complaint_label)}</p></div>
        </div>
        <div class="vitals-grid">
          ${[['HR', patient.heart_rate + ' bpm'], ['BP', patient.systolic_bp + ' mmHg'], ['RR', patient.resp_rate + '/min'],
             ['SpO₂', patient.spo2 + '%'], ['Temp', patient.temperature + ' °C'], ['Pain', patient.pain + '/10'],
             ['AVPU', patient.consciousness_label], ['Waited', patient.waited + ' min']]
            .map(([k, v]) => `<div><span>${k}</span><b>${v}</b></div>`).join('')}
        </div>
        ${patient.override_reason ? `<div class="callout info" style="margin-top:12px">${icon('info')}<div><strong>The nurse changed the AI level from ${patient.ai_esi} to ${patient.esi}</strong><p>${escapeHtml(patient.override_reason)}</p></div></div>` : ''}
        ${patient.notes ? `<p style="margin-top:12px;font-size:13px"><b>Notes:</b> ${escapeHtml(patient.notes)}</p>` : ''}
        <div class="progress" role="progressbar" aria-label="Consultation time" aria-valuemin="0" aria-valuemax="${patient.predicted_minutes}" aria-valuenow="${patient.done}"><span style="width:${progress}%"></span></div>
        <div class="doctor-meta"><span>${patient.done} min so far</span><span>AI expected about ${patient.predicted_minutes} min</span></div>
        <div class="row" style="margin-top:16px">
          ${postButton('/doctor/discharge', 'Discharge', 'btn btn-primary')}
          ${postButton('/doctor/admit', `Admit to observation · ${s.semaphore} bed${s.semaphore === 1 ? '' : 's'} free`, 'btn')}
        </div>`);
    } else {
      const onDuty = mine && mine.on_duty;
      setHTML(card, `
        <div class="card-head"><h2>Current patient</h2></div>
        <div class="empty" style="padding:30px 10px"><strong>${onDuty ? 'You are free' : 'You are off duty'}</strong>
          <p style="margin:4px auto 16px">${onDuty
            ? (s.queue.length ? `${s.queue.length} patient${s.queue.length === 1 ? ' is' : 's are'} waiting. The dispatcher picks the most urgent one for you.` : 'Nobody is waiting right now.')
            : 'Start your shift to receive patients.'}</p>
          ${onDuty && s.queue.length ? postButton('/doctor/call-next', 'Call next patient', 'btn btn-primary btn-lg') : ''}
        </div>`);
    }

    setHTML($('#up-next'), s.queue.slice(0, 6).map(q => `
      <div class="queue-item ${q.over_target ? 'breach' : ''}"><span class="queue-pos">#${q.position}</span>
        <div><div class="queue-name">${q.token} · ${escapeHtml(q.name)} ${esiBadge(q.esi)} ${agedTag(q, true)}</div>
          <div class="queue-sub">${escapeHtml(q.complaint)} · about ${q.predicted} min${q.paused ? ' · paused earlier' : ''}</div></div>
        <div class="queue-right">${q.waited} min<small>waiting</small></div></div>`).join('')
      || '<div class="empty"><strong>Nobody is waiting</strong><p>New patients appear here as soon as the triage desk issues a token.</p></div>');

    setHTML($('#colleagues'), s.doctors.filter(d => d.id !== me).map(d => `
      <div class="colleague"><span class="avatar" aria-hidden="true">${escapeHtml(d.name.replace('Dr. ', '')[0])}</span>
        <div><b>${escapeHtml(d.name)}</b><small>${d.room} · ${d.on_duty ? (d.current ? `with ${d.current.token} (ESI ${d.current.esi}) for ${d.current.done} min` : 'free') : 'off duty'}</small></div></div>`).join(''));

    renderLog(s.events);
  });
}

function renderLog(events) {
  const el = $('#event-log');
  if (!el) return;
  const kindClass = { triaged: 'arrive', called: 'start', discharged: 'finish', paused: 'preempt', retriaged: 'aging', bed: 'finish', boarding: 'info', left: 'preempt' };
  setHTML(el, events.map(e => `<div class="log-item ${kindClass[e.kind] || ''}"><span class="log-time">${e.at}</span><span class="log-dot" aria-hidden="true"></span>
    <span>${e.token ? `<b>${e.token}</b> ` : ''}${escapeHtml(e.detail || e.kind)}${e.who ? ` <span class="muted">· ${escapeHtml(e.who)}</span>` : ''}</span></div>`).join('')
    || '<div class="empty">No activity yet.</div>');
}

// ---------------------------------------------------------------- bed board
function initBeds() {
  const STATUS = { free: ['Free', 'ok'], occupied: ['Occupied', 'info'], cleaning: ['Cleaning', 'warn'] };
  poll(async () => {
    const s = await getJSON('/api/state');
    const count = st => s.beds.filter(b => b.status === st).length;
    setHTML($('#bed-stats'), `
      <div class="stat good"><div class="label">Free beds</div><div class="value">${s.semaphore}<small>/ ${s.beds.length}</small></div><div class="foot">the semaphore's value</div></div>
      <div class="stat"><div class="label">Occupied</div><div class="value">${count('occupied')}</div><div class="foot">patients in observation</div></div>
      <div class="stat"><div class="label">Being cleaned</div><div class="value">${count('cleaning')}</div><div class="foot">“Mark clean” = signal()</div></div>
      <div class="stat ${s.boarding.length ? 'alert' : ''}"><div class="label">Boarding</div><div class="value">${s.boarding.length}</div><div class="foot">blocked, waiting for a bed</div></div>`);

    setHTML($('#bed-grid'), s.beds.map(b => `
      <div class="bed ${b.status}">
        <div class="bed-top"><b>${b.label}</b><span class="badge ${STATUS[b.status][1]}">${STATUS[b.status][0]}</span></div>
        <div class="bed-body">${b.status === 'occupied' ? `${b.token} · ${escapeHtml(b.name)} ${esiBadge(b.esi)}` : b.status === 'cleaning' ? 'With housekeeping' : 'Ready for the next patient'}</div>
        ${b.status === 'occupied' ? postButton(`/beds/${b.id}/release`, 'Release bed', 'btn btn-sm', { aria: `Release ${b.label} (${b.token} leaves the ward)` }) : ''}
        ${b.status === 'cleaning' ? postButton(`/beds/${b.id}/clean`, 'Mark clean', 'btn btn-sm btn-primary', { aria: `Mark ${b.label} as clean` }) : ''}
      </div>`).join(''));

    $('#boarding-count').textContent = s.boarding.length;
    setHTML($('#boarding'), s.boarding.map((b, i) => `
      <div class="queue-item"><span class="queue-pos">#${i + 1}</span><div><div class="queue-name">${b.token} · ${escapeHtml(b.name)} ${esiBadge(b.esi)}</div></div>
        <div class="queue-right">${b.since} min<small>boarding</small></div></div>`).join('')
      || '<div class="empty"><strong>Nobody is waiting for a bed</strong><p>When a doctor admits a patient and no bed is free, they wait here.</p></div>');
  });
}

// ---------------------------------------------------------------- waiting room display
function initBoard() {
  poll(async () => {
    const b = await getJSON('/api/board');
    setHTML($('#calling'), b.calling.map(c => `<div class="calling"><b>${c.token}</b><span>${c.room}</span></div>`).join('')
      || '<p class="board-empty">No one is with a doctor right now.</p>');
    $('#waiting-count').textContent = `(${b.waiting.length})`;
    setHTML($('#waiting'), b.waiting.map(w => `
      <div class="waiting-tile ${w.urgent ? 'urgent' : ''}"><b>${w.token}</b>
        <span>${w.urgent ? 'Next' : w.eta_range ? `about ${w.eta_range[0]}–${w.eta_range[1]} min` : 'Please wait'}</span></div>`).join('')
      || '<p class="board-empty">Nobody is waiting.</p>');
  }, 5000);
}

// ---------------------------------------------------------------- admin reports
function initAdmin() {
  poll(async () => {
    const s = await getJSON('/api/state');
    const st = s.stats;
    setHTML($('#admin-stats'), [
      ['Waiting now', st.waiting, `${st.in_consult} with a doctor`],
      ['Doctors on duty', st.on_duty, 'of 4'],
      ['Seen, last 12 h', st.seen_today, `${st.left} left without being seen`],
      ['Avg wait, 12 h', `${st.avg_wait}<small>min</small>`, 'arrival to doctor'],
      ['ESI 1–2 wait, 12 h', `${st.critical_wait}<small>min</small>`, 'target 10 min or less'],
      ['Nurse overrides', st.overrides, 'AI suggestion changed'],
    ].map(([l, v, f]) => `<div class="stat"><div class="label">${l}</div><div class="value">${v}</div><div class="foot">${f}</div></div>`).join(''));
  }, 10000);

  getJSON('/api/admin/reports').then(r => {
    $('#chart-wait-esi').innerHTML = Charts.bars({
      categories: r.by_esi.map(x => `ESI ${x.esi}`),
      series: [{ name: 'Average wait (min)', color: 'var(--accent)', values: r.by_esi.map(x => x.wait || 0) }],
      width: 520, height: 230,
    });
    $('#chart-hourly').innerHTML = Charts.bars({
      categories: r.hourly.map(x => String(x.hour).padStart(2, '0')),
      series: [{ name: 'Arrivals', color: 'var(--primary)', values: r.hourly.map(x => x.n) }],
      width: 520, height: 230, decimals: 0, emptyText: 'No arrivals in the last 12 hours',
    });

    // AI vs nurse agreement matrix
    const grid = [...Array(5)].map(() => new Array(5).fill(0));
    r.agreement.forEach(a => { grid[a.ai_esi - 1][a.esi - 1] += a.n; });
    const total = grid.flat().reduce((a, b) => a + b, 0);
    const agree = grid.reduce((s, row, i) => s + row[i], 0);
    $('#agreement').innerHTML = `<table class="confusion"><caption class="sr-only">Rows: AI suggestion. Columns: nurse's final level.</caption>
      <thead><tr><th scope="col">AI ↓ · nurse →</th>${[1, 2, 3, 4, 5].map(l => `<th scope="col">ESI ${l}</th>`).join('')}</tr></thead>
      <tbody>${grid.map((row, i) => `<tr><th scope="row">ESI ${i + 1}</th>${row.map((n, j) => `<td class="cell" style="background:${n ? (i === j ? 'rgba(13,148,136,' : 'rgba(225,29,72,') + (0.15 + Math.min(0.7, n / 12)) + ')' : 'transparent'}">${n || ''}</td>`).join('')}</tr>`).join('')}</tbody></table>
      <p class="axis-caption">Nurses kept the AI suggestion in ${agree} of ${total} visits (${pct(agree / Math.max(1, total))}).</p>`;

    if (r.metrics) {
      const imp = Object.entries(r.metrics.importance)
        .reduce((acc, [k, v]) => { const key = k.startsWith('complaint_') ? 'complaint' : k; acc[key] = (acc[key] || 0) + v; return acc; }, {});
      const labels = { age: 'Age', heart_rate: 'Heart rate', systolic_bp: 'Systolic BP', resp_rate: 'Resp. rate', spo2: 'SpO₂',
        temperature: 'Temperature', consciousness: 'Consciousness', pain: 'Pain score', complaint: 'Complaint' };
      $('#chart-importance').innerHTML = Charts.hbars(Object.entries(imp).sort((a, b) => b[1] - a[1])
        .map(([k, v]) => ({ label: labels[k], value: v, display: pct(v) })));
    }

    if (r.simulation) {
      const pol = Object.values(r.simulation.policies);
      const names = pol.map(p => p.label.replace(' (token order)', '').replace(' (triage only)', '').replace(' (ours)', '').replace('Shortest predicted first', 'SJF'));
      const colors = pol.map(p => (p.label.includes('ours') ? 'var(--primary)' : 'var(--border-strong)'));
      const small = (values, decimals = 0) => Charts.bars({
        categories: names, series: [{ name: '', color: 'var(--primary)', values }], width: 300, height: 220, decimals,
      });
      $('#chart-critical').innerHTML = small(pol.map(p => p.critical_wait), 1);
      $('#chart-tail').innerHTML = small(pol.map(p => p.low_priority_p95));
      // highlight our policy's bar, grey for the baselines
      ['#chart-critical', '#chart-tail'].forEach(sel => $$(`${sel} rect`).forEach((rect, i) => { rect.style.fill = colors[i]; }));

      const sweep = r.simulation.aging_sweep;
      const cats = sweep.map(x => x.aging ? `${x.aging}` : 'off');
      $('#chart-aging-tail').innerHTML = Charts.bars({ categories: cats, series: [{ name: '', color: 'var(--esi-5)', values: sweep.map(x => x.low_p95) }], width: 240, height: 200, decimals: 0 });
      $('#chart-aging-esi3').innerHTML = Charts.bars({ categories: cats, series: [{ name: '', color: 'var(--esi-3)', values: sweep.map(x => x.esi3_target * 100) }], width: 240, height: 200, decimals: 0 });
      // the interval we actually use stays solid, the others fade back
      ['#chart-aging-tail', '#chart-aging-esi3'].forEach(sel => $$(`${sel} rect`).forEach((rect, i) => { if (cats[i] !== '30') rect.style.opacity = 0.4; }));
    }
  });
}

// ---------------------------------------------------------------- router
({ login: initLogin, triage: initTriage, doctor: initDoctor, beds: initBeds, board: initBoard, admin: initAdmin })[document.body.dataset.page]?.();
