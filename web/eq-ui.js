import {
  TYPES, PRESETS, changeBandType, defaultBands, clampBand, limitsFor, hasGain, isPass,
  freqToX, xToFreq, dbToY, yToDb, curveDb, webAudioStages, levelMatchDb, soloParams,
  nudgeBand, describeBand, eqNoteBlock, parseEqFromNote, shaperCurve, SOFT_KNEE, SHAPER_SPAN,
} from './eq.mjs';

const IMPORT_LIMIT_BYTES = 256 * 1024;
const MESSAGE_HOLD_MS = 6000;
const PAUSED_MESSAGE = 'The browser paused audio. Press Play again.';
const DEAD_MESSAGE = 'EQ stopped working. Reload the page to restore playback.';
const UNAVAILABLE_MESSAGE = 'EQ is not available in this window. Playback works without it.';
const SLIDER_KEYS = ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 't', 'T', 'e', 'E'];
const DISCRETE_KEYS = ['t', 'T', 'e', 'E', 'Home'];


// Source -> three parallel paths (EQ chain, bypass, solo slice) -> sum -> shaper -> analyser -> destination.
// Every path ends in `sum`, so nothing reaches the destination without passing the shaper.
function buildGraph(ctx, player) {
  const filters = Array.from({ length: 14 }, () => ctx.createBiquadFilter());
  const soloFilters = [ctx.createBiquadFilter(), ctx.createBiquadFilter()];
  const eqGain = ctx.createGain();
  const bypass = ctx.createGain();
  const soloGain = ctx.createGain();
  const sum = ctx.createGain();
  const preGain = ctx.createGain();
  const shaper = ctx.createWaveShaper();
  const postGain = ctx.createGain();
  const analyser = ctx.createAnalyser();

  eqGain.gain.value = 0;
  bypass.gain.value = 0;
  soloGain.gain.value = 0;
  preGain.gain.value = 1 / SHAPER_SPAN;
  postGain.gain.value = SHAPER_SPAN;
  soloFilters.forEach(node => { node.type = 'bandpass'; });
  shaper.curve = shaperCurve();
  shaper.oversample = '2x';
  analyser.fftSize = 4096;
  analyser.smoothingTimeConstant = 0.8;
  analyser.minDecibels = -100;
  analyser.maxDecibels = -10;

  filters.forEach((node, i) => node.connect(filters[i + 1] || eqGain));
  soloFilters[0].connect(soloFilters[1]);
  soloFilters[1].connect(soloGain);
  for (const node of [eqGain, bypass, soloGain]) node.connect(sum);
  sum.connect(preGain);
  preGain.connect(shaper);
  shaper.connect(postGain);
  postGain.connect(analyser);
  analyser.connect(ctx.destination);

  // After this call the media element plays only through the graph, so the caller must treat a failure as fatal.
  const source = ctx.createMediaElementSource(player);
  source.connect(filters[0]);
  source.connect(bypass);
  source.connect(soloFilters[0]);
  return { ctx, source, filters, eqGain, bypass, soloGain, soloFilters, analyser };
}

export function initEq({ player, announce, fitCanvas }) {
  const $ = id => document.getElementById(id);
  const panel = $('eqPanel');
  let settings = freshSettings();
  let selected = 0;
  let solo = null;
  let bypassed = false;
  let graph = null;
  let unavailable = false;
  let revision = 0;
  let switchTimer = null;
  let monitor = null;
  let frame = null;
  let pending = null;
  let importRevision = 0;
  let lastTime = 0;
  let silentSince = null;
  let announcedWarning = '';
  let lastDraw = 0;
  let curveCache = null;
  let statusText = '';
  let statusHoldUntil = 0;
  const handles = [];

  function freshSettings() {
    return { bands: defaultBands(), level_match: true, trim_db: 0, preset: null };
  }

  const sr = () => graph?.ctx.sampleRate || 48000;

  // Writes only when the text changes, so a screen reader is not re-announced on every drag frame. A held message
  // (import result, audio problem) is not replaced by the routine mode text until the hold expires.
  function status(text, holdMs = 0) {
    if (holdMs) statusHoldUntil = performance.now() + holdMs;
    if (text === statusText) return;
    statusText = text;
    $('eqStatus').textContent = text;
  }

  function mode() {
    if (!graph) return 'EQ not turned on';
    if (solo !== null) return `Solo band ${solo + 1}`;
    if (bypassed) return 'EQ bypassed · output soft clipping remains active';
    return 'EQ active · output soft clipping active';
  }

  function cancelDrag() {
    pending = null;
    if (frame !== null) cancelAnimationFrame(frame);
    frame = null;
  }

  function makeupDb() {
    return (settings.level_match ? levelMatchDb(settings.bands, sr()) : 0) + settings.trim_db;
  }

  // Curve in dB plus the largest gain the EQ applies anywhere once level match and trim are included.
  function currentCurve() {
    if (!curveCache) {
      const freqs = Array.from({ length: 256 }, (_, i) => xToFreq(i / 255, sr()));
      const db = curveDb(settings.bands, freqs, sr());
      curveCache = { db, peakGainDb: Math.max(...db) + makeupDb() };
    }
    return curveCache;
  }

  function ramp(param, value, tau = 0.02) {
    const t = graph.ctx.currentTime;
    if (param.cancelAndHoldAtTime) {
      param.cancelAndHoldAtTime(t);
    } else {
      param.cancelScheduledValues(t);
      param.setValueAtTime(param.value, t);
    }
    param.setTargetAtTime(value, t, tau);
  }

  function route() {
    if (!graph) return;
    const eqActive = !bypassed && solo === null;
    ramp(graph.eqGain.gain, eqActive ? 10 ** (makeupDb() / 20) : 0, 0.015);
    ramp(graph.bypass.gain, bypassed && solo === null ? 1 : 0, 0.015);
    ramp(graph.soloGain.gain, solo !== null ? 1 : 0, 0.015);
  }

  function apply() {
    if (!graph) return;
    settings.bands.flatMap(webAudioStages).forEach((stage, i) => {
      const node = graph.filters[i];
      node.type = stage.type;
      ramp(node.frequency, stage.frequency);
      ramp(node.gain, stage.gain);
      ramp(node.Q, stage.Q);
    });
    if (solo !== null) {
      const p = soloParams(settings.bands[solo]);
      for (const node of graph.soloFilters) {
        ramp(node.frequency, p.frequency);
        ramp(node.Q, p.Q);
      }
    }
    route();
  }

  function changed(discrete = false) {
    cancelDrag();
    curveCache = null;
    revision++;
    clearTimeout(switchTimer);
    silentSince = null;
    if (graph && discrete) {
      // Mute briefly so a filter type switch does not click, then apply.
      ramp(graph.eqGain.gain, 0, 0.008);
      ramp(graph.soloGain.gain, 0, 0.008);
      const token = revision;
      switchTimer = setTimeout(() => { if (token === revision) apply(); }, 25);
    } else {
      apply();
    }
    render();
  }

  function select(i) {
    selected = i;
    if (solo !== null) solo = i;
    changed();
  }

  function edit(band, discrete = false) {
    const current = settings.bands[selected];
    const next = band.type !== current.type ? changeBandType(band, band.type) : band;
    settings.bands[selected] = clampBand(next, sr(), selected);
    settings.preset = null;
    changed(discrete);
  }

  function toggleSolo(i = selected) {
    selected = i;
    solo = solo === i ? null : i;
    bypassed = false;
    changed();
    announce(solo === null ? 'EQ active' : `Soloing band ${i + 1} around ${Math.round(settings.bands[i].frequency_hz)} hertz`);
  }

  function toggleBypass() {
    solo = null;
    bypassed = !bypassed;
    changed();
    announce(mode());
  }

  function drawGrid(ctx, w, h) {
    for (const db of [-24, -12, 0, 12, 24]) {
      const y = dbToY(db) * h;
      ctx.strokeStyle = '#304044';
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(w, y);
      ctx.stroke();
      ctx.fillStyle = '#839b94';
      ctx.fillText(`${db} dB`, 4, Math.max(10, y - 3));
    }
    for (const f of [20, 100, 1000, 10000]) {
      const x = freqToX(f, sr()) * w;
      ctx.strokeStyle = '#304044';
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, h);
      ctx.stroke();
      ctx.fillStyle = '#839b94';
      ctx.fillText(f >= 1000 ? `${f / 1000}k Hz` : `${f} Hz`, x + 3, h - 5);
    }
  }

  function drawSpectrum(ctx, w, h) {
    const data = new Float32Array(graph.analyser.frequencyBinCount);
    graph.analyser.getFloatFrequencyData(data);
    ctx.beginPath();
    for (let i = 0; i < 256; i++) {
      const f = xToFreq(i / 255, sr());
      const bin = Math.min(data.length - 1, Math.round(f * graph.analyser.fftSize / sr()));
      const y = h * (1 - Math.max(0, Math.min(1, (data[bin] + 100) / 100)));
      if (i === 0) ctx.moveTo(0, y); else ctx.lineTo(i / 255 * w, y);
    }
    ctx.strokeStyle = '#587e72';
    ctx.stroke();
  }

  function draw() {
    if (panel.hidden) return;
    const canvas = $('eqCanvas');
    const ratio = window.devicePixelRatio || 1;
    const w = Math.max(1, canvas.clientWidth);
    const h = Math.max(1, canvas.clientHeight);
    if (canvas.width !== Math.round(w * ratio) || canvas.height !== Math.round(h * ratio)) {
      canvas.width = Math.round(w * ratio);
      canvas.height = Math.round(h * ratio);
    }
    const ctx = canvas.getContext('2d');
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, w, h);
    drawGrid(ctx, w, h);
    if (graph && $('eqSpectrum').checked && !player.paused) drawSpectrum(ctx, w, h);
    const { db, peakGainDb } = currentCurve();
    ctx.beginPath();
    db.forEach((v, i) => {
      const x = i / 255 * w;
      const y = dbToY(v) * h;
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = '#9fe4ca';
    ctx.lineWidth = 2;
    ctx.stroke();
    $('eqWarning').textContent = peakGainDb > 9 ? 'Large boosts can be loud. Lower your volume before playing.' : '';
  }

  function renderEditor() {
    const band = settings.bands[selected];
    const lim = limitsFor(band.type, sr());
    $('eqBandLegend').textContent = `Band ${selected + 1}`;
    $('eqType').value = band.type;
    $('eqFrequency').value = Math.round(band.frequency_hz);
    $('eqFrequency').max = lim.frequency[1];
    $('eqGain').value = band.gain_db.toFixed(1);
    $('eqGain').disabled = !hasGain(band.type);
    $('eqQ').value = band.q.toFixed(2);
    $('eqQ').min = lim.q[0];
    $('eqQ').max = lim.q[1];
    $('eqQ').disabled = lim.q[0] === lim.q[1];
    $('eqSlope').value = band.slope_db_per_oct;
    $('eqSlope').disabled = !isPass(band.type);
    $('eqEnabled').checked = band.enabled;
  }

  function renderControls() {
    $('eqSolo').setAttribute('aria-pressed', String(solo !== null));
    $('eqBypass').setAttribute('aria-pressed', String(bypassed));
    $('eqMatch').checked = settings.level_match;
    $('eqTrim').value = settings.trim_db;
    $('eqPreset').value = settings.preset || '';
    $('eqPresetDescription').textContent = PRESETS.find(p => p.id === settings.preset)?.description || '';
    $('eqBypass').disabled = !graph;
    $('eqSolo').disabled = !graph;
  }

  function renderHandle(band, i) {
    const handle = handles[i];
    const gains = hasGain(band.type);
    const lim = limitsFor(band.type, sr());
    handle.style.left = `${freqToX(band.frequency_hz, sr()) * 100}%`;
    handle.style.top = `${dbToY(gains ? band.gain_db : 0) * 100}%`;
    handle.setAttribute('aria-valuemin', gains ? '-24' : '20');
    handle.setAttribute('aria-valuemax', String(gains ? 12 : lim.frequency[1]));
    handle.setAttribute('aria-valuenow', String(gains ? band.gain_db : band.frequency_hz));
    handle.setAttribute('aria-valuetext', describeBand(band, i));
    handle.classList.toggle('selected', i === selected);
    handle.classList.toggle('disabled', !band.enabled);
  }

  function renderTable() {
    const body = $('eqTable').tBodies[0];
    body.replaceChildren();
    settings.bands.forEach((band, i) => {
      const row = body.insertRow();
      const cells = [i + 1, band.enabled ? 'yes' : 'no', band.type, Math.round(band.frequency_hz), band.gain_db, band.q, band.slope_db_per_oct];
      for (const value of cells) row.insertCell().textContent = String(value);
    });
  }

  function render() {
    renderEditor();
    renderControls();
    settings.bands.forEach(renderHandle);
    renderTable();
    if (!unavailable && performance.now() >= statusHoldUntil) status(mode());
    draw();
  }

  panel.innerHTML=`<h3>Listening EQ</h3><p>EQ turns frequency ranges up or down in the whole mix. It cannot isolate an instrument: guitar, bass, drums and voice share these ranges.</p>
  <div class="eq-controls"><button id="eqEnable" type="button">Turn on EQ</button><button id="eqBypass" type="button" aria-pressed="false">Bypass A/B</button><label><input id="eqMatch" type="checkbox" checked> Level match (approximate)</label><label>Trim dB <input id="eqTrim" type="number" min="-12" max="6" step="0.5" value="0"></label><button id="eqReset" type="button">Reset</button><label>Load EQ from note… <input id="eqImport" type="file" accept=".json,application/json"></label></div>
  <p class="subtle">Level match assumes a pink-noise-like spectrum. Real music differs, so use Trim and your ears. Soft clipping limits digital peaks; it does not guarantee a safe listening volume.</p>
  <label>Starting points (creative suggestions) <select id="eqPreset"><option value="">Custom / flat</option></select></label><span class="subtle"> Ideas, not detections</span><p id="eqPresetDescription"></p>
  <div class="eq-chart" id="eqChart"><canvas id="eqCanvas" aria-hidden="true"></canvas></div>
  <p class="subtle">Curve: computed from your settings · Spectrum: measured playback output, not instrument detection.</p><label><input id="eqSpectrum" type="checkbox"> Show live spectrum</label>
  <fieldset class="eq-editor"><legend id="eqBandLegend">Band 1</legend><label>Type <select id="eqType"></select></label><label>Frequency Hz <input id="eqFrequency" type="number" min="20" step="1"></label><label>Gain dB <input id="eqGain" type="number" min="-24" max="12" step="0.5"></label><label>Q <input id="eqQ" type="number" step="0.01"></label><label>Slope dB/oct <select id="eqSlope"><option>12</option><option>24</option></select></label><label><input id="eqEnabled" type="checkbox"> Enabled</label><button id="eqSolo" type="button" aria-pressed="false">Solo frequency slice</button></fieldset>
  <p id="eqStatus" role="status"></p><p id="eqWarning" class="eq-warning"></p><p id="eqMonitor" class="eq-warning"></p><div class="visually-hidden"><table id="eqTable"><caption>EQ bands you set (the curve is computed from these settings)</caption><thead><tr><th>Band</th><th>On</th><th>Type</th><th>Frequency</th><th>Gain</th><th>Q</th><th>Slope</th></tr></thead><tbody></tbody></table></div>`;
  for (const type of TYPES) $('eqType').add(new Option(type, type));
  for (const p of PRESETS) $('eqPreset').add(new Option(p.label, p.id));
  $('eqSpectrum').checked = !matchMedia('(prefers-reduced-motion: reduce)').matches;

  function onHandleKey(e, i) {
    if (SLIDER_KEYS.includes(e.key)) {
      e.preventDefault();
      selected = i;
      const next = nudgeBand(settings.bands[i], e.key, e.shiftKey, e.ctrlKey, sr(), i);
      edit(next, DISCRETE_KEYS.includes(e.key));
    } else if (e.key.toLowerCase() === 's' && graph) {
      e.preventDefault();
      toggleSolo(i);
    }
  }

  // Pointer moves are coalesced to one edit per animation frame; the latest position wins.
  function onHandleMove(e, i) {
    const box = $('eqChart').getBoundingClientRect();
    pending = {
      i,
      frequency: xToFreq((e.clientX - box.left) / box.width, sr()),
      gain: yToDb((e.clientY - box.top) / box.height),
    };
    if (frame !== null) return;
    frame = requestAnimationFrame(() => {
      frame = null;
      if (!pending) return;
      const p = pending;
      pending = null;
      selected = p.i;
      const band = settings.bands[p.i];
      edit({ ...band, frequency_hz: p.frequency, gain_db: hasGain(band.type) ? p.gain : 0 });
    });
  }

  for (let i = 0; i < 7; i++) {
    const handle = document.createElement('button');
    handle.type = 'button';
    handle.className = 'eq-handle';
    handle.textContent = i + 1;
    handle.setAttribute('role', 'slider');
    handle.setAttribute('aria-label', `EQ band ${i + 1}`);
    $('eqChart').append(handle);
    handles.push(handle);
    let pointer = null;
    handle.addEventListener('focus', () => select(i));
    handle.addEventListener('keydown', e => onHandleKey(e, i));
    handle.addEventListener('pointerdown', e => {
      if (e.button !== 0 || e.isPrimary === false) return;
      select(i);
      pointer = e.pointerId;
      handle.setPointerCapture(pointer);
    });
    handle.addEventListener('pointermove', e => {
      if (pointer !== e.pointerId) return;
      if (!(e.buttons & 1)) {
        pointer = null;
        return;
      }
      onHandleMove(e, i);
    });
    handle.addEventListener('pointerup', () => { pointer = null; });
    for (const name of ['pointercancel', 'lostpointercapture']) {
      handle.addEventListener(name, () => {
        pointer = null;
        cancelDrag();
      });
    }
    handle.addEventListener('dblclick', () => {
      selected = i;
      edit(defaultBands()[i], true);
    });
    handle.addEventListener('wheel', e => {
      e.preventDefault();
      selected = i;
      edit(nudgeBand(settings.bands[i], e.deltaY < 0 ? 'PageUp' : 'PageDown', false, false, sr(), i));
    }, { passive: false });
  }

  const EDITOR_FIELDS = [
    ['eqType', 'type'], ['eqFrequency', 'frequency_hz'], ['eqGain', 'gain_db'],
    ['eqQ', 'q'], ['eqSlope', 'slope_db_per_oct'], ['eqEnabled', 'enabled'],
  ];
  for (const [id, key] of EDITOR_FIELDS) {
    $(id).addEventListener('change', () => {
      const el = $(id);
      const value = key === 'type' ? el.value : key === 'enabled' ? el.checked : Number(el.value);
      const discrete = ['type', 'slope_db_per_oct', 'enabled'].includes(key);
      edit({ ...settings.bands[selected], [key]: value }, discrete);
    });
  }
  $('eqMatch').addEventListener('change', () => {
    settings.level_match = $('eqMatch').checked;
    changed();
  });
  $('eqTrim').addEventListener('change', () => {
    const n = Number($('eqTrim').value);
    settings.trim_db = Number.isFinite(n) ? Math.max(-12, Math.min(6, n)) : 0;
    changed();
  });
  $('eqBypass').addEventListener('click', toggleBypass);
  $('eqSolo').addEventListener('click', () => toggleSolo());
  $('eqReset').addEventListener('click', () => {
    settings = freshSettings();
    solo = null;
    changed(true);
    announce('Reset to flat');
  });
  $('eqPreset').addEventListener('change', () => {
    const p = PRESETS.find(preset => preset.id === $('eqPreset').value);
    if (!p) return;
    settings.bands = p.bands.map((b, i) => clampBand(b, sr(), i));
    settings.preset = p.id;
    solo = null;
    changed(true);
    announce(`Preset applied: ${p.label}`);
  });
  panel.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
      solo = null;
      changed();
    }
    if (['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target.tagName)) return;
    if (e.key.toLowerCase() === 'b' && graph) {
      e.preventDefault();
      toggleBypass();
    }
  });
  $('eqDisclosure').addEventListener('click', () => {
    panel.hidden = !panel.hidden;
    $('eqDisclosure').setAttribute('aria-expanded', String(!panel.hidden));
    draw();
  });
  $('eqSpectrum').addEventListener('change', draw);

  // The note file is untrusted: size-capped, parsed as JSON only, validated and clamped by parseEqFromNote, and its
  // file name is shown as text. A later import or a newer change supersedes an earlier one through importRevision.
  async function importNote() {
    const token = ++importRevision;
    const file = $('eqImport').files[0];
    if (!file) return;
    try {
      if (file.size > IMPORT_LIMIT_BYTES) throw Error('Note exceeds 256 KB.');
      const note = JSON.parse(await file.text());
      const result = parseEqFromNote(note, sr());
      if (token !== importRevision) return;
      if (!result.ok) throw Error(result.reason);
      settings = result.settings;
      solo = null;
      changed(true);
      const saved = typeof note.file_name === 'string' ? `Saved with ‘${note.file_name}’. ` : '';
      status(`EQ loaded. ${saved}${result.notices.join(' ')}`, MESSAGE_HOLD_MS);
      announce('EQ settings loaded.');
    } catch (e) {
      if (token === importRevision) {
        status(e.message, MESSAGE_HOLD_MS);
        announce(e.message);
      }
    } finally {
      $('eqImport').value = '';
    }
  }
  $('eqImport').addEventListener('change', importNote);

  function stopPlayback() {
    if (!player.paused) player.pause();
  }

  // Warn about hot output only when the EQ itself adds level; a loud master through a flat EQ is not the EQ's doing.
  // Each distinct warning is announced once per playback, not on a timer.
  function checkOutput(now) {
    const data = new Float32Array(graph.analyser.fftSize);
    graph.analyser.getFloatTimeDomainData(data);
    const peak = data.reduce((p, v) => Math.max(p, Math.abs(v)), 0);
    const adding = !bypassed && solo === null && currentCurve().peakGainDb > 0;
    let warning = peak > SOFT_KNEE && adding ? 'Output peaks are near the soft-clipping region. Reduce boosts or Trim.' : '';
    if (peak === 0 && !player.muted && player.volume > 0 && player.currentTime > lastTime) {
      silentSince ??= now;
      if (now - silentSince > 1500) warning = 'EQ may not hear playback in this window. If you hear nothing, reload the page.';
    } else {
      silentSince = null;
    }
    lastTime = player.currentTime;
    $('eqMonitor').textContent = warning;
    if (warning && warning !== announcedWarning) {
      announce(warning);
      announcedWarning = warning;
    }
  }

  function monitoring() {
    clearInterval(monitor);
    monitor = null;
    silentSince = null;
    announcedWarning = '';
    if (!graph || player.paused || document.hidden) return;
    monitor = setInterval(() => {
      if (graph.ctx.state !== 'running') {
        status(PAUSED_MESSAGE);
        return;
      }
      const now = performance.now();
      checkOutput(now);
      if ($('eqSpectrum').checked && now - lastDraw > 33) {
        draw();
        lastDraw = now;
      }
    }, 100);
  }

  function onContextState(ctx) {
    if (ctx.state === 'closed') {
      unavailable = true;
      stopPlayback();
      status(DEAD_MESSAGE, MESSAGE_HOLD_MS);
    } else if (ctx.state !== 'running') {
      // The media element would keep advancing silently while the graph is suspended, so stop it.
      stopPlayback();
      status(PAUSED_MESSAGE, MESSAGE_HOLD_MS);
    } else {
      render();
    }
  }

  $('eqEnable').addEventListener('click', async () => {
    if (graph || unavailable) return;
    $('eqEnable').disabled = true;
    let ctx;
    let captured = false;
    try {
      const Context = window.AudioContext || window.webkitAudioContext;
      ctx = new Context();
      graph = buildGraph(ctx, player);
      captured = true;
      settings.bands = settings.bands.map((b, i) => clampBand(b, ctx.sampleRate, i));
      curveCache = null;
      apply();
      ctx.addEventListener('statechange', () => onContextState(ctx));
      await ctx.resume();
      render();
      monitoring();
      announce('EQ on');
    } catch (e) {
      unavailable = true;
      if (!captured) {
        graph = null;
        ctx?.close().catch(() => {});
      }
      status(captured ? DEAD_MESSAGE : UNAVAILABLE_MESSAGE, MESSAGE_HOLD_MS);
    }
  });

  for (const event of ['play', 'pause', 'ended']) player.addEventListener(event, monitoring);
  document.addEventListener('visibilitychange', monitoring);
  window.addEventListener('resize', draw);
  const Context = window.AudioContext || window.webkitAudioContext;
  if (!Context || !Context.prototype.createMediaElementSource) {
    unavailable = true;
    $('eqEnable').disabled = true;
    status(UNAVAILABLE_MESSAGE);
  }
  render();

  const snapshot = () => eqNoteBlock({ ...settings, bypassed, turned_on: !!graph && !unavailable });

  function responseDb(freqs) {
    if (!graph) return null;
    const f = new Float32Array(freqs);
    const mag = new Float32Array(f.length);
    const phase = new Float32Array(f.length);
    const out = new Array(f.length).fill(0);
    for (const node of graph.filters) {
      node.getFrequencyResponse(f, mag, phase);
      out.forEach((_, i) => { out[i] += 20 * Math.log10(mag[i]); });
    }
    return out;
  }

  function spectrumDb(hz) {
    if (!graph) return null;
    const data = new Float32Array(graph.analyser.frequencyBinCount);
    graph.analyser.getFloatFrequencyData(data);
    return data[Math.round(hz * graph.analyser.fftSize / sr())];
  }

  if (new URLSearchParams(location.search).get('eqdebug') === '1') {
    const state = () => ({ ...snapshot(), solo, context: graph?.ctx.state || null, sampleRate: sr() });
    Object.defineProperty(window, '__ohrinEq', { value: Object.freeze({ state, responseDb, spectrumDb }) });
  }

  return {
    async resume() {
      if (!graph) return;
      // After capture the element plays only through the graph; a dead graph cannot be resumed from here.
      if (unavailable) throw Error(DEAD_MESSAGE);
      await graph.ctx.resume();
      if (graph.ctx.state !== 'running') throw Error(PAUSED_MESSAGE);
    },
    fileChanged() {
      solo = null;
      cancelDrag();
      revision++;
      clearTimeout(switchTimer);
      apply();
      render();
    },
    snapshot,
  };
}
