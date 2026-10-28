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
const SEX = { M: 'M', F: 'F', O: '' };
const esiBadge = l => `<span class="esi-badge esi-${l}">ESI ${l}</span>`;

// small inline icon set (one stroke style everywhere)
const ICONS = {
  up: '<path d="M12 19V5M5 12l7-7 7 7"/>',
  alert: '<path d="M12 3 2 20h20z"/><path d="M12 10v4M12 17h.01"/>',
  pause: '<path d="M9 5v14M15 5v14"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 8h.01M11 12h1v5h1"/>',
  ambulance: '<path d="M2.5 6.5h11v10h-11zM13.5 10h4.2l3.3 3.4v3.1h-7.5"/><circle cx="6.5" cy="17.5" r="1.9"/><circle cx="17" cy="17.5" r="1.9"/><path d="M8 8.8v4.4M5.8 11h4.4"/>',
};
const icon = (name, cls = 'ico') => `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name]}</svg>`;

// aging: a low-priority patient who has waited long enough is treated as a more urgent level
const agedTag = q => q.effective < q.esi
  ? `<span class="tag aged" title="Waited long enough to move up from ESI ${q.esi}">${icon('up')}now ${q.effective}</span>` : '';

// risk-adaptive aging: the AI thinks this ESI 3-5 patient may really be an emergency
const riskTag = q => q.high_risk
  ? `<span class="tag risk" title="The AI gives a ${pct(q.risk)} chance this is really an emergency (ESI 1–2), so they move up every ${q.aging_every} min and can reach level 2">risk ${pct(q.risk)}</span>` : '';
const ambulanceTag = q => (q.ambulance ? `<span class="tag amb">${icon('ambulance')}ambulance</span>` : '');
const etaWords = m => (m > 0 ? `in ${m} min` : m === 0 ? 'due now' : `${-m} min late`);

// every override carries its signed delta against the AI
function deltaTag(q) {
  if (!q.overridden) return '';
  const d = q.ai_esi - q.esi;
  const word = d > 0 ? 'up' : 'down';
  return `<span class="tag delta" title="The AI suggested ESI ${q.ai_esi}; the nurse chose ESI ${q.esi}">AI ${q.ai_esi} · ${word} ${Math.abs(d)}</span>`;
}

// length is duration: one shared minute scale, a tick at the patient's target
function waitScale(minutes) {
  return Math.ceil(Math.max(120, ...minutes) / 30) * 30;
}
function waitBar(waited, target, scale, late) {
  const w = Math.min(100, (waited / scale) * 100);
  const t = Math.min(100, (target / scale) * 100);
  return `<div class="waitbar${late ? ' late' : ''}" style="--w:${w}%;--t:${t}%" role="img"
    aria-label="Waited ${waited} min, target ${target ? target + ' min' : 'immediate'}"><i></i><u></u></div>`;
}

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
    return true;
  }
  return false;
}

// ---------------------------------------------------------------- motion
// Motion only where it explains a state change: the queue reordering, a token being called,
// the preemption alarm, a new AI level. Everything else stays still.
const EASE_OUT = 'cubic-bezier(.23, 1, .32, 1)';
const EASE_IN_OUT = 'cubic-bezier(.77, 0, .175, 1)';
const reduceMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

// Re-render a list and slide each patient from where they were to where they are now (FLIP),
// so someone moving up the queue is seen moving instead of teleporting.
function renderSliding(container, html, itemSelector) {
  if (!container) return false;
  const before = new Map($$(itemSelector, container).map(el => [el.dataset.token, el.getBoundingClientRect().top]));
  if (!setHTML(container, html)) return false;
  if (!before.size) return true; // first paint: nothing to slide from
  const calm = reduceMotion();
  $$(itemSelector, container).forEach(el => {
    const old = before.get(el.dataset.token);
    if (old === undefined) {
      el.animate([{ opacity: 0, transform: calm ? 'none' : 'translateY(-6px)' }, { opacity: 1, transform: 'none' }],
        { duration: calm ? 150 : 220, easing: EASE_OUT });
      return;
    }
    const dy = old - el.getBoundingClientRect().top;
    if (!calm && Math.abs(dy) > 1) {
      el.animate([{ transform: `translateY(${dy}px)` }, { transform: 'translateY(0)' }], { duration: 300, easing: EASE_IN_OUT });
    }
  });
  return true;
}

// restart a one-shot CSS animation class
function replay(el, cls) {
  el.classList.remove(cls);
  void el.offsetWidth;
  el.classList.add(cls);
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

function postButton(action, label, cls = 'btn btn-sm', { confirm = '', aria = '', fields = {} } = {}) {
  const onsubmit = confirm ? ` onsubmit="return confirm('${confirm.replace(/'/g, '&#39;')}')"` : '';
  const ariaAttr = aria ? ` aria-label="${escapeHtml(aria)}"` : '';
  const hidden = Object.entries(fields).map(([k, v]) => `<input type="hidden" name="${k}" value="${escapeHtml(v)}">`).join('');
  return `<form method="post" action="${action}" class="inline-form"${onsubmit}>${hidden}<button type="submit" class="${cls}"${ariaAttr}>${label}</button></form>`;
}

// the department runs on Indian Standard Time whatever the viewer's machine is set to
function tickClock() {
  const el = $('#live-clock') || $('#board-clock');
  if (el && online) {
    // 24-hour time, the way the department log and clinical notes write it
    const t = new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: 'Asia/Kolkata' });
    el.innerHTML = `${t} <small>IST</small>`;
  }
}

// live counts in the navigation: patients waiting, and the bed semaphore's value
function updateNav(s) {
  $$('#nav-waiting').forEach(el => { el.textContent = s.queue.length; });
  const amb = $('#nav-incoming');
  if (amb) amb.textContent = s.incoming.length || '';
  const beds = $('#nav-beds');
  if (beds) beds.textContent = `${s.semaphore}/${s.beds.length}`;
}

// ---------------------------------------------------------------- door plates
// Each consultation room is a door plate with a sliding Busy / Free / Off indicator.
// The plates are kept between refreshes so the slider can glide to its new position.
const roomNo = room => (String(room).match(/\d+/) || [room])[0];
const doorPos = d => (!d.on_duty ? 2 : d.current ? 0 : 1);
const doorText = d => (!d.on_duty ? 'Off duty'
  : d.held && !d.current ? `Held for ambulance <b>${d.held.code}</b> · ${etaWords(d.held.eta_min)}`
  : d.current ? `With <b>${d.current.token}</b> · ESI ${d.current.esi} · ${d.current.done} min${d.held ? ` · then <b>${d.held.code}</b>` : ''}`
  : 'Free · the next call goes here');
const sliderHTML = (labels = ['Busy', 'Free', 'Off']) =>
  `<span class="slider" aria-hidden="true"><span class="slider-thumb"></span>${labels.map(l => `<span>${l}</span>`).join('')}</span>`;

function setSlider(slider, pos, cls) {
  slider.dataset.pos = pos;
  slider.classList.remove('busy', 'idle', 'clean');
  if (cls) slider.classList.add(cls);
}

function renderDoors(el, doctors, me = null) {
  if (!el) return;
  $('.skeleton', el)?.remove();
  doctors.forEach(d => {
    let door = $(`[data-doctor="${d.id}"]`, el);
    if (!door) {
      door = document.createElement('article');
      door.className = `door${d.id === me ? ' me' : ''}`;
      door.dataset.doctor = d.id;
      door.innerHTML = `<span class="door-room" aria-hidden="true">${escapeHtml(roomNo(d.room))}</span>
        <span class="door-name">${escapeHtml(d.name)}${d.id === me ? ' <span class="muted">(you)</span>' : ''}</span>
        <span class="door-sub"></span>${sliderHTML()}`;
      el.appendChild(door);
    }
    const pos = doorPos(d);
    setSlider($('.slider', door), pos, pos === 0 ? 'busy' : pos === 2 ? 'idle' : '');
    door.classList.toggle('held', Boolean(d.held));
    setHTML($('.door-sub', door), doorText(d));
    door.setAttribute('aria-label', `${d.room}, ${d.name}: ${doorText(d).replace(/<[^>]+>/g, '')}`);
  });
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
  // once, on arrival: a door plate slides from Busy to Free, the app's signature in one gesture
  const demo = $('.login-wall .door:nth-child(2) .slider');
  if (demo) setTimeout(() => { demo.classList.add('slow'); setSlider(demo, 1, ''); }, 500);

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
  let shownLevel = null;

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
    if (changed) {
      const d = latest.esi - chosen;
      $('#override-delta').innerHTML = `AI suggested ESI ${latest.esi} · you chose ESI ${chosen} · <b>${d > 0 ? 'up' : 'down'} ${Math.abs(d)} level${Math.abs(d) === 1 ? '' : 's'}</b>`;
    }
    const reason = form.elements.override_reason;
    if (reason) reason.required = Boolean(changed && !isRetriage);
  };

  const render = s => {
    latest = s;
    const levelChanged = s.esi !== shownLevel;
    shownLevel = s.esi;
    const flags = s.red_flags.length
      ? `<div class="callout danger" style="margin-bottom:14px">${icon('alert')}<div><strong>Red flag: ESI 1 whatever the model says</strong><p>${s.red_flags.map(escapeHtml).join(' · ')}</p></div></div>` : '';
    const bumped = s.safety_bumped
      ? `<p class="hint" style="margin-top:6px">The model's first choice was ESI ${s.model_esi}. It gave ESI ${s.esi} at least 30%, so the safety margin moved it one level up.</p>` : '';
    const reasons = s.reasons.length
      ? `<ul class="reasons">${s.reasons.map(r => `<li class="${r.effect > 0 ? 'up' : 'down'}"><span>${escapeHtml(r.feature)} <b>${escapeHtml(r.value)}</b></span>
          <em>${icon('up')}${r.effect > 0 ? 'raises' : 'lowers'} urgency</em></li>`).join('')}</ul>`
      : '<p class="hint">The vitals are close to normal, so nothing in particular pushed the level.</p>';
    const top = s.probs.indexOf(Math.max(...s.probs));
    const strip = `<div class="prob-strip" role="img" aria-label="${s.probs.map((p, i) => `ESI ${i + 1} ${pct(p)}`).join(', ')}">
        ${s.probs.map((p, i) => `<span class="bg-esi-${i + 1}" style="width:${p * 100}%"></span>`).join('')}</div>
      <div class="prob-legend" aria-hidden="true">${s.probs.map((p, i) => `<div class="${i === top ? 'top' : ''}">ESI ${i + 1}<b>${pct(p)}</b></div>`).join('')}</div>`;

    $('#ai-result').innerHTML = `
      ${flags}
      <div class="verdict">
        <div class="level-plate bg-esi-${s.esi}" aria-hidden="true"><small>ESI</small><b>${s.esi}</b><span>${ESI_NAMES[s.esi]}</span></div>
        <div>
          <p class="verdict-title">ESI ${s.esi} · ${ESI_NAMES[s.esi]}</p>
          <p class="verdict-meta">${pct(s.confidence)} confident · see a doctor ${ESI_TARGET[s.esi] ? 'within ' + ESI_TARGET[s.esi] + ' min' : 'immediately'}</p>${bumped}
          <dl class="facts">
            <div><dt>Consult</dt><dd>${s.minutes} min</dd></div>
            <div><dt>NEWS2</dt><dd>${s.news2}</dd></div>
            <div><dt>Model</dt><dd>ESI ${s.model_esi}</dd></div>
          </dl>
        </div>
      </div>
      <h3 class="sub-head">Why this level</h3>${reasons}
      <h3 class="sub-head">Probability of each level</h3>${strip}`;
    // the plate settles in only when the suggested level actually changes, never on every keystroke
    if (levelChanged) $('#ai-result .level-plate')?.classList.add('settle');

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
    if (document.body.dataset.page === 'prealert') {
      examples.splice(0, examples.length,
        { name: '', age: 63, heart_rate: 118, systolic_bp: 96, resp_rate: 26, spo2: 91, temperature: 37.0, pain: 8, consciousness: 0, complaint: 'chest', sex: 'M', eta_minutes: 8, notes: 'Chest pain at home 40 min, aspirin given by crew' },
        { name: 'Unknown', age: 30, heart_rate: 124, systolic_bp: 102, resp_rate: 24, spo2: 95, temperature: 36.7, pain: 9, consciousness: 0, complaint: 'trauma', sex: 'M', eta_minutes: 12, notes: 'Two-wheeler accident, open fracture left leg' },
        { name: 'Savitri Patil', age: 71, heart_rate: 88, systolic_bp: 192, resp_rate: 18, spo2: 96, temperature: 36.8, pain: 1, consciousness: 1, complaint: 'stroke', sex: 'F', eta_minutes: 6, notes: 'Left-sided weakness, onset 50 min ago' });
    }
    const ex = examples[Math.floor(Math.random() * examples.length)];
    Object.entries(ex).forEach(([k, v]) => { if (form.elements[k]) form.elements[k].value = v; });
    delete form.dataset.touched;
    preview();
  });

  preview();
  if (!isRetriage) poll(refreshQueueTable);
}

// ambulances on the way: countdown, held room, Arrived / Cancel
function renderIncoming(s) {
  const list = $('#incoming');
  if (!list) return;
  $('#incoming-panel')?.classList.toggle('hidden', !s.incoming.length);
  const note = $('#hold-note');
  if (note) {
    setHTML(note, s.hold_rooms ? 'Rooms are held for ESI 1–2 ambulances. ESI 3–5 join the queue when they arrive.'
      : 'Room holding is switched off in Reports &amp; settings: ambulances join the queue when they arrive.');
  }
  const back = location.pathname;
  setHTML(list, s.incoming.map(a => `
    <div class="list-item incoming-item ${a.eta_min < 0 ? 'is-late' : ''}"><span class="pos amb-ico">${icon('ambulance')}</span>
      <div><div class="line1"><span class="token">${a.code}</span>${esiBadge(a.esi)}<span>${escapeHtml(a.complaint)} · ${a.age} ${SEX[a.sex] ?? ''}</span></div>
        <div class="line2">${a.room ? `<b>${escapeHtml(a.room)}</b> is held · ${escapeHtml(a.doctor)}` : a.esi <= 2 ? 'No room held' : 'No room held: joins the queue on arrival'}</div></div>
      <div class="right"><span class="eta">${etaWords(a.eta_min)}</span>
        <div class="row-actions">${postButton(`/prealerts/${a.id}/arrived`, 'Arrived', 'btn btn-sm btn-primary', { aria: `Ambulance ${a.code} has arrived`, fields: { back } })}
          <span class="gap" aria-hidden="true"></span>${postButton(`/prealerts/${a.id}/cancel`, 'Cancel', 'btn btn-sm btn-destructive', { confirm: `Cancel pre-alert ${a.code}? Any held room is freed.`, aria: `Cancel pre-alert ${a.code}`, fields: { back } })}</div></div>
    </div>`).join('') || '<div class="empty"><strong>No ambulances on the way</strong><p>When a crew calls ahead, enter their vitals below and send a pre-alert.</p></div>');
}

// rows that moved up since the last refresh (aging, a re-triage) glow brass for a moment
const lastPositions = new Map();
function flagMovedRows(rows) {
  rows.forEach(row => {
    const before = lastPositions.get(row.dataset.token);
    const now = Number(row.dataset.pos);
    if (before && now < before) {
      row.classList.add('moved');
      setTimeout(() => row.classList.remove('moved'), 1600);
    }
    lastPositions.set(row.dataset.token, now);
  });
}

async function refreshQueueTable() {
  const s = await getJSON('/api/state');
  updateNav(s);
  renderDoors($('#doors'), s.doctors);
  renderIncoming(s);
  setHTML($('#queue-sub'), s.aging_mode === 'adaptive'
    ? `${s.queue.length} waiting · risk-adaptive aging: every ${s.aging} min or sooner for patients the AI thinks may be an emergency`
    : `${s.queue.length} waiting · fixed aging: ESI 3–5 move up a level every ${s.aging} min, up to level 3`);
  if (!s.queue.length) {
    setHTML($('#queue-table'), '<div class="empty"><strong>Nobody is waiting</strong><p>Patients appear here, in calling order, as soon as you issue a token.</p></div>');
    return;
  }
  const scale = waitScale(s.queue.map(q => Math.max(q.waited, ESI_TARGET[q.esi])));
  const changed = renderSliding($('#queue-table'), `<table class="queue-table">
    <thead><tr><th>#</th><th>Token</th><th>Level</th><th>Patient</th><th>Waited · target</th>
      <th class="num">Est. wait</th><th>Flags</th><th><span class="sr-only">Actions</span></th></tr></thead>
    <tbody>${s.queue.map(q => {
      const flags = `${q.paused ? `<span class="tag warn">${icon('pause')}paused, goes first</span>` : ''}${q.recheck ? '<span class="tag bad">re-check vitals</span>' : ''}${q.over_target ? '<span class="tag warn">over target</span>' : ''}`;
      return `<tr data-token="${q.token}" data-pos="${q.position}" class="${q.over_target ? 'is-late' : ''}">
      <td class="c-pos">${q.position}</td>
      <td class="c-token"><span class="token">${q.token}</span></td>
      <td class="c-level"><div class="tags">${esiBadge(q.esi)}${agedTag(q)}${riskTag(q)}${ambulanceTag(q)}${deltaTag(q)}</div></td>
      <td class="c-patient"><b>${escapeHtml(q.name)}</b><span>${q.age} ${SEX[q.sex] ?? ''} · ${escapeHtml(q.complaint)}</span></td>
      <td class="c-wait"><div class="wait-cell">${waitBar(q.waited, ESI_TARGET[q.esi], scale, q.over_target)}<span class="wait-num">${q.waited} min</span></div></td>
      <td class="c-eta num">${q.eta_range ? `${q.eta_range[0]}–${q.eta_range[1]} min` : '–'}</td>
      <td class="c-flags">${flags ? `<div class="tags">${flags}</div>` : ''}</td>
      <td class="c-actions"><div class="row-actions"><a class="btn btn-sm" href="/triage?retriage=${q.id}" aria-label="Re-triage ${q.token}">Re-triage</a><span class="gap" aria-hidden="true"></span>
        ${postButton(`/visits/${q.id}/left`, 'Mark left', 'btn btn-sm btn-destructive', { confirm: `Mark ${q.token} as left without being seen?`, aria: `Mark ${q.token} as left without being seen` })}</div></td>
    </tr>`;
    }).join('')}</tbody></table>`, 'tr[data-token]');
  if (changed) flagMovedRows($$('#queue-table tr[data-token]'));
}

// ---------------------------------------------------------------- doctor console
const VITAL_RANGES = { heart_rate: [60, 100], systolic_bp: [100, 140], resp_rate: [12, 20], spo2: [95, 100], temperature: [36.1, 37.9], pain: [0, 3] };
function vitalCell(label, value, unit, key, raw) {
  const r = VITAL_RANGES[key];
  // lab-report style: an abnormal value carries an H or L flag, not just a colour
  const flag = r ? (raw > r[1] ? 'H' : raw < r[0] ? 'L' : '') : '';
  return `<div class="${flag ? 'hi' : ''}"><dt>${label}</dt><dd>${value}${unit ? ` <span class="unit">${unit}</span>` : ''}${flag ? ` <span class="flag" title="${flag === 'H' ? 'Above' : 'Below'} the normal range">${flag}</span>` : ''}</dd></div>`;
}

function initDoctor() {
  const me = Number(document.body.dataset.me);
  let alarmKey = '';
  const showAlarm = (banner, key, calm) => {
    banner.classList.remove('hidden');
    banner.classList.toggle('calm', calm);
    if (key !== alarmKey) replay(banner, 'alarm-enter'); // drops in (and pulses when red) once per new alarm
    alarmKey = key;
  };
  poll(async () => {
    const s = await getJSON('/api/state');
    updateNav(s);
    const patient = s.my_patient;
    const mine = s.doctors.find(d => d.id === me);

    // ESI 1 preemption banner
    const critical = s.queue.find(q => q.esi === 1);
    const banner = $('#preempt-banner');
    if (critical && s.preempt_doctor === me && patient) {
      showAlarm(banner, `${critical.token}:preempt`, false);
      setHTML(banner, `${icon('alert', '')}<div style="flex:1;min-width:240px"><strong>Resuscitation patient waiting · ${critical.token}</strong>
        <p>Every doctor is busy and your patient (ESI ${patient.esi}) is the least urgent one being seen. Their time so far is kept and they go back to the front of their level.</p></div>
        ${postButton('/doctor/preempt', `Pause and see ${critical.token}`, 'btn btn-lg')}`);
    } else if (critical && !patient) {
      showAlarm(banner, `${critical.token}:free`, true);
      setHTML(banner, `${icon('alert', '')}<div style="flex:1"><strong>ESI 1 patient waiting · ${critical.token}</strong><p>You are free, so they will be your next patient. Press “Call next patient”.</p></div>`);
    } else {
      banner.classList.add('hidden');
      alarmKey = '';
    }

    // a room held for an incoming ambulance
    const hold = $('#hold-banner');
    if (hold && mine && mine.held && banner.classList.contains('hidden')) {
      hold.classList.remove('hidden');
      setHTML(hold, `${icon('ambulance', '')}<div style="flex:1"><strong>Room held · ambulance ${mine.held.code} (ESI ${mine.held.esi}) ${etaWords(mine.held.eta_min)}</strong>
        <p>${patient ? 'When you finish, keep your room free for it.' : '“Call next patient” will only give you someone whose consultation should end before it arrives.'}</p></div>`);
    } else if (hold) {
      hold.classList.add('hidden');
    }

    const card = $('#current-card');
    if (patient) {
      const late = patient.done > patient.predicted_minutes;
      setHTML(card, `
        <div class="panel-head"><h2>Current patient</h2><span class="token lg">${patient.token}</span></div>
        <div class="patient-head">
          <div class="level-plate sm bg-esi-${patient.esi}" aria-label="ESI ${patient.esi}"><small>ESI</small><b>${patient.esi}</b></div>
          <div><h3>${escapeHtml(patient.name)}</h3><p>${patient.age} yrs · ${patient.sex} · ${escapeHtml(patient.complaint_label)} · waited ${patient.waited} min</p></div>
        </div>
        <dl class="vitals">
          ${vitalCell('Heart rate', patient.heart_rate, 'bpm', 'heart_rate', patient.heart_rate)}
          ${vitalCell('Systolic BP', patient.systolic_bp, 'mmHg', 'systolic_bp', patient.systolic_bp)}
          ${vitalCell('Resp. rate', patient.resp_rate, '/min', 'resp_rate', patient.resp_rate)}
          ${vitalCell('SpO₂', patient.spo2, '%', 'spo2', patient.spo2)}
          ${vitalCell('Temperature', patient.temperature, '°C', 'temperature', patient.temperature)}
          ${vitalCell('Pain', patient.pain, '/10', 'pain', patient.pain)}
          ${vitalCell('AVPU', escapeHtml(patient.consciousness_label), '', '', 0)}
          ${vitalCell('AI level', `ESI ${patient.ai_esi}`, '', '', 0)}
        </dl>
        ${patient.override_reason ? `<div class="callout" style="margin-top:12px">${icon('info')}<div><strong>The nurse changed the AI level from ${patient.ai_esi} to ${patient.esi}</strong><p>${escapeHtml(patient.override_reason)}</p></div></div>` : ''}
        ${patient.notes ? `<p style="margin-top:12px;font-size:14px"><b>Notes:</b> ${escapeHtml(patient.notes)}</p>` : ''}
        <div class="consult">
          <div class="consult-top"><span><b>${patient.done} min</b> so far</span><span>AI expected about <b>${patient.predicted_minutes} min</b></span></div>
          ${waitBar(patient.done, patient.predicted_minutes, Math.max(patient.predicted_minutes * 1.5, patient.done), late).replace('Waited', 'Consultation').replace('target', 'expected')}
        </div>
        <div class="row" style="margin-top:18px">
          ${postButton('/doctor/discharge', 'Discharge', 'btn btn-primary btn-lg')}
          ${postButton('/doctor/admit', `Admit to observation · ${s.semaphore} bed${s.semaphore === 1 ? '' : 's'} free`, 'btn btn-lg')}
        </div>`);
    } else {
      const onDuty = mine && mine.on_duty;
      setHTML(card, `
        <div class="panel-head"><h2>Current patient</h2></div>
        <div class="empty-free"><strong>${onDuty ? 'You are free' : 'You are off duty'}</strong>
          <p>${onDuty
            ? (s.queue.length ? `${s.queue.length} patient${s.queue.length === 1 ? ' is' : 's are'} waiting. The dispatcher picks the most urgent one for you.` : 'Nobody is waiting right now.')
            : 'Start your shift to receive patients.'}</p>
          ${onDuty && s.queue.length ? postButton('/doctor/call-next', 'Call next patient', 'btn btn-primary btn-lg') : ''}
        </div>`);
    }

    const scale = waitScale(s.queue.slice(0, 6).map(q => Math.max(q.waited, ESI_TARGET[q.esi])));
    renderSliding($('#up-next'), s.queue.slice(0, 6).map(q => `
      <div class="list-item ${q.over_target ? 'is-late' : ''}" data-token="${q.token}"><span class="pos">#${q.position}</span>
        <div><div class="line1"><span class="token">${q.token}</span>${esiBadge(q.esi)}${agedTag(q)}<span>${escapeHtml(q.name)}</span></div>
          <div class="line2">${escapeHtml(q.complaint)} · about ${q.predicted} min${q.paused ? ' · paused earlier' : ''}</div>
          ${waitBar(q.waited, ESI_TARGET[q.esi], scale, q.over_target)}</div>
        <div class="right">${q.waited} min<small>waiting</small></div></div>`).join('')
      || '<div class="empty"><strong>Nobody is waiting</strong><p>New patients appear here as soon as the triage desk issues a token.</p></div>', '.list-item[data-token]');

    renderDoors($('#doors'), s.doctors, me);
    renderLog(s.events);
  });
}

// nothing disappears from the log: each event is stamped with what happened
const STAMPS = {
  triaged: ['Triaged', 'green'], called: ['Called', ''], discharged: ['Discharged', 'green'], paused: ['Paused', 'amber'],
  retriaged: ['Re-triaged', 'blue'], bed: ['Bed', 'blue'], boarding: ['Boarding', 'amber'], left: ['Left', 'red'],
};
function renderLog(events) {
  const el = $('#event-log');
  if (!el) return;
  setHTML(el, events.map(e => {
    const [label, cls] = STAMPS[e.kind] || [e.kind, ''];
    return `<div class="log-item"><span class="log-time">${e.at}</span><span><span class="stamp ${cls}">${escapeHtml(label)}</span></span>
      <span>${e.token ? `<b>${e.token}</b> ` : ''}${escapeHtml(e.detail || e.kind)}${e.who ? ` <span class="who">· ${escapeHtml(e.who)}</span>` : ''}</span></div>`;
  }).join('') || '<div class="empty">No activity yet.</div>');
}

// ---------------------------------------------------------------- bed board
function initBeds() {
  const STATE = { free: [0, '', 'Ready for the next patient'], occupied: [1, 'busy', ''], cleaning: [2, 'clean', 'With housekeeping'] };
  poll(async () => {
    const s = await getJSON('/api/state');
    updateNav(s);
    const count = st => s.beds.filter(b => b.status === st).length;
    setHTML($('#bed-stats'), `
      <div class="sem"><dt>Free beds</dt><dd>${s.semaphore}<small>/ ${s.beds.length}</small></dd><p>the semaphore's value</p></div>
      <div><dt>Occupied</dt><dd>${count('occupied')}</dd><p>patients in observation</p></div>
      <div><dt>Being cleaned</dt><dd>${count('cleaning')}</dd><p>“Mark clean” = signal()</p></div>
      <div class="${s.boarding.length ? 'alert' : ''}"><dt>Boarding</dt><dd>${s.boarding.length}</dd><p>blocked, waiting for a bed</p></div>`);

    setHTML($('#bed-grid'), s.beds.map(b => {
      const [pos, cls, text] = STATE[b.status];
      return `<article class="bed ${b.status}" aria-label="${b.label}: ${b.status}">
        <div class="bed-top"><b>${b.label}</b></div>
        <span class="slider ${cls}" data-pos="${pos}" aria-hidden="true"><span class="slider-thumb"></span><span>Free</span><span>In use</span><span>Clean</span></span>
        <div class="bed-body">${b.status === 'occupied' ? `<div class="tags"><span class="token" style="font-size:20px">${b.token}</span>${esiBadge(b.esi)}</div>${escapeHtml(b.name)}` : text}</div>
        ${b.status === 'occupied' ? postButton(`/beds/${b.id}/release`, 'Release bed', 'btn btn-sm', { aria: `Release ${b.label} (${b.token} leaves the ward)` }) : ''}
        ${b.status === 'cleaning' ? postButton(`/beds/${b.id}/clean`, 'Mark clean', 'btn btn-sm btn-primary', { aria: `Mark ${b.label} as clean` }) : ''}
      </article>`;
    }).join(''));

    $('#boarding-count').textContent = s.boarding.length;
    setHTML($('#boarding'), s.boarding.map((b, i) => `
      <div class="list-item"><span class="pos">#${i + 1}</span><div><div class="line1"><span class="token">${b.token}</span>${esiBadge(b.esi)}<span>${escapeHtml(b.name)}</span></div></div>
        <div class="right">${b.since} min<small>boarding</small></div></div>`).join('')
      || '<div class="empty"><strong>Nobody is waiting for a bed</strong><p>When a doctor admits a patient and no bed is free, they wait here.</p></div>');
  });
}

// ---------------------------------------------------------------- waiting room display
function initBoard() {
  let calledBefore = null;
  poll(async () => {
    const b = await getJSON('/api/board');
    const redrawn = setHTML($('#calling'), b.calling.map(c => `<div class="calling" data-token="${c.token}"><span class="door-room">Room<em>${escapeHtml(roomNo(c.room))}</em></span><b>${c.token}</b></div>`).join('')
      || '<p class="board-empty">No one is with a doctor right now.</p>');
    // a token that has just been called grows in and glows brass twice, so its owner notices from across the room
    if (redrawn && calledBefore) {
      $$('#calling .calling').forEach(el => { if (!calledBefore.has(el.dataset.token)) el.classList.add('just-called'); });
    }
    calledBefore = new Set(b.calling.map(c => c.token));
    const strip = $('#incoming-strip');
    strip.classList.toggle('hidden', !b.incoming.length);
    setHTML(strip, b.incoming.map(a => `<p>${icon('ambulance')}<span>Ambulance arriving ${a.eta_min > 0 ? `in about ${a.eta_min} min` : 'now'}${a.room ? ` · ${escapeHtml(a.room)} is being kept free for it` : ''}</span></p>`).join(''));
    $('#waiting-count').textContent = `(${b.waiting.length})`;
    setHTML($('#waiting'), b.waiting.map(w => `
      <div class="waiting-tile ${w.urgent ? 'urgent' : ''}"><b>${w.token}</b>
        <span>${w.urgent ? 'Next' : w.eta_range ? `about ${w.eta_range[0]}–${w.eta_range[1]} min` : 'Please wait'}</span></div>`).join('')
      || '<p class="board-empty">Nobody is waiting.</p>');
  }, 5000);
}

// ---------------------------------------------------------------- the patient's own page
const ordinal = n => `${n}${['th', 'st', 'nd', 'rd'][(n % 100 > 10 && n % 100 < 14) || n % 10 > 3 ? 0 : n % 10]}`;
function initWait() {
  const code = document.body.dataset.code;
  if (!code) return;
  const DONE = {
    discharged: ['Your visit is complete', 'Take care. If you were given medicines or advice, follow it, and come back if you feel worse.'],
    admitted: ['You have been admitted', 'You are in an observation bed. The ward team will look after you from here.'],
    boarding: ['You are being admitted', 'The doctor has admitted you. You are waiting for an observation bed to be free.'],
    left: ['This token is closed', 'If you still need care, please speak to the nurse at the desk.'],
  };
  poll(async () => {
    const w = await getJSON(`/api/wait/${encodeURIComponent(code)}`);
    const card = $('#wait-card');
    const why = $('#wait-why-panel');
    if (w.status === 'waiting') {
      const aging = w.next_step_in ? `<p class="wait-aging">${icon('up')}Your priority goes up in about ${w.next_step_in} min (every ${w.aging_every} min while you wait).</p>` : '';
      setHTML(card, `<p class="wait-token"><span class="token lg">${w.token}</span><span class="muted">arrived ${w.arrived}</span></p>
        <h1 class="wait-h1">You are <b>${ordinal(w.position)}</b> in the queue</h1>
        <p class="wait-eta">${w.eta_range ? `Expected wait about <b>${w.eta_range[0]}–${w.eta_range[1]} min</b>` : 'A doctor will call your token'} · waiting ${w.waited} min</p>${aging}`);
      why.classList.remove('hidden');
      setHTML($('#why'), w.reasons.map(r => `<li>${escapeHtml(r)}</li>`).join(''));
    } else if (w.status === 'in_consult') {
      setHTML(card, `<p class="wait-token"><span class="token lg">${w.token}</span></p><h1 class="wait-h1">Please go to <b>${escapeHtml(w.room || 'the consultation room')}</b></h1><p class="wait-eta">A doctor is ready to see you now.</p>`);
      why.classList.add('hidden');
    } else {
      const [title, text] = DONE[w.status] || ['Thank you', ''];
      setHTML(card, `<p class="wait-token"><span class="token lg">${w.token}</span></p><h1 class="wait-h1">${title}</h1><p class="wait-eta">${text}</p>`);
      why.classList.add('hidden');
    }
  }, 10000);
}

// ---------------------------------------------------------------- admin reports
function initAdmin() {
  poll(async () => {
    const s = await getJSON('/api/state');
    updateNav(s);
    const st = s.stats;
    setHTML($('#admin-stats'), `<span class="lead">Last 12 hours</span>
      <span><b>${st.waiting}</b>waiting (${st.in_consult} with a doctor)</span>
      <span><b>${st.on_duty} of 4</b>doctors on duty</span>
      <span><b>${st.seen_today}</b>seen · ${st.left} left unseen</span>
      <span><b>${st.avg_wait} min</b>average wait</span>
      <span><b>${st.critical_wait} min</b>ESI 1–2 wait (target 10)</span>
      <span><b>${st.overrides}</b>nurse override${st.overrides === 1 ? '' : 's'}</span>`);
  }, 10000);

  getJSON('/api/admin/reports').then(r => {
    $('#chart-wait-esi').innerHTML = Charts.bars({
      categories: r.by_esi.map(x => `ESI ${x.esi}`),
      series: [{ name: 'Average wait (min)', color: 'var(--dado)', values: r.by_esi.map(x => x.wait || 0) }],
      width: 520, height: 230,
    });
    // each level in its own clinical colour
    $$('#chart-wait-esi rect').forEach((rect, i) => { rect.style.fill = `var(--esi-${r.by_esi[i]?.esi || i + 1})`; });
    $('#chart-hourly').innerHTML = Charts.bars({
      categories: r.hourly.map(x => String(x.hour).padStart(2, '0')),
      series: [{ name: 'Arrivals', color: 'var(--enamel)', values: r.hourly.map(x => x.n) }],
      width: 520, height: 230, decimals: 0, emptyText: 'No arrivals in the last 12 hours',
    });

    // AI vs nurse agreement matrix
    const grid = [...Array(5)].map(() => new Array(5).fill(0));
    r.agreement.forEach(a => { grid[a.ai_esi - 1][a.esi - 1] += a.n; });
    const total = grid.flat().reduce((a, b) => a + b, 0);
    const agree = grid.reduce((s, row, i) => s + row[i], 0);
    const shade = (n, same) => {
      if (!n) return 'background:transparent';
      const a = 0.18 + Math.min(0.72, n / 12);
      return `background:${same ? `rgba(36,89,74,${a})` : `rgba(200,16,46,${a})`};color:${a > 0.5 ? '#fff' : 'inherit'}`;
    };
    $('#agreement').innerHTML = `<table class="confusion"><caption class="sr-only">Rows: AI suggestion. Columns: nurse's final level.</caption>
      <thead><tr><th scope="col">AI ↓ · nurse →</th>${[1, 2, 3, 4, 5].map(l => `<th scope="col">ESI ${l}</th>`).join('')}</tr></thead>
      <tbody>${grid.map((row, i) => `<tr><th scope="row">ESI ${i + 1}</th>${row.map((n, j) => `<td class="cell" style="${shade(n, i === j)}">${n || ''}</td>`).join('')}</tr>`).join('')}</tbody></table>
      <p class="axis-caption">Nurses kept the AI suggestion in ${agree} of ${total} visits (${pct(agree / Math.max(1, total))}).</p>`;

    if (r.metrics) {
      const imp = Object.entries(r.metrics.importance)
        .reduce((acc, [k, v]) => { const key = k.startsWith('complaint_') ? 'complaint' : k; acc[key] = (acc[key] || 0) + v; return acc; }, {});
      const labels = { age: 'Age', heart_rate: 'Heart rate', systolic_bp: 'Systolic BP', resp_rate: 'Resp. rate', spo2: 'SpO₂',
        temperature: 'Temperature', consciousness: 'Consciousness', pain: 'Pain score', complaint: 'Complaint' };
      $('#chart-importance').innerHTML = Charts.hbars(Object.entries(imp).sort((a, b) => b[1] - a[1])
        .map(([k, v]) => ({ label: labels[k], value: v, display: pct(v), color: 'var(--enamel)' })));
    }

    if (r.simulation) {
      const pol = Object.values(r.simulation.policies);
      const names = pol.map(p => p.label.replace(' (token order)', '').replace(' (triage only)', '').replace('Shortest predicted first', 'SJF')
        .replace('MediQueue, fixed aging', 'Fixed aging').replace('MediQueue, risk-adaptive (ours)', 'Adaptive'));
      const colors = pol.map(p => (p.label.includes('ours') ? 'var(--dado)' : 'var(--line-strong)'));
      const small = (values, decimals = 0) => Charts.bars({
        categories: names, series: [{ name: '', color: 'var(--dado)', values }], width: 340, height: 230, decimals,
      });
      $('#chart-critical').innerHTML = small(pol.map(p => p.critical_wait), 1);
      $('#chart-tail').innerHTML = small(pol.map(p => p.low_priority_p95));
      // our policy in dado green, the baselines grey
      ['#chart-critical', '#chart-tail'].forEach(sel => $$(`${sel} rect`).forEach((rect, i) => { rect.style.fill = colors[i]; }));

      const sweep = r.simulation.aging_sweep;
      const cats = sweep.map(x => x.aging ? `${x.aging}` : 'off');
      $('#chart-aging-tail').innerHTML = Charts.bars({ categories: cats, series: [{ name: '', color: 'var(--enamel)', values: sweep.map(x => x.low_p95) }], width: 240, height: 200, decimals: 0 });
      $('#chart-aging-esi3').innerHTML = Charts.bars({ categories: cats, series: [{ name: '', color: 'var(--esi-3)', values: sweep.map(x => x.esi3_target * 100) }], width: 240, height: 200, decimals: 0 });
      // the interval we actually use stays solid, the others fade back
      ['#chart-aging-tail', '#chart-aging-esi3'].forEach(sel => $$(`${sel} rect`).forEach((rect, i) => { if (cats[i] !== '30') rect.style.opacity = 0.35; }));

      const rs = r.simulation.risk_sweep;
      if (rs && $('#chart-risk-hidden')) {
        const th = rs.map(x => (x.threshold === null ? 'never' : `${Math.round(x.threshold * 100)}%`));
        $('#chart-risk-hidden').innerHTML = Charts.bars({ categories: th, series: [{ name: '', color: 'var(--dado)', values: rs.map(x => x.hidden_wait) }], width: 260, height: 200, decimals: 0 });
        $('#chart-risk-critical').innerHTML = Charts.bars({ categories: th, series: [{ name: '', color: 'var(--esi-2)', values: rs.map(x => x.critical_wait) }], width: 260, height: 200, decimals: 1 });
        // the threshold we use stays solid
        ['#chart-risk-hidden', '#chart-risk-critical'].forEach(sel => $$(`${sel} rect`).forEach((rect, i) => { if (th[i] !== '30%') rect.style.opacity = 0.35; }));
      }
      const pa = r.simulation.prealert;
      if (pa && $('#chart-prealert')) {
        $('#chart-prealert').innerHTML = Charts.bars({
          categories: ['Ambulance door-to-doctor', 'Slowest 10% of ambulances', 'Everyone else'],
          series: [{ name: 'No pre-alert', color: 'var(--line-strong)', values: [pa.off.ambulance_wait, pa.off.ambulance_p90, pa.off.others_wait] },
                   { name: 'Room held', color: 'var(--enamel)', values: [pa.on.ambulance_wait, pa.on.ambulance_p90, pa.on.others_wait] }],
          width: 520, height: 220, unit: '', decimals: 1,
        });
      }
    }
  });
}

// ---------------------------------------------------------------- router
({ login: initLogin, triage: initTriage, prealert: initTriage, doctor: initDoctor, beds: initBeds, board: initBoard, admin: initAdmin, wait: initWait })[document.body.dataset.page]?.();
