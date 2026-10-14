// The graph editor: a property of the selected layer on every frame, its keys dragged
// in value or time, and the speed graph's segments dragged to another speed.
"use strict";

// ----- graph editor -------------------------------------------------------

// What the graph editor can show: each a curve on every frame the layer is visible and the
// keys of one property, dragged up or down to change a key's value (`lungecuta op key`).
const GRAPHS = {
  speed: { label: "speed", prop: "remap", lines: [0, 100, 200], unit: "%", fixed: true, segments: true,
           curve: (c) => c.speed && c.speed.map((v) => v * 100) },
  source: { label: "source time", prop: "remap", unit: "f", curve: (c) => c.source,
            key: (c, k) => k.value * c.media_fps },
  x: { label: "position x", prop: "position", part: 0, lines: [0, 0.5, 1], curve: (c) => c.position && c.position.map((p) => p[0]) },
  y: { label: "position y", prop: "position", part: 1, lines: [0, 0.5, 1], curve: (c) => c.position && c.position.map((p) => p[1]) },
  scale: { label: "scale", prop: "scale", part: 0, lines: [1], curve: (c) => c.scale && c.scale.map((s) => s[0]) },
  opacity: { label: "opacity", prop: "opacity", lines: [0, 1], range: [-0.05, 1.05], clamp: [0, 1], curve: (c) => c.opacity },
  rotation: { label: "rotation", prop: "rotation", lines: [0], unit: "°", digits: 2, curve: (c) => c.rotation },
  gain: { label: "gain", prop: "gain", lines: [0], unit: " dB", digits: 2, curve: (c) => c.gain },
  shutter: { label: "shutter", prop: "shutter", lines: [0, 180, 360], unit: "°", digits: 1, range: [-10, 370],
             clamp: [0, 720], curve: (c) => c.shutter },
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
  $("graph-hint").textContent = spec.segments ? "drag a segment up or down to change its speed; the segments after it keep theirs"
    : !spec.fixed && keys.length ? "drag a key up or down to change its value, across to move it" : "";
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
    const dragged = graph.drag && graph.drag.key === key;
    const kx = x(dragged ? graph.drag.frame : key.frame);
    const ky = spec.fixed ? y(100) : y(dragged ? graph.drag.value : keyValue(mode, c, key));
    g.fillStyle = dragged ? "#ffffff" : "#7fd1ff";
    g.beginPath(); g.moveTo(kx, ky - 5); g.lineTo(kx + 5, ky); g.lineTo(kx, ky + 5); g.lineTo(kx - 5, ky); g.closePath(); g.fill();
  }
  g.strokeStyle = "#ffffff";
  const px = x(state.frame);
  if (px >= map.pad && px <= width - map.pad) { g.beginPath(); g.moveTo(px + 0.5, 0); g.lineTo(px + 0.5, height); g.stroke(); }
  if (graph.drag && graph.drag.segment) {
    const [f0, f1] = graph.drag.segment;
    g.strokeStyle = "#ffffff"; g.lineWidth = 2;
    g.beginPath(); g.moveTo(x(f0), y(graph.drag.value)); g.lineTo(x(f1), y(graph.drag.value)); g.stroke(); g.lineWidth = 1;
  }
  if (graph.drag) {
    g.fillStyle = "#ffffff";
    const label = graph.drag.along === "time" ? `f:${graph.drag.frame}` : `${round(graph.drag.value, spec.digits || 4)}${unit}`;
    g.fillText(label, x(graph.drag.frame) + 8, y(graph.drag.value) - 6);
  }
}
const graph = { map: null, drag: null };

function segmentAround(c, frame) {
  // The remap segment holding a frame, as comp frames: between two remap keys, or the
  // whole layer before it is remapped (lungecuta op speed remaps it first).
  const at = c.keys.filter((k) => k.prop === "remap").map((k) => k.frame);
  if (at.length < 2) return [c.first, c.end];
  const k = at.findIndex((f, i) => i + 1 < at.length && f <= frame && frame < at[i + 1]);
  return k < 0 ? null : [at[k], at[k + 1]];
}

function draggedValue(mode, key, value) {
  // The key's new value as `lungecuta op key` takes it, in the form the key already has.
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
  const frame = Math.round(c.first + (px - m.pad) / (m.width - 2 * m.pad) * (c.end - c.first));
  if (GRAPHS[mode].segments && frame >= c.first && frame < c.end) {
    const value = c.speed[frame - c.first] * 100, segment = segmentAround(c, frame);
    if (segment && Math.abs(m.y(value) - py) < 8) {
      graph.drag = { segment, frame, value, start: value, map: m, from: [px, py], along: null };
      return drawGraph();
    }
  }
  const key = GRAPHS[mode].fixed ? null : graphKeys(mode, c)
    .find((k) => Math.abs(m.x(k.frame) - px) < 7 && Math.abs(m.y(keyValue(mode, c, k)) - py) < 7);
  // The first few pixels of movement decide: across moves the key in time, up or down
  // changes its value.
  if (key) graph.drag = { key, value: keyValue(mode, c, key), map: m, from: [px, py], along: null, frame: key.frame };
  else show(frame);
});
window.addEventListener("mousemove", (event) => {
  if (!graph.drag) return;
  const drag = graph.drag, m = drag.map, c = state.curves;
  const rect = $("graph").getBoundingClientRect();
  const px = event.clientX - rect.left, py = event.clientY - rect.top;
  if (drag.along === null) {
    const dx = Math.abs(px - drag.from[0]), dy = Math.abs(py - drag.from[1]);
    if (Math.max(dx, dy) < 4) return;
    drag.along = drag.segment || dy >= dx ? "value" : "time";  // a segment only moves up or down
  }
  if (drag.along === "time") {
    const frame = Math.round(c.first + (px - m.pad) / (m.width - 2 * m.pad) * (c.end - c.first));
    drag.frame = Math.max(c.first, Math.min(c.end - 1, frame));  // whole frames, inside the layer
  } else {
    let value = m.bottom + (m.height - 10 - py) / (m.height - 20) * (m.top - m.bottom);
    if (state.graph === "source") value = Math.max(0, event.altKey ? value : Math.round(value));  // whole source frames unless Alt
    if (drag.segment && !event.altKey) value = Math.round(value);  // whole percent unless Alt
    drag.value = value;
  }
  drawGraph();
});
window.addEventListener("mouseup", () => {
  if (!graph.drag) return;
  const { key, value, along, frame, segment, start } = graph.drag;
  const mode = state.graph;
  graph.drag = null;
  if (segment) {
    if (along === null || Math.abs(value - start) < 1e-6) return drawGraph();
    return edit("speed", { layer_id: state.selected, factor: round(value / 100, 4), at: `f:${frame}` });
  }
  if (along === "time") {
    if (Math.abs(frame - key.frame) < 0.5) return drawGraph();
    return edit("move-key", { layer_id: state.selected, prop: GRAPHS[mode].prop, at: key.at, to: `f:${frame}` });
  }
  if (along === null || Math.abs(value - keyValue(mode, state.curves, key)) < 1e-6) return drawGraph();
  edit("key", { layer_id: state.selected, prop: GRAPHS[mode].prop, at: key.at, value: draggedValue(mode, key, value),
                ease: key.ease });
});
