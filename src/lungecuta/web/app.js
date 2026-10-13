// lungecuta's editor: the viewer, the timeline with its beat grid, playback against the
// audio clock, and edits through the engine's own operations (docs/design/06-editing.md).
"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  project: null,
  tl: null,              // the comp's timeline, from /api/comp
  frame: 0,              // the playhead, a comp frame index
  cache: new Map(),      // "version:frame" -> ImageData (the version covers the comp and its styles and LUTs)
  asked: new Set(),      // "hash:frame" requested and not arrived
  pending: new Map(),    // request id -> its "hash:frame", so a reply clears the key it was asked under
  nextId: 1,
  zoom: 4,               // timeline pixels per frame
  selected: null,
  playing: false,
  audio: null,           // { ctx, buffer, source, startedAt, from }
  graphics: new Map(),   // layer id -> iframe
  ready: new Set(),      // graphics layers whose page is ready
  curves: null,          // the selected footage layer's properties per frame, from /curves
  styles: [],            // the project's styles
  graph: "speed",        // what the graph editor shows: "speed" or "source"
};
const ROW = 30, RULER = 26, GRID = 18, LEFT = 0;

// ----- server -------------------------------------------------------------

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const body = response.headers.get("content-type")?.includes("json") ? await response.json() : await response.arrayBuffer();
  if (!response.ok) throw new Error(body.detail || response.statusText);
  return body;
}

function status(text, error = false) {
  const el = $("status");
  el.textContent = text;
  el.className = error ? "error" : "";
}

let socket = null;
function connect() {
  socket = new WebSocket(`ws://${location.host}/ws`);
  socket.binaryType = "arraybuffer";
  socket.onopen = () => { status("connected"); show(state.frame); };
  socket.onclose = () => { status("disconnected; reload to reconnect", true); };
  socket.onmessage = (event) => {
    if (typeof event.data === "string") return message(JSON.parse(event.data));
    const view = new DataView(event.data);
    const length = view.getUint32(0, true);
    const header = JSON.parse(new TextDecoder().decode(new Uint8Array(event.data, 4, length)));
    // Cleared by the id it was asked under: an edit in between gives the reply a new version.
    state.asked.delete(state.pending.get(header.id));
    state.pending.delete(header.id);
    const key = `${header.version}:${header.frame}`;
    const pixels = new Uint8ClampedArray(event.data, 4 + length);
    state.cache.set(key, new ImageData(pixels, header.width, header.height));
    if (state.tl && header.version === state.tl.version && header.frame === state.frame) draw();
    pump();
  };
}

function message(m) {
  if (m.type === "changed") {
    // A file changed underneath the editor: the agent's edit, or another tool's.
    status(`${m.file} changed on disk; reloading`);
    if (state.tl && (m.file === `comps/${state.tl.name}.json` || m.file === "project.json"
                     || ["media/", "styles/", "luts/"].some((prefix) => m.file.startsWith(prefix)))) {
      loadComp(state.tl.name, true);
    }
  } else if (m.type === "error") {
    state.asked.delete(state.pending.get(m.id));
    state.pending.delete(m.id);
    status(m.detail, true);
    pump();
  }
}

// ----- frames -------------------------------------------------------------

const wanted = [];
function ask(frame) {
  if (!state.tl || frame < 0 || frame >= state.tl.frames) return;
  const key = `${state.tl.version}:${frame}`;
  if (state.cache.has(key) || state.asked.has(key) || wanted.includes(frame)) return;
  wanted.push(frame);
  pump();
}

function pump() {
  // A few requests in flight at once; the server renders one at a time.
  while (wanted.length && state.asked.size < 3 && socket && socket.readyState === 1) {
    const frame = wanted.shift();
    const key = `${state.tl.version}:${frame}`;
    if (state.cache.has(key) || state.asked.has(key)) continue;
    state.asked.add(key);
    const id = state.nextId++;
    state.pending.set(id, key);
    socket.send(JSON.stringify({ type: "frame", id, comp: state.tl.name, frame }));
  }
}

function draw() {
  const image = state.cache.get(`${state.tl.version}:${state.frame}`);
  // A frame not rendered yet is shown as missing, never as a stale one (01-architecture.md).
  $("missing").hidden = Boolean(image);
  if (image) $("picture").getContext("2d").putImageData(image, 0, 0);
}

function show(frame) {
  if (!state.tl) return;
  state.frame = Math.max(0, Math.min(state.tl.frames - 1, frame));
  wanted.length = 0;
  ask(state.frame);
  draw();
  for (let k = 1; k <= 6; k++) ask(state.frame + k);
  graphicsAt(state.frame);
  readout();
  drawTimeline();
  inspectorAt();
  drawGraph();
  scopes();
}

function readout() {
  const tl = state.tl;
  const seconds = state.frame / tl.fps;
  $("time").textContent = `f:${state.frame}  ${seconds.toFixed(3)} s  of ${tl.frames}`;
  const coordinate = beatCoordinate(state.frame);
  $("beat").textContent = coordinate === null ? "" : `b:${coordinate.toFixed(2)}`;
  $("selection").textContent = state.selected ? `selected: ${state.selected}` : "";
}

// ----- beats --------------------------------------------------------------

function beatCoordinate(frame) {
  const beats = state.tl.beats;
  if (beats.length < 2 || frame < beats[0].frame || frame > beats[beats.length - 1].frame) return null;
  let k = 0;
  while (k + 1 < beats.length && beats[k + 1].frame <= frame) k++;
  if (k + 1 === beats.length) return k;
  return k + (frame - beats[k].frame) / (beats[k + 1].frame - beats[k].frame);
}

function nearestBeat(frame) {
  const beats = state.tl.beats;
  let best = 0;
  for (let k = 1; k < beats.length; k++) if (Math.abs(beats[k].frame - frame) < Math.abs(beats[best].frame - frame)) best = k;
  return best;
}

function amount(from, to, inFrames) {
  // A signed span from `from` to `to` (frames), as a beat amount on the grid unless asked for
  // frames: the engine writes the result back in the field's own unit.
  if (inFrames || state.tl.beats.length < 2) return `f:${Math.round(to - from)}`;
  const start = beatCoordinate(from);
  const target = nearestBeat(to);
  if (start === null) return `f:${Math.round(to - from)}`;
  const beats = +(target - start).toFixed(6);
  return `b:${beats}`;
}

// ----- graphics -----------------------------------------------------------

function buildGraphics() {
  const box = $("overlays");
  box.innerHTML = "";
  state.graphics.clear();
  state.ready.clear();
  const [cw, ch] = state.tl.size;
  const [pw, ph] = state.tl.preview;
  for (const layer of state.tl.layers.filter((l) => l.type === "graphics")) {
    const frame = document.createElement("iframe");
    const query = new URLSearchParams({ id: layer.id, page: layer.page, params: JSON.stringify(layer.params || {}) });
    frame.src = `${state.project.graphics_origin}/__lc_frame.html?${query}`;
    frame.width = cw;
    frame.height = ch;
    frame.style.transform = `scale(${pw / cw})`;
    frame.hidden = true;
    box.appendChild(frame);
    state.graphics.set(layer.id, { frame, layer });
  }
}

window.addEventListener("message", (event) => {
  if (event.data && event.data.lc === "ready") { state.ready.add(event.data.id); graphicsAt(state.frame); }
});

function graphicsAt(frame) {
  // The editor draws web layers live over the engine's frames (01-architecture.md, Preview).
  for (const { frame: iframe, layer } of state.graphics.values()) {
    // A page under footage is composited in the working space by the engine, which the
    // preview does not capture; drawing it over everything would show the wrong stack.
    const visible = layer.top && frame >= layer.first && frame < layer.end;
    iframe.hidden = !visible;
    if (visible) iframe.contentWindow.postMessage({ lc: "at", t: (frame - layer.start) / state.tl.fps }, "*");
  }
}

// ----- timeline -----------------------------------------------------------

function rows() {
  // The top row is the top of the stack: the last layer listed.
  return [...state.tl.layers].sort((a, b) => b.row - a.row);
}

function drawTimeline() {
  const tl = state.tl;
  const canvas = $("timeline");
  const layers = rows();
  const width = Math.max(canvas.parentElement.clientWidth, Math.ceil(tl.frames * state.zoom) + 40);
  const height = RULER + GRID + layers.length * ROW + 8;
  if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
  const g = canvas.getContext("2d");
  const x = (frame) => LEFT + frame * state.zoom;
  g.fillStyle = "#111317"; g.fillRect(0, 0, width, height);
  // Ruler: a tick a second, labelled; frames when zoomed in far enough.
  g.fillStyle = "#1a1d23"; g.fillRect(0, 0, width, RULER);
  g.strokeStyle = "#3a404b"; g.fillStyle = "#8a919d"; g.font = "11px -apple-system, sans-serif";
  for (let s = 0; s * tl.fps <= tl.frames; s++) {
    g.beginPath(); g.moveTo(x(s * tl.fps) + 0.5, RULER - 10); g.lineTo(x(s * tl.fps) + 0.5, RULER); g.stroke();
    g.fillText(`${s}s`, x(s * tl.fps) + 3, 12);
  }
  if (state.zoom >= 6) for (let f = 0; f < tl.frames; f++) {
    g.beginPath(); g.moveTo(x(f) + 0.5, RULER - 4); g.lineTo(x(f) + 0.5, RULER); g.stroke();
  }
  // Beat grid: beats as ticks, downbeats as lines with their bar numbers.
  let bar = 0;
  for (const beat of tl.beats) {
    const bx = x(beat.frame) + 0.5;
    g.strokeStyle = beat.downbeat ? "rgba(255,181,71,0.55)" : "rgba(255,181,71,0.18)";
    g.beginPath(); g.moveTo(bx, RULER + (beat.downbeat ? 0 : GRID / 2)); g.lineTo(bx, height); g.stroke();
    if (beat.downbeat) { g.fillStyle = "#ffb547"; g.fillText(`${bar}`, bx + 3, RULER + 12); bar++; }
  }
  // Layers.
  const colours = { footage: "#3d6fb6", audio: "#3f9a6b", graphics: "#b8763a", comp: "#7a5bb5" };
  layers.forEach((layer, k) => {
    const y = RULER + GRID + k * ROW;
    g.fillStyle = layer.id === state.selected ? "rgba(255,255,255,0.06)" : "transparent";
    g.fillRect(0, y, width, ROW);
    const x0 = x(layer.first), x1 = x(layer.end);
    g.fillStyle = colours[layer.type];
    g.globalAlpha = layer.id === state.selected ? 1 : 0.8;
    g.fillRect(x0, y + 3, Math.max(2, x1 - x0), ROW - 6);
    g.globalAlpha = 1;
    if (layer.id === state.selected) { g.strokeStyle = "#ffffff"; g.strokeRect(x0 + 0.5, y + 3.5, Math.max(2, x1 - x0) - 1, ROW - 7); }
    g.fillStyle = "#f2f4f7";
    const speed = layer.remapped ? "  remapped" : layer.speed !== 1 ? `  ${Math.round(layer.speed * 100)}%` : "";
    const label = layer.type === "footage" ? `${layer.id}  ${layer.media}${speed}` : `${layer.id}  ${layer.media || layer.page || ""}`;
    g.save(); g.beginPath(); g.rect(x0, y, Math.max(0, x1 - x0), ROW); g.clip();
    g.fillText(label, x0 + 5, y + 19); g.restore();
    for (const [f0, f1, a0, a1] of layer.ramps || []) {
      // A transition's opacity ramp: darker where the layer is more transparent.
      const ramp = g.createLinearGradient(x(f0), 0, x(f1), 0);
      ramp.addColorStop(0, `rgba(0,0,0,${0.6 * (1 - a0)})`);
      ramp.addColorStop(1, `rgba(0,0,0,${0.6 * (1 - a1)})`);
      g.fillStyle = ramp;
      g.fillRect(x(f0), y + 3, x(f1) - x(f0), ROW - 6);
    }
    if (layer.type === "audio" && layer.fades) {
      // Fades as ramps over the row: up from the in point, down to the out point.
      g.fillStyle = "rgba(0,0,0,0.35)";
      const [up, down] = layer.fades;
      if (up > 0) { g.beginPath(); g.moveTo(x0, y + 3); g.lineTo(x(layer.first + up), y + 3); g.lineTo(x0, y + ROW - 3); g.closePath(); g.fill(); }
      if (down > 0) { g.beginPath(); g.moveTo(x1, y + 3); g.lineTo(x(layer.end - down), y + 3); g.lineTo(x1, y + ROW - 3); g.closePath(); g.fill(); }
    }
    for (const key of layer.keys || []) {
      const kx = x(key.frame), ky = y + ROW - 7;
      g.fillStyle = key.prop === "remap" ? "#7fd1ff" : "#ffffff";
      g.beginPath(); g.moveTo(kx, ky - 4); g.lineTo(kx + 4, ky); g.lineTo(kx, ky + 4); g.lineTo(kx - 4, ky); g.closePath(); g.fill();
    }
  });
  // Markers on the ruler (comp) or on their layer's row.
  for (const marker of tl.markers) {
    const mx = x(marker.frame);
    const row = marker.layer ? rows().findIndex((l) => l.id === marker.layer) : -1;
    const my = row < 0 ? RULER - 2 : RULER + GRID + row * ROW + 4;
    g.fillStyle = "#ff6b5b";
    g.beginPath(); g.moveTo(mx, my); g.lineTo(mx - 5, my - 7); g.lineTo(mx + 5, my - 7); g.closePath(); g.fill();
  }
  // The playhead.
  g.strokeStyle = "#ffffff";
  g.beginPath(); g.moveTo(x(state.frame) + 0.5, 0); g.lineTo(x(state.frame) + 0.5, height); g.stroke();
}

function hit(event) {
  const canvas = $("timeline");
  const rect = canvas.getBoundingClientRect();
  const px = event.clientX - rect.left, py = event.clientY - rect.top;
  const frame = (px - LEFT) / state.zoom;
  if (py < RULER + GRID) return { where: "ruler", frame };
  const k = Math.floor((py - RULER - GRID) / ROW);
  const layer = rows()[k];
  if (!layer || frame < layer.first - 2 / state.zoom || frame > layer.end + 2 / state.zoom) return { where: "empty", frame };
  const edge = 6 / state.zoom;
  if (layer.type !== "audio" && Math.abs(frame - layer.first) < edge) return { where: "in", frame, layer };
  if (layer.type !== "audio" && Math.abs(frame - layer.end) < edge) return { where: "out", frame, layer };
  return { where: "body", frame, layer };
}

let drag = null;
function onDown(event) {
  const target = hit(event);
  if (target.where === "ruler" || target.where === "empty") {
    drag = { kind: "scrub" };
    show(Math.round(target.frame));
    return;
  }
  const changed = state.selected !== target.layer.id;
  state.selected = target.layer.id;
  drag = { kind: target.where, layer: target.layer, from: target.frame, to: target.frame };
  readout();
  drawTimeline();
  if (changed) inspect();
}

function onMove(event) {
  if (!drag) {
    const target = hit(event);
    $("timeline").style.cursor = target.where === "in" || target.where === "out" ? "ew-resize"
      : target.where === "body" ? "grab" : "default";
    return;
  }
  const target = hit(event);
  if (drag.kind === "scrub") return show(Math.round(target.frame));
  drag.to = target.frame;
  // A ghost of where the layer would land, snapped as the edit will be.
  drawTimeline();
  const g = $("timeline").getContext("2d");
  const k = rows().findIndex((l) => l.id === drag.layer.id);
  const y = RULER + GRID + k * ROW;
  const delta = drag.to - drag.from;
  const snap = (frame) => (event.altKey || state.tl.beats.length < 2) ? Math.round(frame) : state.tl.beats[nearestBeat(frame)].frame;
  let first = drag.layer.first, end = drag.layer.end;
  if (drag.kind === "body") { const s = snap(drag.layer.start + delta) - drag.layer.start; first += s; end += s; }
  if (drag.kind === "in") first = snap(drag.layer.in_at + delta);
  if (drag.kind === "out") end = snap(drag.layer.out_at + delta);
  g.strokeStyle = "#ffb547"; g.setLineDash([4, 3]);
  g.strokeRect(first * state.zoom + 0.5, y + 2.5, (end - first) * state.zoom, ROW - 5);
  g.setLineDash([]);
}

async function onUp(event) {
  if (!drag) return;
  const finished = drag;
  drag = null;
  if (finished.kind === "scrub" || Math.abs(finished.to - finished.from) * state.zoom < 3) return drawTimeline();
  const layer = finished.layer;
  const delta = finished.to - finished.from;
  if (finished.kind === "body" && (event.metaKey || event.ctrlKey) && layer.type === "footage") {
    // Cmd-drag slides the layer between its neighbours instead of moving it over them.
    await edit("slide", { layer_id: layer.id, by: amount(layer.in_at, layer.in_at + delta, event.altKey) });
  } else if (finished.kind === "body") {
    await edit("move", { layer_id: layer.id, by: amount(layer.start, layer.start + delta, event.altKey) });
  } else {
    const point = finished.kind === "in" ? layer.in_at : layer.out_at;
    await edit("trim", { layer_id: layer.id, side: finished.kind, by: amount(point, point + delta, event.altKey) });
  }
}

// ----- edits --------------------------------------------------------------

async function edit(op, args) {
  try {
    const result = await api("/api/edit", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ comp: state.tl.name, op, args, expect: state.tl.hash }),
    });
    setTimeline(result.timeline);
    status(result.changes.join("; "));
    journal();
  } catch (error) {
    status(error.message, true);
    drawTimeline();
  }
}

async function step(which) {
  try {
    const result = await api(`/api/${which}`, { method: "POST" });
    status(result.changes.join("; "));
    await loadComp(state.tl.name, true);
    journal();
  } catch (error) {
    status(error.message, true);
  }
}

async function journal() {
  const { entries } = await api("/api/journal");
  const list = $("journal");
  list.innerHTML = "";
  for (const entry of entries.slice(-30).reverse()) {
    const item = document.createElement("li");
    item.textContent = `${entry.seq} ${entry.kind}: ${entry.changes.join("; ")}`;
    list.appendChild(item);
  }
}

// ----- playback -----------------------------------------------------------

async function audioBuffer() {
  if (state.audio && state.audio.hash === state.tl.hash) return state.audio;
  const ctx = state.audio?.ctx || new AudioContext({ sampleRate: 48000 });
  const data = await api(`/api/comp/${state.tl.name}/audio.wav`);
  const buffer = await ctx.decodeAudioData(data);
  state.audio = { ctx, buffer, hash: state.tl.hash };
  return state.audio;
}

async function play() {
  if (state.playing) return stop();
  const audio = await audioBuffer();
  await audio.ctx.resume();
  const from = state.frame / state.tl.fps;
  audio.source = audio.ctx.createBufferSource();
  audio.source.buffer = audio.buffer;
  audio.source.connect(audio.ctx.destination);
  audio.startedAt = audio.ctx.currentTime;
  audio.from = from;
  audio.source.start(audio.startedAt, from);
  state.playing = true;
  $("play").textContent = "Pause";
  requestAnimationFrame(tick);
}

function stop() {
  state.playing = false;
  $("play").textContent = "Play";
  if (state.audio?.source) { state.audio.source.stop(); state.audio.source = null; }
}

function tick() {
  if (!state.playing) return;
  const audio = state.audio;
  // The frame on screen follows the sound being heard: getOutputTimestamp gives the
  // context time now reaching the output, latency included (01-architecture.md, Playback).
  const heard = audio.ctx.getOutputTimestamp().contextTime ?? (audio.ctx.currentTime - audio.ctx.outputLatency);
  const position = audio.from + Math.max(0, heard - audio.startedAt);
  const frame = Math.floor(position * state.tl.fps);
  if (frame >= state.tl.frames) { stop(); return show(state.tl.frames - 1); }
  if (frame !== state.frame) {
    state.frame = frame;
    draw();
    graphicsAt(frame);
    readout();
    drawTimeline();
  }
  for (let k = 1; k <= 10; k++) ask(frame + k);
  requestAnimationFrame(tick);
}

// ----- inspector ----------------------------------------------------------

function selectedLayer() {
  return state.tl && state.tl.layers.find((l) => l.id === state.selected);
}

async function inspect() {
  const layer = selectedLayer();
  state.curves = null;
  if (layer && (layer.type === "footage" || layer.type === "audio")) {
    try {
      state.curves = await api(`/api/comp/${state.tl.name}/layer/${layer.id}/curves`);
    } catch (error) {
      status(error.message, true);
    }
  }
  renderInspector();
  buildGraphModes();
  drawGraph();
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k.startsWith("on")) node[k] = v;
    else if (v !== undefined && v !== null) node.setAttribute(k, v);
  }
  for (const child of children) node.append(child);
  return node;
}

const DEFAULTS = { position: [0.5, 0.5], scale: [1, 1], opacity: 1, rotation: 0, gain: 0 };

function valueAt(prop) {
  // The property's value at the playhead, as the engine evaluated it.
  const c = state.curves;
  if (!c || !c[prop]) return DEFAULTS[prop];
  const k = Math.max(0, Math.min(c[prop].length - 1, state.frame - c.first));
  return c[prop][k];
}

function keyHere(prop) {
  const layer = selectedLayer();
  return (layer?.keys || []).find((k) => k.prop === prop && Math.abs(k.frame - state.frame) < 0.5);
}

function animated(prop) {
  return (selectedLayer()?.keys || []).some((k) => k.prop === prop);
}

function round(value, digits = 4) {
  return Number(value.toFixed(digits));
}

function number(value, step, onchange) {
  return el("input", { type: "number", step, value: round(value), onchange });
}

function propertyRow(prop) {
  // A transform property: its value at the playhead, a key button, and the key's ease.
  const value = valueAt(prop);
  const here = keyHere(prop);
  const single = ["opacity", "rotation", "gain"].includes(prop);
  const read = (inputs) => single ? Number(inputs[0].value)
    : prop === "scale" && Number(inputs[0].value) === Number(inputs[1].value) ? Number(inputs[0].value)
    : inputs.map((i) => Number(i.value));
  const inputs = [];
  const apply = () => {
    const v = read(inputs);
    if (animated(prop)) edit("key", { layer_id: state.selected, prop, at: here ? here.at : `f:${state.frame}`, value: v,
                                      ease: here ? here.ease : null });
    else edit("set", { layer_id: state.selected, prop, value: v });
  };
  if (single) inputs.push(number(value, prop === "opacity" ? 0.01 : 0.5, apply));
  else inputs.push(number(value[0], 0.01, apply), number(value[1], 0.01, apply));
  const field = inputs.length === 1 ? inputs[0] : el("div", { class: "pair" }, ...inputs);
  const key = el("button", { class: here ? "key on" : "key", title: here ? "Delete this key" : "Add a key here",
    onclick: () => here ? edit("unkey", { layer_id: state.selected, prop, at: here.at })
      : edit("key", { layer_id: state.selected, prop, at: `f:${state.frame}`, value: read(inputs) }) }, "◆");
  const ease = el("select", { title: "How the segment arriving at this key moves", disabled: here ? null : "",
    onchange: (e) => edit("key", { layer_id: state.selected, prop, at: here.at, value: here.value,
                                   ease: e.target.value || null }) },
    ...["", "in", "out", "in_out", "hold"].map((v) => el("option", { value: v }, v || "linear")));
  ease.value = here?.ease || "";
  return el("div", { class: "row" }, el("span", {}, prop), field, key, ease);
}

function sourceLiteral(frames, fps) {
  // A source time as the engine takes it: whole source frames exactly, anything between in
  // seconds to five places (a frame boundary is never crossed by rounding a whole frame).
  if (Math.abs(frames - Math.round(frames)) < 1e-6) return `f:${Math.round(frames)}`;
  return `${round(frames / fps, 5)}s`;
}

function timeSection(layer) {
  const box = el("div");
  const actions = el("div", { class: "actions" });
  if (layer.style) {
    const intensity = layer.style.overrides?.intensity;
    box.append(el("p", {}, `style ${layer.style.style} at ${layer.style.at}` + (intensity === undefined ? "" : `, intensity ${intensity}`)));
    actions.append(el("button", { onclick: () => edit("unstyle", { layer_id: layer.id }) }, "Remove style"));
  } else if (layer.remapped) {
    const n = layer.keys.filter((k) => k.prop === "remap").length;
    box.append(el("p", {}, `time remapped, ${n} keys`));
    actions.append(el("button", { title: "A source-time key at the playhead holding what it shows now",
      onclick: () => {
        const c = state.curves;
        const here = keyHere("remap");
        edit("key", { layer_id: layer.id, prop: "remap", at: here ? here.at : `f:${state.frame}`,
                      value: sourceLiteral(c.source[state.frame - c.first], c.media_fps) });
      } }, "Key source here"));
  } else {
    box.append(el("p", {}, layer.speed === 1 ? "plays at 100 %" : `constant speed ${round(layer.speed * 100, 2)} %`));
    actions.append(el("button", { onclick: () => edit("remap", { layer_id: layer.id }) }, "Enable time remap"));
  }
  if (!layer.remapped && (layer.speed === 1 || layer.style) && state.styles.length && state.tl.beats.length) {
    const select = el("select", {}, ...state.styles.map((st) => el("option", { value: st.id }, st.name)));
    const intensity = el("input", { type: "number", step: 0.05, title: "intensity" });
    const param = () => state.styles.find((st) => st.id === select.value)?.params?.intensity;
    const fit = () => {
      // Only a style with an intensity parameter takes one, within its range.
      const p = param();
      intensity.hidden = !p;
      if (p) Object.assign(intensity, { min: p.min, max: p.max, value: p.default });
    };
    select.onchange = fit;
    fit();
    actions.append(select, intensity, el("button", { title: "Anchor the style on the beat nearest the playhead",
      onclick: () => edit("style", { layer_id: layer.id, style_id: select.value, at: `b:${nearestBeat(state.frame)}`,
                                     intensity: param() ? Number(intensity.value) : null }) }, "Place on beat"));
  }
  box.append(actions);
  return box;
}

function maskSection(layer) {
  // Each mask's fields; a change replaces the layer's mask list through the engine.
  const list = layer.masks || [];
  const commit = (next) => edit("masks", { layer_id: layer.id, masks: next });
  const box = el("div");
  list.forEach((mask, k) => {
    const change = (patch) => commit(list.map((m, j) => j === k ? { ...m, ...patch } : m));
    const pair = (name) => el("div", { class: "pair" }, ...[0, 1].map((i) => number(mask[name][i], 0.01,
      (e) => change({ [name]: mask[name].map((v, j) => j === i ? Number(e.target.value) : v) }))));
    const mode = el("select", { onchange: (e) => change({ mode: e.target.value }) },
      ...["add", "subtract", "intersect"].map((v) => el("option", { value: v }, v)));
    mode.value = mask.mode;
    const invert = el("input", { type: "checkbox", title: "invert", onchange: (e) => change({ invert: e.target.checked }) });
    invert.checked = mask.invert;
    box.append(
      el("div", { class: "row" }, el("span", {}, `${k + 1} ${mask.shape}`), mode, invert,
        el("button", { class: "key", title: "Remove this mask", onclick: () => commit(list.filter((_, j) => j !== k)) }, "\u00d7")),
      el("div", { class: "row" }, el("span", { class: "dim" }, "centre"), pair("centre")),
      el("div", { class: "row" }, el("span", { class: "dim" }, "size"), pair("size")),
      el("div", { class: "row" }, el("span", { class: "dim" }, "feather"), number(mask.feather, 0.005,
        (e) => change({ feather: Number(e.target.value) }))));
  });
  const add = (shape) => commit([...list, { shape, centre: [0.5, 0.5], size: [0.6, 0.6], feather: 0.02, invert: false, mode: "add" }]);
  box.append(el("div", { class: "actions" }, el("button", { onclick: () => add("rectangle") }, "Add rectangle"),
                el("button", { onclick: () => add("ellipse") }, "Add ellipse")));
  return box;
}

function transitionSection(layer) {
  // Fades on this layer, and a dissolve or dip to the footage layer that starts where it ends.
  const length = el("input", { type: "text", value: "b:1", title: "length: b:1, 0.5s, f:12" });
  const next = state.tl.layers.find((l) => l.type === "footage" && l.id !== layer.id && l.first === layer.end);
  const existing = (state.tl.comp.transitions || []).filter((t) => t.layers.includes(layer.id));
  const put = (kind, layers) => edit("transition", { kind, layer_ids: layers, length: length.value.trim() });
  const actions = el("div", { class: "actions" }, length,
    el("button", { onclick: () => put("fade_in", [layer.id]) }, "Fade in"),
    el("button", { onclick: () => put("fade_out", [layer.id]) }, "Fade out"));
  if (next) actions.append(
    el("button", { title: `Dissolve to ${next.id}, centred on the cut`, onclick: () => put("dissolve", [layer.id, next.id]) }, "Dissolve"),
    el("button", { title: `Dip through black to ${next.id}`, onclick: () => put("dip", [layer.id, next.id]) }, "Dip"));
  const box = el("div", {}, actions);
  for (const t of existing) {
    box.append(el("div", { class: "row" }, el("span", {}, t.type), el("span", { class: "dim" }, `${t.layers.join(" to ")}, ${t.length}`),
      el("button", { class: "key", title: "Take it off", onclick: () => edit("untransition", { kind: t.type, layer_ids: t.layers }) }, "\u00d7")));
  }
  return box;
}

const PRIMARY = [
  // name, min, max, step, neutral (pivot's neutral is the working space's grey, left unset)
  ["offset", -0.2, 0.2, 0.001, 0], ["contrast", 0.5, 2, 0.01, 1], ["pivot", 0, 1, 0.001, null],
  ["gamma", 0.5, 2, 0.01, 1], ["saturation", 0, 2, 0.01, 1],
];

function gradeSection(layer) {
  if (layer.grade && !Array.isArray(layer.grade)) {
    // A node graph: shown, not edited here yet (lc op grade writes one).
    const box = el("div");
    layer.grade.nodes.forEach((node, k) => box.append(el("p", { class: "dim" },
      `node ${k + 1}: ${node.ops.map((op) => op.op).join(", ")}${node.window?.length ? `, ${node.window.length} window shapes` : ""}` +
      `${node.mix !== undefined && node.mix !== 1 ? `, mix ${node.mix}` : ""}`)));
    box.append(el("div", { class: "actions" },
      el("button", { onclick: () => edit("grade", { layer_id: layer.id, grade: [] }) }, "Remove grade")));
    return box;
  }
  const grade = layer.grade || [];
  const index = grade.findIndex((op) => op.op === "primary");
  const primary = index >= 0 ? grade[index] : { op: "primary" };
  const box = el("div");
  const commit = (name, value) => {
    const next = { ...primary };
    if (value === null) delete next[name]; else next[name] = value;
    const ops = [...grade];
    const empty = Object.keys(next).length === 1;
    if (index >= 0) { if (empty) ops.splice(index, 1); else ops[index] = next; }
    else if (!empty) ops.push(next);
    edit("grade", { layer_id: layer.id, grade: ops });
  };
  for (const [name, min, max, step, neutral] of PRIMARY) {
    const set = typeof primary[name] === "number";
    const shown = set ? primary[name] : neutral ?? 0.336;
    const slider = el("input", { type: "range", min, max, step, value: shown,
      onchange: (e) => commit(name, Number(e.target.value)) });
    const field = el("input", { type: "number", step, value: set ? primary[name] : "", placeholder: neutral === null ? "grey" : String(neutral),
      onchange: (e) => commit(name, e.target.value === "" ? null : Number(e.target.value)) });
    box.append(el("div", { class: "row grade" }, el("span", {}, name), slider, field,
      el("button", { class: "key", title: "Back to neutral", onclick: () => commit(name, null) }, "↺")));
  }
  for (const op of grade.filter((o) => o.op !== "primary")) {
    box.append(el("p", { class: "dim" }, `${op.op}: ${JSON.stringify(op)}`));
  }
  if (grade.length) box.append(el("div", { class: "actions" },
    el("button", { onclick: () => edit("grade", { layer_id: layer.id, grade: [] }) }, "Remove grade")));
  return box;
}

function renderInspector() {
  const box = $("inspector");
  box.innerHTML = "";
  const layer = selectedLayer();
  if (!layer) { box.append(el("p", { class: "dim" }, "Select a layer to see its properties.")); return; }
  box.append(el("h2", {}, `${layer.id}  `, el("span", { class: "dim" }, layer.media || layer.page || "")));
  if (layer.type === "audio") {
    box.append(el("h3", {}, "Level"), propertyRow("gain"), el("p", { class: "dim" }, "gain in dB"));
    for (const side of ["fade_in", "fade_out"]) {
      const field = el("input", { type: "text", value: layer[side] || "", placeholder: "none (b:2, 1.5s)",
        onchange: (e) => edit("set", { layer_id: layer.id, prop: side, value: e.target.value.trim() || null }) });
      box.append(el("div", { class: "row" }, el("span", {}, side.replace("_", " ")), field));
    }
    return;
  }
  if (layer.type === "comp") {
    box.append(el("p", { class: "dim" }, `precomp ${layer.media}: drawn with its footage; its graphics are not drawn in the editor yet`));
    return;
  }
  if (layer.type !== "footage") {
    box.append(el("p", { class: "dim" }, `graphics page ${layer.page}`));
    if (!layer.top) box.append(el("p", { class: "dim" },
      "Under footage: composited in the working space in renders and lc still; the editor does not draw it yet."));
    return;
  }
  box.append(el("h3", {}, "Transform"), ...["position", "scale", "rotation", "opacity"].map(propertyRow));
  box.append(el("h3", {}, "Time"), timeSection(layer));
  box.append(el("h3", {}, "Masks"), maskSection(layer));
  box.append(el("h3", {}, "Transitions"), transitionSection(layer));
  box.append(el("h3", {}, "Grade (primary, working-space codes)"), gradeSection(layer));
}

function inspectorAt() {
  // The playhead moved: the transform values and key buttons follow it.
  const layer = selectedLayer();
  if (!layer || layer.type === "graphics" || document.activeElement?.closest?.("#inspector")) return;
  renderInspector();
}

// ----- scopes -------------------------------------------------------------

let scopeTimer = null;
function scopes() {
  // The engine's scopes of the frame on screen, asked for once the playhead rests.
  const box = $("scopes");
  if (box.hidden || !state.tl || state.playing) return;
  clearTimeout(scopeTimer);
  scopeTimer = setTimeout(async () => {
    const frame = state.frame, name = state.tl.name;
    $("scope-image").src = `/api/comp/${name}/scope.png?frame=${frame}&v=${encodeURIComponent(state.tl.version)}`;
    try {
      const n = await api(`/api/comp/${name}/scope?frame=${frame}`);
      const l = n.luma;
      $("scope-numbers").textContent = `f:${frame} on the proxy; luma p1 ${l.p1} p50 ${l.p50} p99 ${l.p99} (10-bit codes)`;
    } catch (error) {
      $("scope-numbers").textContent = error.message;
    }
  }, 150);
}

// ----- graph editor -------------------------------------------------------

// What the graph editor can show: each a curve on every frame the layer is visible and the
// keys of one property, dragged up or down to change a key's value (`lc op key`).
const GRAPHS = {
  speed: { label: "speed", prop: "remap", lines: [0, 100, 200], unit: "%", fixed: true,
           curve: (c) => c.speed && c.speed.map((v) => v * 100) },
  source: { label: "source time", prop: "remap", unit: "f", curve: (c) => c.source,
            key: (c, k) => k.value * c.media_fps },
  x: { label: "position x", prop: "position", part: 0, lines: [0, 0.5, 1], curve: (c) => c.position && c.position.map((p) => p[0]) },
  y: { label: "position y", prop: "position", part: 1, lines: [0, 0.5, 1], curve: (c) => c.position && c.position.map((p) => p[1]) },
  scale: { label: "scale", prop: "scale", part: 0, lines: [1], curve: (c) => c.scale && c.scale.map((s) => s[0]) },
  opacity: { label: "opacity", prop: "opacity", lines: [0, 1], range: [-0.05, 1.05], clamp: [0, 1], curve: (c) => c.opacity },
  rotation: { label: "rotation", prop: "rotation", lines: [0], unit: "°", digits: 2, curve: (c) => c.rotation },
  gain: { label: "gain", prop: "gain", lines: [0], unit: " dB", digits: 2, curve: (c) => c.gain },
};

function graphModes(layer, c) {
  if (!layer || !c) return [];
  return Object.keys(GRAPHS).filter((name) => (layer.type === "audio") === (name === "gain") && GRAPHS[name].curve(c));
}

function keyValue(mode, c, key) {
  const spec = GRAPHS[mode];
  if (spec.key) return spec.key(c, key);
  if (spec.part !== undefined) return Array.isArray(key.value) ? key.value[spec.part] : key.value;
  return key.value;
}

function graphKeys(mode, c) {
  return c.keys.filter((k) => k.prop === GRAPHS[mode].prop);
}

function buildGraphModes() {
  const layer = selectedLayer(), c = state.curves;
  const modes = graphModes(layer, c);
  if (modes.length && !modes.includes(state.graph)) state.graph = modes[0];
  const box = $("graph-modes");
  box.replaceChildren(...modes.map((name) => {
    const radio = el("input", { type: "radio", name: "graph", value: name });
    radio.checked = name === state.graph;
    radio.onchange = () => { state.graph = name; drawGraph(); };
    return el("label", {}, radio, ` ${GRAPHS[name].label}`);
  }));
}

function drawGraph() {
  const layer = selectedLayer();
  const box = $("graph-box");
  const c = state.curves;
  const modes = graphModes(layer, c);
  box.hidden = !modes.length;
  if (box.hidden) return;
  if (!modes.includes(state.graph)) buildGraphModes();
  const mode = state.graph, spec = GRAPHS[mode];
  const keys = graphKeys(mode, c);
  $("graph-title").textContent = layer.id;
  $("graph-hint").textContent = !spec.fixed && keys.length ? "drag a key up or down to change its value" : "";
  const canvas = $("graph");
  const width = canvas.clientWidth, height = canvas.clientHeight;
  if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
  const g = canvas.getContext("2d");
  g.fillStyle = "#111317"; g.fillRect(0, 0, width, height);
  const values = spec.curve(c);
  let map = graph.drag ? graph.drag.map : null;
  if (!map) {
    const seen = [...values, ...keys.map((k) => keyValue(mode, c, k))];
    let top, bottom;
    if (spec.range) [bottom, top] = spec.range;
    else if (mode === "speed") { top = Math.max(200, ...seen) * 1.05; bottom = Math.min(0, ...seen); }
    else {
      top = Math.max(...seen); bottom = Math.min(...seen);
      const margin = (top - bottom) * 0.1 || Math.abs(top) * 0.1 || 1;
      top += margin; bottom -= margin;
    }
    const pad = 30;
    map = { top, bottom, pad, width, height,
            x: (frame) => pad + (frame - c.first) / Math.max(1, c.end - c.first) * (width - 2 * pad),
            y: (v) => height - 10 - (v - bottom) / (top - bottom || 1) * (height - 20) };
  }
  graph.map = map;
  const { x, y } = map;
  const unit = spec.unit || "";
  g.font = "10px -apple-system, sans-serif";
  g.strokeStyle = "#2a2f38"; g.fillStyle = "#8a919d";
  for (const v of spec.lines || []) {
    if (v < map.bottom || v > map.top) continue;
    g.beginPath(); g.moveTo(map.pad, y(v) + 0.5); g.lineTo(width - map.pad, y(v) + 0.5); g.stroke();
    g.fillText(`${v}${unit}`, 2, y(v) + 3);
  }
  if (!spec.lines) { g.fillText(`${round(map.top, 1)}${unit}`, 2, 12); g.fillText(`${round(map.bottom, 1)}${unit}`, 2, height - 4); }
  g.strokeStyle = mode === "speed" ? "#ffb547" : "#7fd1ff"; g.lineWidth = 1.5;
  g.beginPath();
  values.forEach((v, k) => { const px = x(c.first + k), py = y(v); if (k) g.lineTo(px, py); else g.moveTo(px, py); });
  g.stroke(); g.lineWidth = 1;
  for (const key of keys) {
    const kx = x(key.frame);
    const dragged = graph.drag && graph.drag.key === key;
    const ky = spec.fixed ? y(100) : y(dragged ? graph.drag.value : keyValue(mode, c, key));
    g.fillStyle = dragged ? "#ffffff" : "#7fd1ff";
    g.beginPath(); g.moveTo(kx, ky - 5); g.lineTo(kx + 5, ky); g.lineTo(kx, ky + 5); g.lineTo(kx - 5, ky); g.closePath(); g.fill();
  }
  g.strokeStyle = "#ffffff";
  const px = x(state.frame);
  if (px >= map.pad && px <= width - map.pad) { g.beginPath(); g.moveTo(px + 0.5, 0); g.lineTo(px + 0.5, height); g.stroke(); }
  if (graph.drag) {
    g.fillStyle = "#ffffff";
    g.fillText(`${round(graph.drag.value, spec.digits || 4)}${unit}`, x(graph.drag.key.frame) + 8, y(graph.drag.value) - 6);
  }
}
const graph = { map: null, drag: null };

function draggedValue(mode, key, value) {
  // The key's new value as `lc op key` takes it, in the form the key already has.
  const spec = GRAPHS[mode], c = state.curves;
  if (mode === "source") return sourceLiteral(value, c.media_fps);
  let v = round(value, spec.digits || 4);
  if (spec.clamp) v = Math.min(spec.clamp[1], Math.max(spec.clamp[0], v));
  if (spec.part === undefined) return v;
  if (!Array.isArray(key.value)) return v;
  if (mode === "scale") {  // both axes by the same factor, so the layer keeps its shape
    const [sx, sy] = key.value;
    return sx ? [v, round(sy * v / sx, 4)] : [v, sy];
  }
  const pair = [...key.value];
  pair[spec.part] = v;
  return pair;
}

$("graph").addEventListener("mousedown", (event) => {
  const c = state.curves, m = graph.map, mode = state.graph;
  if (!c || !m || !GRAPHS[mode]) return;
  const rect = $("graph").getBoundingClientRect();
  const px = event.clientX - rect.left, py = event.clientY - rect.top;
  const key = GRAPHS[mode].fixed ? null : graphKeys(mode, c)
    .find((k) => Math.abs(m.x(k.frame) - px) < 7 && Math.abs(m.y(keyValue(mode, c, k)) - py) < 7);
  if (key) graph.drag = { key, value: keyValue(mode, c, key), map: m };
  else show(Math.round(c.first + (px - m.pad) / (m.width - 2 * m.pad) * (c.end - c.first)));
});
window.addEventListener("mousemove", (event) => {
  if (!graph.drag) return;
  const m = graph.drag.map;
  const py = event.clientY - $("graph").getBoundingClientRect().top;
  let value = m.bottom + (m.height - 10 - py) / (m.height - 20) * (m.top - m.bottom);
  if (state.graph === "source") value = Math.max(0, event.altKey ? value : Math.round(value));  // whole source frames unless Alt
  graph.drag.value = value;
  drawGraph();
});
window.addEventListener("mouseup", () => {
  if (!graph.drag) return;
  const { key, value } = graph.drag;
  const mode = state.graph;
  graph.drag = null;
  if (Math.abs(value - keyValue(mode, state.curves, key)) < 1e-6) return drawGraph();
  edit("key", { layer_id: state.selected, prop: GRAPHS[mode].prop, at: key.at, value: draggedValue(mode, key, value),
                ease: key.ease });
});

// ----- loading ------------------------------------------------------------

function setTimeline(tl) {
  const sameComp = state.tl && state.tl.name === tl.name;
  state.tl = tl;
  const picture = $("picture");
  picture.width = tl.preview[0];
  picture.height = tl.preview[1];
  const box = $("viewer");
  const fit = Math.min(box.clientWidth / tl.preview[0], box.clientHeight / tl.preview[1], 2);
  for (const el of [picture, $("overlays"), $("stage")]) {
    el.style.width = `${tl.preview[0] * fit}px`;
    el.style.height = `${tl.preview[1] * fit}px`;
  }
  $("overlays").style.transform = `scale(${fit})`;
  $("overlays").style.transformOrigin = "0 0";
  $("overlays").style.width = `${tl.preview[0]}px`;
  $("overlays").style.height = `${tl.preview[1]}px`;
  if (!sameComp || state.graphics.size !== tl.layers.filter((l) => l.type === "graphics").length) buildGraphics();
  else for (const layer of tl.layers.filter((l) => l.type === "graphics")) {
    const entry = state.graphics.get(layer.id);
    if (entry) entry.layer = layer;
  }
  if (state.selected && !tl.layers.some((l) => l.id === state.selected)) state.selected = null;
  show(Math.min(state.frame, tl.frames - 1));
  inspect();
}

async function loadComp(name, keepFrame = false) {
  const tl = await api(`/api/comp/${name}`);
  if (!keepFrame) state.frame = 0;
  setTimeline(tl);
}

async function boot() {
  state.project = await api("/api/project");
  $("title").textContent = `lungecuta  ${state.project.directory.split("/").pop()}`;
  const select = $("comp");
  for (const name of state.project.comps) {
    const option = document.createElement("option");
    option.value = option.textContent = name;
    select.appendChild(option);
  }
  select.onchange = () => loadComp(select.value);
  // Media that can be placed: decided footage and music (an undecided clip cannot be).
  const media = $("media");
  for (const item of state.project.media) {
    if (!item.music && item.color !== "decided") continue;
    const option = document.createElement("option");
    option.value = item.key;
    option.textContent = `${item.key}${item.music ? " (music)" : ""}`;
    media.appendChild(option);
  }
  $("place").onclick = () => media.value && edit("place", { media: media.value, at: `f:${state.frame}` });
  state.styles = await api("/api/styles");
  connect();
  if (state.project.comps.length) await loadComp(state.project.comps[0]);
  else status("this project has no comps yet");
  journal();
}

$("timeline").addEventListener("mousedown", onDown);
window.addEventListener("mousemove", onMove);
window.addEventListener("mouseup", onUp);
$("play").onclick = play;
$("scope-toggle").onclick = () => { $("scopes").hidden = !$("scopes").hidden; scopes(); };
$("undo").onclick = () => step("undo");
$("redo").onclick = () => step("redo");
window.addEventListener("resize", () => state.tl && setTimeline(state.tl));
window.addEventListener("keydown", (event) => {
  if (!state.tl || ["SELECT", "INPUT"].includes(event.target.tagName)) return;
  const beats = state.tl.beats;
  if (event.key === " ") { event.preventDefault(); play(); }
  else if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
    event.preventDefault();
    const forward = event.key === "ArrowRight";
    if (event.shiftKey && beats.length) {
      const next = forward ? beats.find((b) => b.frame > state.frame + 0.5) : [...beats].reverse().find((b) => b.frame < state.frame - 0.5);
      if (next) show(Math.ceil(next.frame));
    } else show(state.frame + (forward ? 1 : -1));
  } else if (event.key === "Home") show(0);
  else if (event.key === "End") show(state.tl.frames - 1);
  else if (event.key === "+" || event.key === "=") { state.zoom = Math.min(40, state.zoom * 1.5); drawTimeline(); }
  else if (event.key === "-") { state.zoom = Math.max(0.25, state.zoom / 1.5); drawTimeline(); }
  else if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "z") { event.preventDefault(); step(event.shiftKey ? "redo" : "undo"); }
  else if (event.key === "s" && state.selected) edit("split", { layer_id: state.selected, at: `f:${state.frame}` });
  else if ((event.key === "Delete" || event.key === "Backspace") && state.selected) {
    edit("ripple-delete", { layer_id: state.selected });
    state.selected = null;
  }
});
boot();
