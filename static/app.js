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

async function getJSON(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) throw new Error(res.status);
  return res.json();
}

function poll(fn, every = 5000) {
  const run = () => fn().catch(err => console.warn('refresh failed', err));
  run();
  return setInterval(run, every);
}

function postButton(action, label, cls = 'btn btn-sm', confirmText = '') {
  const onsubmit = confirmText ? ` onsubmit="return confirm('${confirmText}')"` : '';
  return `<form method="post" action="${action}" class="inline-form"${onsubmit}><button class="${cls}">${label}</button></form>`;
}

// ---------------------------------------------------------------- shell
(function shell() {
  const tick = () => {
    const el = $('#live-clock') || $('#board-clock');
    if (el) el.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };
  tick();
  setInterval(tick, 15000);

  $('#theme-btn')?.addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('mq-theme', next); } catch (e) { /* private mode */ }
  });
  $('#menu-btn')?.addEventListener('click', () => {
    $('#sidebar').classList.add('open');
    $('#sidebar-backdrop').classList.add('show');
  });
  $('#sidebar-backdrop')?.addEventListener('click', () => {
    $('#sidebar').classList.remove('open');
    $('#sidebar-backdrop').classList.remove('show');
  });
  // flash messages fade out on their own
  $$('.flash').forEach(el => setTimeout(() => el.classList.add('fade'), 6000));
})();

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
      field.classList.toggle('abnormal', input.value !== '' && (v < lo || v > hi));
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
      ? `<div class="callout danger" style="margin-bottom:12px"><div><strong>Red flag – ESI 1 regardless of the model</strong><p>${s.red_flags.map(escapeHtml).join(' · ')}</p></div></div>` : '';
    const bumped = s.safety_bumped
      ? `<p class="muted" style="font-size:12px;margin-top:6px">Model's top choice was ESI ${s.model_esi}; moved one level up because it gave ESI ${s.esi} ≥ 30% probability.</p>` : '';
    const reasons = s.reasons.length ? s.reasons.map(r => `
        <div class="reason ${r.effect > 0 ? 'up' : 'down'}"><span>${escapeHtml(r.feature)}: <b>${escapeHtml(r.value)}</b></span>
          <span>${r.effect > 0 ? 'raises' : 'lowers'} urgency</span></div>`).join('')
      : '<p class="muted" style="font-size:12.5px">Vitals are close to normal – nothing in particular pushed the level.</p>';
    const bars = s.probs.map((p, i) => `
        <div class="prob-row"><span>${esiBadge(i + 1)}</span><div class="prob-track"><span class="bg-esi-${i + 1}" style="width:${p * 100}%"></span></div><b>${pct(p)}</b></div>`).join('');

    $('#ai-result').innerHTML = `
      ${flags}
      <div class="ai-hero">
        <div class="ai-level bg-esi-${s.esi}">${s.esi}</div>
        <div><h2>${ESI_NAMES[s.esi]}</h2>
          <p class="muted">${pct(s.confidence)} confident · see a doctor ${ESI_TARGET[s.esi] ? 'within ' + ESI_TARGET[s.esi] + ' min' : 'immediately'}</p>${bumped}</div>
      </div>
      <div class="mini-stats">
        <div class="mini-stat"><span>Expected consult</span><b>${s.minutes} min</b></div>
        <div class="mini-stat"><span>NEWS2</span><b>${s.news2}</b></div>
        <div class="mini-stat"><span>Model's pick</span><b>ESI ${s.model_esi}</b></div>
      </div>
      <div class="section-title">Why</div>${reasons}
      <div class="section-title">Probabilities</div>${bars}`;

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
  $('#queue-sub').textContent = `· ${s.queue.length} waiting · aging every ${s.aging} min`;
  if (!s.queue.length) {
    $('#queue-table').innerHTML = '<div class="empty"><strong>Nobody is waiting</strong></div>';
    return;
  }
  $('#queue-table').innerHTML = `<table>
    <thead><tr><th>#</th><th>Token</th><th>Patient</th><th>Complaint</th><th>Triage</th><th class="num">Waiting</th>
      <th class="num">Est. wait</th><th>Notes</th><th></th></tr></thead>
    <tbody>${s.queue.map(q => `<tr class="${q.over_target ? 'row-warn' : ''}">
      <td class="muted">${q.position}</td><td><b>${q.token}</b></td>
      <td>${escapeHtml(q.name)} <span class="muted">${q.age}${q.sex}</span></td>
      <td>${escapeHtml(q.complaint)}</td>
      <td>${esiBadge(q.esi)} ${q.effective < q.esi ? `<span class="aged">↑ aged to ${q.effective}</span>` : ''}
          ${q.overridden ? `<span class="badge info" title="AI suggested ESI ${q.ai_esi}">nurse override</span>` : ''}</td>
      <td class="num">${q.waited} min</td>
      <td class="num">${q.eta_range ? `${q.eta_range[0]}–${q.eta_range[1]} min` : '–'}</td>
      <td>${q.paused ? '<span class="badge warn">paused, resumes first</span> ' : ''}${q.recheck ? '<span class="badge bad">re-check vitals</span>' : ''}
          ${q.over_target ? '<span class="badge warn">over target</span>' : ''}</td>
      <td class="row-actions"><a class="btn btn-sm" href="/triage?retriage=${q.id}">Re-triage</a>
        ${postButton(`/visits/${q.id}/left`, 'Left', 'btn btn-sm btn-ghost', 'Mark this patient as left without being seen?')}</td>
    </tr>`).join('')}</tbody></table>`;
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
      banner.innerHTML = `<div style="flex:1"><strong>Resuscitation case waiting: ${critical.token}</strong>
        <p>Every doctor is busy and your patient (ESI ${patient.esi}) is the least urgent one being seen. Pause them and attend now?</p></div>
        ${postButton('/doctor/preempt', 'Pause &amp; attend', 'btn btn-danger')}`;
    } else if (critical && !patient) {
      banner.classList.remove('hidden');
      banner.innerHTML = `<div style="flex:1"><strong>ESI 1 patient waiting: ${critical.token}</strong><p>You are free – they will be your next patient.</p></div>`;
    } else {
      banner.classList.add('hidden');
    }

    if (patient) {
      const progress = Math.min(100, (patient.done / patient.predicted_minutes) * 100);
      $('#current-card').innerHTML = `
        <div class="card-head"><h3>Current patient</h3><span class="badge teal">${patient.token}</span></div>
        <div class="patient-head">
          <div class="ai-level bg-esi-${patient.esi}">${patient.esi}</div>
          <div><h2>${escapeHtml(patient.name)}</h2><p class="muted">${patient.age} yrs · ${patient.sex} · ${escapeHtml(patient.complaint_label)}</p></div>
        </div>
        <div class="vitals-grid">
          ${[['HR', patient.heart_rate + ' bpm'], ['BP', patient.systolic_bp + ' mmHg'], ['RR', patient.resp_rate + '/min'],
             ['SpO₂', patient.spo2 + '%'], ['Temp', patient.temperature + ' °C'], ['Pain', patient.pain + '/10'],
             ['AVPU', patient.consciousness_label], ['Waited', patient.waited + ' min']]
            .map(([k, v]) => `<div><span>${k}</span><b>${v}</b></div>`).join('')}
        </div>
        ${patient.override_reason ? `<div class="callout info" style="margin-top:12px"><div><strong>Nurse changed AI level ${patient.ai_esi} → ${patient.esi}</strong><p>${escapeHtml(patient.override_reason)}</p></div></div>` : ''}
        ${patient.notes ? `<p style="margin-top:12px;font-size:13px"><b>Notes:</b> ${escapeHtml(patient.notes)}</p>` : ''}
        <div class="progress" style="margin-top:14px"><span style="width:${progress}%"></span></div>
        <div class="doctor-meta"><span>${patient.done} min so far</span><span>AI expected ~${patient.predicted_minutes} min</span></div>
        <div class="row" style="margin-top:16px">
          ${postButton('/doctor/discharge', 'Discharge', 'btn btn-primary')}
          ${postButton('/doctor/admit', `Admit to observation (${s.semaphore} bed${s.semaphore === 1 ? '' : 's'} free)`, 'btn')}
        </div>`;
    } else {
      $('#current-card').innerHTML = `
        <div class="card-head"><h3>Current patient</h3></div>
        <div class="empty" style="padding:30px 10px"><strong>You are free</strong>
          <p style="margin:4px 0 16px">${mine && mine.on_duty ? `${s.queue.length} patient${s.queue.length === 1 ? '' : 's'} waiting.` : 'Start your shift to receive patients.'}</p>
          ${mine && mine.on_duty ? postButton('/doctor/call-next', 'Call next patient', 'btn btn-primary btn-lg') : ''}
        </div>`;
    }

    $('#up-next').innerHTML = s.queue.slice(0, 6).map(q => `
      <div class="queue-item ${q.over_target ? 'breach' : ''}"><span class="queue-pos">#${q.position}</span>
        <div><div class="queue-name">${q.token} · ${escapeHtml(q.name)} ${esiBadge(q.esi)} ${q.effective < q.esi ? `<span class="aged">↑ ${q.effective}</span>` : ''}</div>
          <div class="queue-sub">${escapeHtml(q.complaint)} · ~${q.predicted} min consult${q.paused ? ' · paused earlier' : ''}</div></div>
        <div class="queue-right">${q.waited} min<small>waiting</small></div></div>`).join('')
      || '<div class="empty">Queue is empty.</div>';

    $('#colleagues').innerHTML = s.doctors.filter(d => d.id !== me).map(d => `
      <div class="colleague"><span class="avatar">${escapeHtml(d.name.replace('Dr. ', '')[0])}</span>
        <div><b>${escapeHtml(d.name)}</b><small>${d.room} · ${d.on_duty ? (d.current ? `with ${d.current.token} (ESI ${d.current.esi}) for ${d.current.done} min` : 'free') : 'off duty'}</small></div></div>`).join('');

    renderLog(s.events);
  });
}

function renderLog(events) {
  const el = $('#event-log');
  if (!el) return;
  const kindClass = { triaged: 'arrive', called: 'start', discharged: 'finish', paused: 'preempt', retriaged: 'aging', bed: 'finish', boarding: 'info', left: 'preempt' };
  el.innerHTML = events.map(e => `<div class="log-item ${kindClass[e.kind] || ''}"><span class="log-time">${e.at}</span><span class="log-dot"></span>
    <span>${e.token ? `<b>${e.token}</b> ` : ''}${escapeHtml(e.detail || e.kind)}${e.who ? ` <span class="muted">– ${escapeHtml(e.who)}</span>` : ''}</span></div>`).join('');
}

// ---------------------------------------------------------------- bed board
function initBeds() {
  poll(async () => {
    const s = await getJSON('/api/state');
    const count = st => s.beds.filter(b => b.status === st).length;
    $('#bed-stats').innerHTML = `
      <div class="stat good"><div class="label">Semaphore value</div><div class="value">${s.semaphore}<small>free</small></div><div class="foot">beds available right now</div></div>
      <div class="stat"><div class="label">Occupied</div><div class="value">${count('occupied')}<small>/ ${s.beds.length}</small></div><div class="foot">patients in observation</div></div>
      <div class="stat"><div class="label">Being cleaned</div><div class="value">${count('cleaning')}</div><div class="foot">signal() when done</div></div>
      <div class="stat ${s.boarding.length ? 'alert' : ''}"><div class="label">Boarding</div><div class="value">${s.boarding.length}</div><div class="foot">blocked, waiting for a bed</div></div>`;

    $('#bed-grid').innerHTML = s.beds.map(b => `
      <div class="bed ${b.status}">
        <div class="bed-top"><b>${b.label}</b><span class="badge ${b.status === 'free' ? 'ok' : b.status === 'cleaning' ? 'warn' : 'info'}">${b.status}</span></div>
        <div class="bed-body">${b.status === 'occupied' ? `${b.token} · ${escapeHtml(b.name)} ${esiBadge(b.esi)}` : b.status === 'cleaning' ? 'Housekeeping' : 'Ready'}</div>
        ${b.status === 'occupied' ? postButton(`/beds/${b.id}/release`, 'Release', 'btn btn-sm') : ''}
        ${b.status === 'cleaning' ? postButton(`/beds/${b.id}/clean`, 'Mark clean', 'btn btn-sm btn-primary') : ''}
      </div>`).join('');

    $('#boarding-count').textContent = s.boarding.length;
    $('#boarding').innerHTML = s.boarding.map((b, i) => `
      <div class="queue-item"><span class="queue-pos">#${i + 1}</span><div><div class="queue-name">${b.token} · ${escapeHtml(b.name)} ${esiBadge(b.esi)}</div></div>
        <div class="queue-right">${b.since} min<small>boarding</small></div></div>`).join('')
      || '<div class="empty">Nobody is waiting for a bed.</div>';
  });
}

// ---------------------------------------------------------------- waiting room display
function initBoard() {
  poll(async () => {
    const b = await getJSON('/api/board');
    $('#calling').innerHTML = b.calling.map(c => `<div class="calling"><b>${c.token}</b><span>${c.room}</span></div>`).join('')
      || '<p class="board-empty">—</p>';
    $('#waiting-count').textContent = `(${b.waiting.length})`;
    $('#waiting').innerHTML = b.waiting.map(w => `
      <div class="waiting-tile ${w.urgent ? 'urgent' : ''}"><b>${w.token}</b>
        <span>${w.urgent ? 'Next' : w.eta_range ? `~${w.eta_range[0]}–${w.eta_range[1]} min` : 'Please wait'}</span></div>`).join('')
      || '<p class="board-empty">No one waiting</p>';
  }, 5000);
}

// ---------------------------------------------------------------- admin reports
function initAdmin() {
  poll(async () => {
    const s = await getJSON('/api/state');
    const st = s.stats;
    $('#admin-stats').innerHTML = [
      ['Waiting now', st.waiting, `${st.in_consult} with a doctor`],
      ['Doctors on duty', st.on_duty, 'of 4'],
      ['Seen today', st.seen_today, `${st.left} left without being seen`],
      ['Avg wait today', `${st.avg_wait}<small>min</small>`, 'arrival → doctor'],
      ['ESI 1–2 wait today', `${st.critical_wait}<small>min</small>`, 'target ≤ 10 min'],
      ['Nurse overrides', st.overrides, 'AI suggestion changed'],
    ].map(([l, v, f]) => `<div class="stat"><div class="label">${l}</div><div class="value">${v}</div><div class="foot">${f}</div></div>`).join('');
  }, 10000);

  getJSON('/api/admin/reports').then(r => {
    $('#chart-wait-esi').innerHTML = Charts.bars({
      categories: r.by_esi.map(x => `ESI ${x.esi}`),
      series: [{ name: 'Average wait (min)', color: 'var(--accent)', values: r.by_esi.map(x => x.wait || 0) }],
      width: 520, height: 230,
    });
    const hours = []; for (let h = 0; h < 24; h++) hours.push(h);
    const counts = Object.fromEntries(r.hourly.map(x => [x.hour, x.n]));
    const shown = hours.filter(h => h >= 6 && h <= 23);
    $('#chart-hourly').innerHTML = Charts.bars({
      categories: shown.map(h => String(h).padStart(2, '0')),
      series: [{ name: 'Arrivals', color: 'var(--primary)', values: shown.map(h => counts[h] || 0) }],
      width: 520, height: 230, decimals: 0,
    });

    // AI vs nurse agreement matrix
    const grid = [...Array(5)].map(() => new Array(5).fill(0));
    r.agreement.forEach(a => { grid[a.ai_esi - 1][a.esi - 1] += a.n; });
    const total = grid.flat().reduce((a, b) => a + b, 0);
    const agree = grid.reduce((s, row, i) => s + row[i], 0);
    $('#agreement').innerHTML = `<table class="confusion"><thead><tr><th>AI ↓ / Nurse →</th>${[1, 2, 3, 4, 5].map(l => `<th>ESI ${l}</th>`).join('')}</tr></thead>
      <tbody>${grid.map((row, i) => `<tr><th>ESI ${i + 1}</th>${row.map((n, j) => `<td class="cell" style="background:${n ? (i === j ? 'rgba(13,148,136,' : 'rgba(225,29,72,') + (0.15 + Math.min(0.7, n / 12)) + ')' : 'transparent'}">${n || ''}</td>`).join('')}</tr>`).join('')}</tbody></table>
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
      $('#chart-policies').innerHTML = Charts.bars({
        categories: pol.map(p => p.label.replace(' (token order)', '').replace(' (triage only)', '').replace(' (ours)', '')),
        series: [
          { name: 'ESI 1–2 mean wait', color: 'var(--esi-1)', values: pol.map(p => p.critical_wait) },
          { name: 'ESI 4–5 95th percentile ÷ 10', color: 'var(--esi-5)', values: pol.map(p => p.low_priority_p95 / 10) },
        ],
        width: 520, height: 250,
      });
      const sweep = r.simulation.aging_sweep;
      $('#chart-aging').innerHTML = Charts.bars({
        categories: sweep.map(x => x.aging ? `${x.aging} min` : 'none'),
        series: [
          { name: 'ESI 4–5 95th pct wait (min)', color: 'var(--esi-5)', values: sweep.map(x => x.low_p95) },
          { name: 'ESI 3 on time (%)', color: 'var(--esi-3)', values: sweep.map(x => x.esi3_target * 100) },
        ],
        width: 520, height: 250, decimals: 0,
      });
    }
  });
}

// ---------------------------------------------------------------- router
({ triage: initTriage, doctor: initDoctor, beds: initBeds, board: initBoard, admin: initAdmin })[document.body.dataset.page]?.();
