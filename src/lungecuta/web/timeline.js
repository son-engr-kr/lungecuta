// The timeline: layers, the beat grid, markers and the marked range drawn on a canvas;
// dragging layers and their edges into edits; playback against the audio clock.
"use strict";

// ----- timeline -----------------------------------------------------------

function rows() {
  // The layers from the top of the stack down: the last layer listed first.
  return [...state.tl.layers].sort((a, b) => b.row - a.row);
}

let packed = { tl: null, row: new Map(), count: 0 };
function layout() {
  // Layers packed into tracks, as an editor's video track holds its shots: a layer goes on
  // the track above the highest one holding an earlier layer it overlaps in time, so what
  // is drawn over what still reads from the top down, and layers that never overlap share
  // a track. Pictures on top, sound on its own tracks below them.
  if (packed.tl === state.tl) return packed;
  const order = [...state.tl.layers].sort((a, b) => a.row - b.row);  // bottom of the stack first
  const overlaps = (a, b) => a.first < b.end && b.first < a.end;
  const pack = (layers) => {
    const track = new Map();
    layers.forEach((layer, k) => {
      const under = layers.slice(0, k).filter((other) => overlaps(other, layer)).map((other) => track.get(other.id));
      track.set(layer.id, under.length ? Math.max(...under) + 1 : 0);
    });
    return track;
  };
  const picture = pack(order.filter((l) => l.type !== "audio")), sound = pack(order.filter((l) => l.type === "audio"));
  const top = picture.size ? Math.max(...picture.values()) + 1 : 0;
  const row = new Map();
  for (const [id, track] of picture) row.set(id, top - 1 - track);  // the highest track drawn first
  for (const [id, track] of sound) row.set(id, top + track);
  packed = { tl: state.tl, row, count: top + (sound.size ? Math.max(...sound.values()) + 1 : 0) };
  return packed;
}

const thumbs = new Map();  // thumb key -> its Image, which redraws the timeline once loaded
function thumbnail(layer) {
  if (!layer.thumb) return null;
  let image = thumbs.get(layer.thumb);
  if (!image) {
    image = new Image();
    image.onload = () => drawTimeline();
    image.src = `/api/comp/${state.tl.name}/layer/${encodeURIComponent(layer.id)}/thumb.png?key=${layer.thumb}`;
    thumbs.set(layer.thumb, image);
  }
  return image.complete && image.naturalWidth ? image : null;
}

function rowOf(id) {
  return layout().row.get(id);
}

function drawTimeline() {
  const tl = state.tl;
  const canvas = $("timeline");
  const layers = rows();
  const width = Math.max(canvas.parentElement.clientWidth, Math.ceil(tl.frames * state.zoom) + 40);
  const height = RULER + GRID + layout().count * ROW + 8;
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
  layers.forEach((layer) => {
    const y = RULER + GRID + rowOf(layer.id) * ROW;
    if (layer.id === state.selected) { g.fillStyle = "rgba(255,255,255,0.06)"; g.fillRect(0, y, width, ROW); }
    const x0 = x(layer.first), x1 = x(layer.end);
    g.fillStyle = colours[layer.type];
    g.globalAlpha = layer.id === state.selected ? 1 : 0.8;
    g.fillRect(x0, y + 3, Math.max(2, x1 - x0), ROW - 6);
    g.globalAlpha = 1;
    g.fillStyle = "#111317"; g.fillRect(x0, y + 3, 1, ROW - 6);  // a cut stays visible between shots on one track
    if (layer.id === state.selected) { g.strokeStyle = "#ffffff"; g.strokeRect(x0 + 0.5, y + 3.5, Math.max(2, x1 - x0) - 1, ROW - 7); }
    const wave = state.peaks.hash === tl.hash ? state.peaks.layers[layer.id] : null;  // audio, or a clip's own sound
    if (wave) {
      // The layer's waveform: its largest magnitude on each frame, about the row's middle.
      g.fillStyle = "rgba(255,255,255,0.32)";
      const middle = y + ROW / 2, half = (ROW - 8) / 2, w = Math.max(1, state.zoom);
      wave.peaks.forEach((p, k) => { const h = Math.min(1, p) * half; g.fillRect(x(wave.first + k), middle - h, w, 2 * h); });
    }
    const speed = layer.remapped ? "  remapped" : layer.speed !== 1 ? `  ${Math.round(layer.speed * 100)}%` : "";
    const label = layer.type === "footage" ? `${layer.id}  ${layer.media}${speed}` : `${layer.id}  ${layer.media || layer.page || ""}`;
    g.save(); g.beginPath(); g.rect(x0, y, Math.max(0, x1 - x0), ROW); g.clip();
    // The shot's first frame at the left of its box, where it is wide enough to show it.
    const thumb = layer.type === "footage" ? thumbnail(layer) : null;
    let text = x0 + 5;
    if (thumb && x1 - x0 > 40) {
      g.drawImage(thumb, x0 + 1, y + 3, thumb.width, ROW - 6);
      text = x0 + thumb.width + 5;
    }
    g.fillStyle = "#f2f4f7";
    g.fillText(label, text, y + 19); g.restore();
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
    const row = marker.layer ? rowOf(marker.layer) : -1;
    const my = row < 0 ? RULER - 2 : RULER + GRID + row * ROW + 4;
    g.fillStyle = "#ff6b5b";
    g.beginPath(); g.moveTo(mx, my); g.lineTo(mx - 5, my - 7); g.lineTo(mx + 5, my - 7); g.closePath(); g.fill();
  }
  // The marked range, from the in mark's frame to the end of the out mark's.
  const { in: from, out: to } = state.marks;
  if (from !== null || to !== null) {
    const x0 = x(from ?? 0), x1 = x((to ?? tl.frames - 1) + 1);
    g.fillStyle = "rgba(120,190,255,0.10)";
    g.fillRect(x0, 0, x1 - x0, height);
    g.fillStyle = "rgba(120,190,255,0.85)";
    g.fillRect(x0, 0, x1 - x0, 3);
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
  // The layer on that row holding the frame, else the nearest edge within two pixels (at a
  // cut between two shots on one track, the one the pointer is inside).
  const near = 2 / state.zoom;
  const onRow = state.tl.layers.filter((l) => rowOf(l.id) === k && frame >= l.first - near && frame <= l.end + near);
  const layer = onRow.find((l) => frame >= l.first && frame < l.end) || onRow[0];
  if (!layer) return { where: "empty", frame };
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
  const y = RULER + GRID + rowOf(drag.layer.id) * ROW;
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
    const asked = ++timelineAsks;
    const result = await api("/api/edit", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ comp: state.tl.name, op, args, expect: state.tl.hash }),
    });
    if (asked === timelineAsks) setTimeline(result.timeline);
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
