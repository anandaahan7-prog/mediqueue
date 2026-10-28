/* Hand-rolled SVG/HTML charts – no chart library, so the project runs fully offline. */

const Charts = {
  niceStep(span, targetTicks = 8) {
    const steps = [1, 2, 5, 10, 15, 20, 30, 60, 120, 240];
    return steps.find(s => span / s <= targetTicks) || 480;
  },

  niceMax(value) {
    if (value <= 0) return 1;
    const magnitude = Math.pow(10, Math.floor(Math.log10(value)));
    const scaled = value / magnitude;
    const nice = scaled <= 1 ? 1 : scaled <= 2 ? 2 : scaled <= 2.5 ? 2.5 : scaled <= 5 ? 5 : 10;
    return nice * magnitude;
  },

  // One lane per doctor, one coloured block per stretch of treatment.
  gantt({ segments, lanes, patients, from = 0, to, tickLabel = t => t, now = null }) {
    const span = Math.max(1, to - from);
    const byId = patients instanceof Map ? patients : new Map(patients.map(p => [p.id, p]));

    const rows = lanes.map((label, lane) => {
      const blocks = segments
        .filter(s => s.doctor === lane && s.end > from && s.start < to)
        .map(s => {
          const start = Math.max(s.start, from);
          const end = Math.min(s.end, to);
          const p = byId.get(s.patientId) || { name: '?', esi: 3 };
          const left = ((start - from) / span) * 100;
          const width = ((end - start) / span) * 100;
          const text = width > 6 ? escapeHtml(p.name.split(' ')[0]) : '';
          return `<div class="gantt-seg bg-esi-${p.esi}" style="left:${left}%;width:calc(${width}% - 2px)"
                    title="${escapeHtml(p.name)} · ESI ${p.esi} · minute ${s.start}–${s.end}">${text}</div>`;
        })
        .join('');
      const nowLine = now !== null ? `<div class="gantt-now" style="left:${((now - from) / span) * 100}%"></div>` : '';
      return `<div class="gantt-row"><div class="gantt-label" title="${escapeHtml(label)}">${escapeHtml(label)}</div>
                <div class="gantt-track">${blocks}${nowLine}</div></div>`;
    }).join('');

    const step = this.niceStep(span);
    let ticks = '';
    for (let t = Math.ceil(from / step) * step; t <= to; t += step) {
      ticks += `<span style="left:${((t - from) / span) * 100}%">${tickLabel(t)}</span>`;
    }
    return `<div class="gantt">${rows}<div class="gantt-axis">${ticks}</div></div>`;
  },

  legend(series) {
    return `<div class="legend">${series.map(s => `<span><i style="background:${s.color}"></i>${escapeHtml(s.name)}</span>`).join('')}</div>`;
  },

  // Grouped vertical bars.
  bars({ categories, series, width = 680, height = 260, unit = '', decimals = 1, emptyText = 'No data yet' }) {
    if (!series.some(s => s.values.some(v => v > 0))) {
      return `<div class="empty"><strong>${escapeHtml(emptyText)}</strong><p>The chart fills in as patients arrive.</p></div>`;
    }
    const W = width, H = height;
    const pad = { l: 46, r: 10, t: 16, b: 44 };
    const plotW = W - pad.l - pad.r;
    const plotH = H - pad.t - pad.b;
    let top = this.niceMax(Math.max(0.0001, ...series.flatMap(s => s.values)) * 1.1);
    // counts (decimals 0) get whole-number gridlines – no "1.25 patients"
    if (decimals === 0) top = Math.max(4, Math.ceil(top / 4) * 4);
    const y = v => pad.t + plotH - (v / top) * plotH;

    let svg = '';
    for (let i = 0; i <= 4; i++) {
      const v = (top * i) / 4;
      svg += `<line class="grid" x1="${pad.l}" x2="${W - pad.r}" y1="${y(v)}" y2="${y(v)}"/>`;
      svg += `<text x="${pad.l - 8}" y="${y(v) + 4}" text-anchor="end">${fmt(v, 1)}${unit}</text>`;
    }

    const groupW = plotW / categories.length;
    const gap = 4;
    const barW = Math.max(6, Math.min(30, (groupW * 0.72 - gap * (series.length - 1)) / series.length));
    const groupInner = barW * series.length + gap * (series.length - 1);

    categories.forEach((cat, ci) => {
      const x0 = pad.l + ci * groupW + (groupW - groupInner) / 2;
      series.forEach((s, si) => {
        const v = s.values[ci] || 0;
        const x = x0 + si * (barW + gap);
        const h = Math.max(0, pad.t + plotH - y(v));
        svg += `<rect x="${x}" y="${y(v)}" width="${barW}" height="${h}" rx="4" style="fill:${s.color}">
                  <title>${escapeHtml(cat)} – ${escapeHtml(s.name)}: ${fmt(v, decimals)}${unit}</title></rect>`;
        if (barW >= 16) {
          svg += `<text class="value-label" x="${x + barW / 2}" y="${y(v) - 5}" text-anchor="middle">${fmt(v, decimals)}</text>`;
        }
      });
      // wrap long labels onto two lines
      const words = String(cat).split(' ');
      const lines = words.length > 1 && cat.length > 9 ? [words.slice(0, -1).join(' '), words[words.length - 1]] : [cat];
      lines.forEach((line, li) => {
        svg += `<text x="${pad.l + ci * groupW + groupW / 2}" y="${H - pad.b + 17 + li * 13}" text-anchor="middle">${escapeHtml(line)}</text>`;
      });
    });

    const legend = series.length > 1 ? this.legend(series) : '';
    return `<div class="chart">${legend}<svg class="chart-svg" viewBox="0 0 ${W} ${H}" role="img">${svg}</svg></div>`;
  },

  // Simple line / area chart. series: [{ name, color, points: [{x, y}] }]
  line({ series, width = 680, height = 230, xLabel = x => x, unit = '' }) {
    const W = width, H = height;
    const pad = { l: 40, r: 12, t: 14, b: 30 };
    const plotW = W - pad.l - pad.r;
    const plotH = H - pad.t - pad.b;
    const xs = series.flatMap(s => s.points.map(p => p.x));
    const minX = Math.min(...xs), maxX = Math.max(...xs);
    const top = this.niceMax(Math.max(1, ...series.flatMap(s => s.points.map(p => p.y))) * 1.1);
    const sx = x => pad.l + ((x - minX) / Math.max(1, maxX - minX)) * plotW;
    const sy = v => pad.t + plotH - (v / top) * plotH;

    let svg = '';
    for (let i = 0; i <= 4; i++) {
      const v = (top * i) / 4;
      svg += `<line class="grid" x1="${pad.l}" x2="${W - pad.r}" y1="${sy(v)}" y2="${sy(v)}"/>`;
      svg += `<text x="${pad.l - 8}" y="${sy(v) + 4}" text-anchor="end">${fmt(v, 0)}${unit}</text>`;
    }
    const step = this.niceStep(maxX - minX, 7);
    for (let x = Math.ceil(minX / step) * step; x <= maxX; x += step) {
      svg += `<text x="${sx(x)}" y="${H - 8}" text-anchor="middle">${xLabel(x)}</text>`;
    }

    series.forEach((s, i) => {
      if (!s.points.length) return;
      const d = s.points.map((p, k) => `${k ? 'L' : 'M'}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join(' ');
      if (i === 0) {
        const area = `${d} L${sx(s.points[s.points.length - 1].x)},${sy(0)} L${sx(s.points[0].x)},${sy(0)} Z`;
        svg += `<path d="${area}" style="fill:${s.color};opacity:.12"/>`;
      }
      svg += `<path d="${d}" style="fill:none;stroke:${s.color};stroke-width:2.2;stroke-linejoin:round"/>`;
    });

    const legend = series.length > 1 ? this.legend(series) : '';
    return `<div class="chart">${legend}<svg class="chart-svg" viewBox="0 0 ${W} ${H}" role="img">${svg}</svg></div>`;
  },

  // Horizontal bars as plain HTML – nice for "importance"-style rankings.
  hbars(items, max = null) {
    const top = max ?? Math.max(0.0001, ...items.map(i => i.value));
    return `<div class="hbars">${items.map(i => `
      <div class="hbar">
        <span class="hbar-label" title="${escapeHtml(i.label)}">${escapeHtml(i.label)}</span>
        <div class="hbar-track"><div class="hbar-fill" style="width:${(i.value / top) * 100}%;background:${i.color || 'var(--primary)'}"></div></div>
        <span class="hbar-value">${i.display ?? fmt(i.value)}</span>
      </div>`).join('')}</div>`;
  },
};
