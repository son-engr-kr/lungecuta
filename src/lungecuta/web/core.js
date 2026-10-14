// lungecuta's editor (docs/design/06-editing.md): what every part shares, the state, the
// server and its socket, frames asked for and drawn, the beat grid, graphics pages, and
// what is on screen reported for the agent.
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
  marks: { in: null, out: null },  // the range marked with I and O, comp frames, both included
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
    // A file changed underneath the editor: the agent's edit, or another tool's. The comp
    // on screen already read at that hash (a reload got there first) needs nothing.
    if (state.tl && m.file === `comps/${state.tl.name}.json` && m.hash === state.tl.hash) return;
    status(`${m.file} changed on disk; reloading`);
    if (m.file.startsWith("styles/")) api("/api/styles").then((styles) => { state.styles = styles; renderInspector(); });
    // any comp, since the one shown may place it as a precomp
    if (state.tl && (m.file === "project.json"
                     || ["comps/", "media/", "styles/", "luts/"].some((prefix) => m.file.startsWith(prefix)))) {
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
  const { in: from, out: to } = state.marks;
  $("marks").textContent = from === null && to === null ? "" : `marked ${from === null ? "start" : `f:${from}`} to ${to === null ? "end" : `f:${to}`}`;
  reportView();
}

// What is on screen goes to the server once the playhead rests, so the agent sees what
// "here", "this shot" and "this part" mean (lungecuta changes; view.py).
let viewTimer = null;
function reportView() {
  clearTimeout(viewTimer);
  viewTimer = setTimeout(() => {
    if (!state.tl) return;
    api("/api/view", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ comp: state.tl.name, frame: state.frame, selected: state.selected,
                             in: state.marks.in, out: state.marks.out }) })
      .catch((error) => status(error.message, true));
  }, 200);
}

function mark(which) {
  const marks = state.marks;
  marks[which] = state.frame;
  // A mark set past the other one starts a new range, as in an editor's in and out points.
  if (marks.in !== null && marks.out !== null && marks.in > marks.out) marks[which === "in" ? "out" : "in"] = null;
  readout();
  drawTimeline();
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
    frame.src = `${state.project.graphics_origin}/__lungecuta_frame.html?${query}`;
    frame.width = cw;
    frame.height = ch;
    frame.style.transform = `scale(${pw / cw})`;
    frame.hidden = true;
    box.appendChild(frame);
    state.graphics.set(layer.id, { frame, layer });
  }
}

window.addEventListener("message", (event) => {
  if (event.data && event.data.lungecuta === "ready") { state.ready.add(event.data.id); graphicsAt(state.frame); }
});

function graphicsAt(frame) {
  // The editor draws web layers live over the engine's frames (01-architecture.md, Preview).
  for (const { frame: iframe, layer } of state.graphics.values()) {
    // A page under footage is composited in the working space by the engine, which the
    // preview does not capture; drawing it over everything would show the wrong stack.
    const visible = layer.top && frame >= layer.first && frame < layer.end;
    iframe.hidden = !visible;
    if (visible) iframe.contentWindow.postMessage({ lungecuta: "at", t: (frame - layer.start) / state.tl.fps }, "*");
  }
}
