const $ = id => document.getElementById(id);
let hours = 24;
let viewEnd = null;
let serverOffset = 0;
const chartNow = () => Date.now()/1000 + serverOffset;
let panTimer;
let drag = null;
let snapshot = null;
let sequence = 0;
let savingInterval = false;
const format = (value, digits = 1) => value == null ? '—' : Number(value).toFixed(digits);
let metric = 'temperature';
let chartPoints = [];
let selectedPoint = -1;
let chartScale = null;
const metrics = {
  temperature: {title: 'История температуры', label: 'ТЕМПЕРАТУРА', unit: '°C', digits: 2, scale: 1, pad: .5},
  humidity: {title: 'История влажности', label: 'ВЛАЖНОСТЬ', unit: '%', digits: 0, scale: 1, pad: 1},
  voltage: {title: 'История напряжения', label: 'НАПРЯЖЕНИЕ', unit: 'V', digits: 3, scale: .001, pad: .005},
};
function hideTooltip() {
  $('chart-tooltip').hidden = true;
  $('chart-marker')?.setAttribute('visibility', 'hidden');
}
function showPoint(index) {
  const p = chartPoints[index];
  if (!p) return;
  selectedPoint = index;
  const config = metrics[metric];
  const tooltip = $('chart-tooltip');
  tooltip.textContent = `${format(p.value, config.digits)} ${config.unit} · ${new Date(p.ts * 1000).toLocaleString('ru-RU')}`;
  tooltip.hidden = false;
  const marker = $('chart-marker');
  marker.setAttribute('cx', p.x);
  marker.setAttribute('cy', p.y);
  marker.setAttribute('visibility', 'visible');
  const width = $('chart').getBoundingClientRect().width;
  tooltip.style.left = Math.max(0, Math.min(p.x - tooltip.offsetWidth / 2, width - tooltip.offsetWidth)) + 'px';
  tooltip.style.top = Math.max(0, p.y - tooltip.offsetHeight - 14) + 'px';
}
function draw(data) {
  const config = metrics[metric];
  const end = viewEnd ?? data.window?.end ?? chartNow();
  const start = end - hours * 3600;
  const available = (data.plot_history || data.history).filter(p => Number.isFinite(p[metric]));
  const visible = available.filter(p => p.ts >= start && p.ts <= end);
  const before = available.findLast(p => p.ts < start);
  const after = available.find(p => p.ts > end);
  const history = [...(before ? [before] : []), ...visible, ...(after ? [after] : [])];
  $('history-window').textContent = [start, end].map(t => new Date(t*1000).toLocaleString('ru-RU', {day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'})).join(' — ');
  $('history-live').disabled = viewEnd === null;
  $('history-forward').disabled = viewEnd === null;
  $('history-back').disabled = data.earliest == null || end <= data.earliest;
  $('empty').textContent = data.earliest == null ? 'Ожидаем первые показания' : 'В этом периоде нет измерений';
  $('empty').hidden = visible.length > 0 || Boolean(before && after);
  $('chart-title').textContent = config.title + ' / ' + config.unit;
  $('chart').setAttribute('aria-label', config.title + '. Стрелки влево и вправо выбирают точку');
  hideTooltip();
  selectedPoint = -1;
  const rect = $('chart').getBoundingClientRect();
  const width = Math.max(200, rect.width), height = rect.height;
  $('chart').setAttribute('viewBox', `0 0 ${width} ${height}`);
  const left = metric === 'voltage' ? 49 : 42, right = width - 12, top = 20, bottom = height - 38;
  const values = history.map(p => p[metric] * config.scale);
  const min = values.length ? Math.min(...values) : 0;
  const max = values.length ? Math.max(...values) : 1;
  const padding = Math.max(config.pad, (max - min) * .15);
  if (!chartScale || chartScale.key !== metric + hours) {
    chartScale = {key:metric + hours, low:min-padding, high:max+padding};
  } else if (!drag && !panTimer && values.length) {
    // Keep the axis stable when endpoints leave the viewport.
    chartScale.low = Math.min(chartScale.low, min-padding);
    chartScale.high = Math.max(chartScale.high, max+padding);
  }
  const {low, high} = chartScale;
  const x = t => left + (t - start) / (end - start) * (right - left);
  const y = t => top + (high - t) / (high - low) * (bottom - top);
  let svg = '<defs><linearGradient id="fade" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#c9e5ed" stop-opacity=".13"/><stop offset="100%" stop-color="#c9e5ed" stop-opacity="0"/></linearGradient></defs>';
  svg += `<defs><clipPath id="plot-clip"><rect x="${left}" y="${top}" width="${right-left}" height="${bottom-top}"/></clipPath></defs>`;
  for (let i = 0; i < 5; i++) {
    const yy = top + i * (bottom - top) / 4;
    svg += `<line x1="${left}" y1="${yy}" x2="${right}" y2="${yy}" stroke="#252d31" stroke-dasharray="3 5"/><text x="0" y="${yy + 4}" fill="#78868d" font-family="monospace" font-size="11">${format(high - i * (high-low)/4, metric === 'voltage' ? 3 : 1)}</text>`;
  }
  const ticks = width < 450 ? 3 : 5;
  for (let i = 0; i < ticks; i++) {
    const t = start + (end-start)*i/(ticks-1);
    const label = new Date(t*1000).toLocaleString('ru-RU', hours === 168 ? {day:'2-digit',month:'2-digit'} : {hour:'2-digit',minute:'2-digit'});
    svg += `<text x="${x(t)}" y="${height-10}" text-anchor="${i === 0 ? 'start' : i === ticks-1 ? 'end' : 'middle'}" fill="#78868d" font-size="11" font-family="monospace">${label}</text>`;
  }
  chartPoints = history.map(p => ({ts:p.ts, value:p[metric]*config.scale, x:x(p.ts), y:y(p[metric]*config.scale)}));
  if (chartPoints.length) {
    const points = chartPoints.map(p => `${p.x},${p.y}`).join(' ');
    svg += `<g clip-path="url(#plot-clip)"><polygon points="${chartPoints[0].x},${bottom} ${points} ${chartPoints.at(-1).x},${bottom}" fill="url(#fade)"/><polyline points="${points}" fill="none" stroke="#e3edf0" stroke-width="2" stroke-linejoin="round"/>`;
    for (const p of chartPoints) svg += `<circle cx="${p.x}" cy="${p.y}" r="3" fill="#dcecf1"/>`;
    svg += '<circle id="chart-marker" r="5" fill="#090b0c" stroke="#b8f3ce" stroke-width="2" visibility="hidden"/></g>';
  }
  chartPoints = chartPoints.filter(p => p.ts >= start && p.ts <= end);
  $('chart').innerHTML = svg;
  const stats = data.metric_stats?.[metric] || (metric === 'temperature' ? data.stats : {});
  ['min', 'avg', 'max'].forEach(key => $(key).textContent = stats[key] == null ? '—' : format(stats[key]*config.scale, key === 'avg' && metric === 'humidity' ? 1 : config.digits) + ' ' + config.unit);
}
$('chart').addEventListener('pointermove', event => {
  if (drag && event.pointerId === drag.id) {
    const dx = event.clientX - drag.x;
    if (Math.abs(dx) > 5 || drag.moved) {
      drag.moved = true;
      panTo(drag.end - dx / Math.max(1, $('chart').clientWidth-54) * hours*3600);
      return;
    }
  }
  if (!chartPoints.length) return;
  const x = event.clientX - $('chart').getBoundingClientRect().left;
  let index = 0;
  chartPoints.forEach((p, i) => { if (Math.abs(p.x-x) < Math.abs(chartPoints[index].x-x)) index = i; });
  showPoint(index);
});
$('chart').addEventListener('pointerdown', event => {
  if (event.button !== 0) return;
  drag = {id:event.pointerId, x:event.clientX, end:viewEnd ?? chartNow(), moved:false};
  $('chart').setPointerCapture(event.pointerId);
  if (!chartPoints.length) return;
  const x = event.clientX - $('chart').getBoundingClientRect().left;
  let index = 0;
  chartPoints.forEach((p, i) => { if (Math.abs(p.x-x) < Math.abs(chartPoints[index].x-x)) index = i; });
  showPoint(index);
});
$('chart').addEventListener('pointerleave', hideTooltip);
$('chart').addEventListener('blur', hideTooltip);
$('chart').addEventListener('keydown', event => {
  if (event.key === 'Escape') return hideTooltip();
  if (!['ArrowLeft', 'ArrowRight'].includes(event.key) || !chartPoints.length) return;
  event.preventDefault();
  showPoint(Math.max(0, Math.min(chartPoints.length-1, selectedPoint + (event.key === 'ArrowRight' ? 1 : -1))));
});
document.querySelectorAll('[data-metric]').forEach(button => button.addEventListener('click', () => {
  metric = button.dataset.metric;
  chartScale = null;
  document.querySelectorAll('[data-metric]').forEach(b => b.setAttribute('aria-pressed', String(b === button)));
  draw(snapshot || {history:[], stats:{}});
}));
new ResizeObserver(() => draw(snapshot || {history:[], stats:{}})).observe($('chart'));
function render(data) {
  if (Number.isFinite(data.server_time)) serverOffset = data.server_time - Date.now()/1000;
  snapshot = data;
  const p = data.latest;
  const fresh = p && chartNow() - p.ts < Math.max(120, (data.interval || 0) + 60);
  if (!savingInterval) $('sampling-interval').value = String(data.interval || 0);
  $('status').textContent = data.interval === -1 ? 'СБОР ПРИОСТАНОВЛЕН' : data.sleeping && fresh ? 'ПАУЗА МЕЖДУ ЗАМЕРАМИ' : data.connected && fresh ? 'В ЭФИРЕ' : data.connected ? 'ОЖИДАНИЕ ДАННЫХ' : 'НЕТ СОЕДИНЕНИЯ';
  $('dot').classList.toggle('live', Boolean(data.connected && fresh));
  $('temperature').textContent = format(p?.temperature);
  $('humidity').textContent = format(p?.humidity, 0);
  $('battery').textContent = p ? '≈' + p.battery : '—';
  $('voltage').textContent = p ? format(p.voltage/1000, 3) + ' V' : '— V';
  $('temp-note').textContent = data.interval === -1 ? 'Сбор приостановлен' : p ? fresh ? 'Последнее измерение' : 'Данные устарели' : 'Ожидание данных';
  $('mac').textContent = data.mac;
  $('message').textContent = data.message + (data.sleeping && data.next_reading ? ' · следующий: ' + new Date(data.next_reading*1000).toLocaleTimeString('ru-RU') : '');
  $('updated').textContent = 'Последнее измерение: ' + (p ? new Date(p.ts*1000).toLocaleString('ru-RU') : '—');
  draw(data);
}
async function refresh() {
  const request = ++sequence;
  try {
    const response = await fetch('/api/readings?hours=' + hours + (viewEnd === null ? '' : '&end=' + viewEnd), {signal: AbortSignal.timeout(8000)});
    if (!response.ok) throw new Error('HTTP ' + response.status);
    const data = await response.json();
    if (request === sequence) render(data);
  } catch {
    if (request !== sequence) return;
    $('status').textContent = 'СЕРВЕР НЕДОСТУПЕН';
    $('dot').classList.remove('live');
    $('message').textContent = 'Нет связи с локальным сервером. Повторяем запрос…';
    if (snapshot?.latest) $('temp-note').textContent = 'Нет обновления данных';
  }
}
document.querySelectorAll('[data-hours]').forEach(button => button.addEventListener('click', () => {
  hours = Number(button.dataset.hours);
  chartScale = null;
  document.querySelectorAll('[data-hours]').forEach(b => b.setAttribute('aria-pressed', String(b === button)));
  refresh();
}));
function clock() { $('clock').textContent = new Date().toLocaleTimeString('ru-RU'); }
clock(); setInterval(clock, 1000); refresh(); setInterval(() => { if (!drag && !panTimer) refresh(); }, 5000);

$('sampling-interval').addEventListener('change', async () => {
  savingInterval = true;
  $('sampling-interval').disabled = true;
  $('sampling-feedback').textContent = 'Сохраняем…';
  try {
    const response = await fetch('/api/settings', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({interval:Number($('sampling-interval').value)}),
      signal:AbortSignal.timeout(8000)
    });
    if (!response.ok) throw new Error('Save failed');
    $('sampling-feedback').textContent = 'Сохранено';
  } catch {
    $('sampling-feedback').textContent = 'Не удалось сохранить. Проверьте соединение с сервером.';
    $('sampling-interval').value = String(snapshot?.interval || 0);
  } finally {
    savingInterval = false;
    $('sampling-interval').disabled = false;
    refresh();
  }
});

function panTo(end) {
  const now = chartNow();
  viewEnd = end >= now - 1 ? null : Math.max(snapshot?.earliest ?? end, end);
  ++sequence; // Discard requests for the previously visible window.
  hideTooltip();
  if (snapshot) draw(snapshot);
  clearTimeout(panTimer);
  panTimer = setTimeout(() => { panTimer = null; refresh(); }, 100);
}
$('chart').addEventListener('pointerup', () => { drag = null; });
$('chart').addEventListener('pointercancel', () => { drag = null; });
$('chart').addEventListener('lostpointercapture', () => { drag = null; });
$('chart').addEventListener('wheel', event => {
  const delta = event.deltaX || (event.shiftKey ? event.deltaY : 0);
  if (!delta) return;
  event.preventDefault();
  const pixels = delta * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? $('chart').clientWidth : 1);
  panTo((viewEnd ?? chartNow()) + pixels / Math.max(1,$('chart').clientWidth) * hours*3600);
}, {passive:false});
$('history-back').addEventListener('click', () => panTo((viewEnd ?? chartNow()) - hours*1800));
$('history-forward').addEventListener('click', () => panTo((viewEnd ?? chartNow()) + hours*1800));
$('history-live').addEventListener('click', () => panTo(chartNow()));
