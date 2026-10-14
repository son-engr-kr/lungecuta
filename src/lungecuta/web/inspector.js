// The inspector: the selected layer's transform, motion blur, time, grade, masks and
// transitions, each change an edit; and the scopes of the frame on screen.
"use strict";

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

const DEFAULTS = { position: [0.5, 0.5], scale: [1, 1], opacity: 1, rotation: 0, gain: 0, shutter: 0 };

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
  const single = ["opacity", "rotation", "gain", "shutter"].includes(prop);
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
  if (single) inputs.push(number(value, prop === "opacity" ? 0.01 : prop === "shutter" ? 15 : 0.5, apply));
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
  if (!layer.style) {
    // The range marked with I and O at one speed, the rest of the layer keeping its own (lungecuta op speed).
    const percent = el("input", { type: "number", step: 5, value: 50, title: "speed in %" });
    actions.append(percent, el("button", { title: "Play the range marked with I and O at this speed",
      onclick: () => {
        const { in: from, out: to } = state.marks;
        if (from === null && to === null) return status("mark a range with I and O first", true);
        edit("speed", { layer_id: layer.id, factor: Number(percent.value) / 100,
                        start: `f:${from ?? 0}`, end: `f:${(to ?? state.tl.frames - 1) + 1}` });
      } }, "% over marked range"));
  }
  const timed = state.styles.filter((st) => st.tracks);  // a look alone has nothing to place in time
  if (!layer.remapped && (layer.speed === 1 || layer.style) && timed.length && state.tl.beats.length) {
    const select = el("select", {}, ...timed.map((st) => el("option", { value: st.id }, st.name)));
    const intensity = el("input", { type: "number", step: 0.05, title: "intensity" });
    const param = () => timed.find((st) => st.id === select.value)?.params?.intensity;
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

function soundSection(layer) {
  // The clip's own sound, played with its picture (lungecuta op sound).
  const box = el("div"), actions = el("div", { class: "actions" });
  if (layer.sound) {
    const gain = el("input", { type: "number", step: 1, value: layer.sound.gain ?? 0, title: "gain in dB",
      onchange: (e) => edit("sound", { layer_id: layer.id, gain: Number(e.target.value) }) });
    box.append(el("div", { class: "row" }, el("span", {}, "gain dB"), gain));
    actions.append(el("button", { onclick: () => edit("sound", { layer_id: layer.id, on: false }) }, "Mute its sound"));
  } else {
    box.append(el("p", { class: "dim" }, "silent: its own sound is off"));
    actions.append(el("button", { onclick: () => edit("sound", { layer_id: layer.id }) }, "Play its sound"));
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

function looksRow(layer) {
  // The looks (styles holding a grade) the project has, put on the layer as a linked node.
  const looks = (state.styles || []).filter((style) => style.grade);
  if (!looks.length) return el("p", { class: "dim" }, "no looks yet: a style in styles/ with a grade is one");
  const select = el("select", {}, ...looks.map((style) => el("option", { value: style.id }, `${style.id}: ${style.name}`)));
  const mix = el("input", { type: "number", min: 0, max: 1, step: 0.05, value: 1, title: "mix" });
  return el("div", { class: "row" }, el("span", {}, "look"), select, mix,
    el("button", { onclick: () => edit("look", { layer_id: layer.id, look_id: select.value, mix: Number(mix.value) }) },
       "Add look"));
}

function gradeSection(layer) {
  if (layer.grade && !Array.isArray(layer.grade)) {
    // A node graph: shown, its looks added and taken off here (lungecuta op grade writes the rest).
    const box = el("div");
    layer.grade.nodes.forEach((node, k) => {
      const what = node.look ? `look ${node.look}` : node.ops.map((op) => op.op).join(", ");
      const line = el("p", { class: "dim" }, `node ${k + 1}: ${what}${node.window?.length ? `, ${node.window.length} window shapes` : ""}` +
        `${node.mix !== undefined && node.mix !== 1 ? `, mix ${node.mix}` : ""}`);
      if (node.look) line.append(" ", el("button", { onclick: () => edit("unlook", { layer_id: layer.id, look_id: node.look }) },
                                           "Remove"));
      box.append(line);
    });
    box.append(looksRow(layer));
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
  box.append(looksRow(layer));
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
      "Under footage: composited in the working space in renders and lungecuta still; the editor does not draw it yet."));
    return;
  }
  box.append(el("h3", {}, "Transform"), ...["position", "scale", "rotation", "opacity"].map(propertyRow));
  box.append(el("h3", {}, "Motion blur"), propertyRow("shutter"),
             el("p", { class: "dim" }, "shutter in degrees of a frame: the layer's own movement over it, averaged"));
  box.append(el("h3", {}, "Time"), timeSection(layer));
  if (state.project.media.find((m) => m.key === layer.media)?.audio) box.append(el("h3", {}, "Sound"), soundSection(layer));
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
