// lungecuta's editor, started: loading the project and its comps, the controls, the keys.
// The other parts are classic scripts loaded before this one, in index.html's order,
// sharing their top-level names (core.js, timeline.js, inspector.js, graph.js).
"use strict";

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
  loadPeaks(tl);
}

// Every request that brings a timeline back takes a number, and only the latest is shown:
// two changes on disk in quick succession send two reloads, whose answers can arrive the
// wrong way round, and an older timeline shown over a newer one has every edit refused.
let timelineAsks = 0;

async function loadComp(name, keepFrame = false) {
  const asked = ++timelineAsks;
  const tl = await api(`/api/comp/${name}`);
  if (asked !== timelineAsks) return;
  if (!keepFrame) { state.frame = 0; state.marks = { in: null, out: null }; }
  setTimeline(tl);
}

async function loadPeaks(tl) {
  // The waveforms come after the timeline: reading every audio layer's sound takes longer.
  if (state.peaks.hash === tl.hash) return;
  const peaks = await api(`/api/comp/${tl.name}/peaks`);
  if (state.tl.hash !== tl.hash) return;
  state.peaks = { hash: tl.hash, layers: peaks.layers };
  drawTimeline();
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
  else if (event.code === "KeyX" && event.altKey) { state.marks = { in: null, out: null }; readout(); drawTimeline(); }
  else if (event.key === "i" || event.key === "o") mark(event.key === "i" ? "in" : "out");
  else if ((event.key === "Delete" || event.key === "Backspace") && state.selected) {
    edit("ripple-delete", { layer_id: state.selected });
    state.selected = null;
  }
});
boot();
