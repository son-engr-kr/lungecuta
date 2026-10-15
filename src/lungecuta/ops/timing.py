"""Operations on when layers play: move, trim, slip, roll, slide, ripple delete, split,
snap to beats, and placing media (06-editing.md#edit-operations)."""
from __future__ import annotations

import re
from fractions import Fraction
from pathlib import Path
from typing import Any

from ..literals import (Field, Frames, Seconds, format_literal, parse_literal, resolve_position, resolve_source,
                        resolve_span, span_literal)
from ..files import write_atomic
from ..geometry import shown_size
from ..media import Project, load_record
from ..ticks import TICKS_PER_SECOND, first_frame_at_or_after, parse_rate, ticks_per_frame
from .edit import Edit, _delta, _resolves, span_text


def move(project: Project, name: str, layer_id: str, by: str, expect: str | None = None) -> dict[str, Any]:
    """Move a layer in the comp: its start changes, and everything in layer time (keys,
    in and out points, markers) comes along."""
    edit = Edit.open(project, name, expect)
    layer = edit.layer(layer_id)
    start = edit.start(layer)
    edit.set_start(layer, start + _delta(edit, by, start))
    return edit.save()


def trim(project: Project, name: str, layer_id: str, side: str, by: str, expect: str | None = None) -> dict[str, Any]:
    """Move a layer's in or out point by `by`; the start, the keys and the source stay."""
    assert side in ("in", "out"), f"a trim moves the in or the out point, not {side!r}"
    edit = Edit.open(project, name, expect)
    layer = edit.layer(layer_id)
    point = edit.start(layer) + edit.span(layer, side)
    moved = point + _delta(edit, by, point)
    edit.set_span(layer, side, moved - edit.start(layer), given=by)
    assert edit.span(layer, "in") <= edit.span(layer, "out"), f"{layer_id}: the trim puts in after out"
    return edit.save()


def slip(project: Project, name: str, layer_id: str, by: str, expect: str | None = None) -> dict[str, Any]:
    """Move the content under a layer's fixed in and out points: its source start
    changes by `by` of source time (f: at the media's rate, or seconds)."""
    edit = Edit.open(project, name, expect)
    layer = edit.layer(layer_id)
    rate = edit.media_rate(layer)
    literal = parse_literal(by, Field.STYLE)
    assert isinstance(literal, Frames | Seconds), f"a slip is in source frames or seconds, not {by}"
    amount = literal.count * ticks_per_frame(rate) if isinstance(literal, Frames) else literal.ticks
    source = resolve_source(parse_literal(layer["source_start"], Field.SOURCE_START), media_rate=rate)
    edit.set_source(layer, source + amount)
    _resolves(project, edit)  # every frame it shows is still in the clip
    return edit.save()


def roll(project: Project, name: str, left_id: str, right_id: str, by: str, expect: str | None = None) -> dict[str, Any]:
    """Move the cut between two adjacent layers: the left one's out point and the right
    one's in point move together."""
    edit = Edit.open(project, name, expect)
    left, right = edit.layer(left_id), edit.layer(right_id)
    cut = edit.start(left) + edit.span(left, "out")
    assert cut == edit.start(right) + edit.span(right, "in"), f"{left_id} and {right_id} do not meet at a cut"
    moved = cut + _delta(edit, by, cut)
    edit.set_span(left, "out", moved - edit.start(left), given=by)
    edit.set_span(right, "in", moved - edit.start(right), given=by)
    for layer in (left, right):
        assert edit.span(layer, "in") <= edit.span(layer, "out"), f"{layer['id']}: the roll puts in after out"
    return edit.save()


def slide(project: Project, name: str, layer_id: str, by: str, expect: str | None = None) -> dict[str, Any]:
    """Slide a footage layer between its neighbours: it moves as a whole (its content and
    keys come along), the layer that ends where it began has its out point moved with it,
    and the one that begins where it ended has its in point moved with it, so no gap
    opens and nothing overlaps. One amount in ticks, counted from the layer's in point,
    moves all three, so the cuts stay exactly shared."""
    edit = Edit.open(project, name, expect)
    layer = edit.layer(layer_id)
    assert layer["type"] == "footage", f"{layer_id} is not a footage layer"
    start = edit.start(layer)
    begins, ends = start + edit.span(layer, "in"), start + edit.span(layer, "out")
    delta = _delta(edit, by, begins)
    footage = [other for other in edit.data["layers"] if other["type"] == "footage" and other is not layer]
    before = [o for o in footage if edit.start(o) + edit.span(o, "out") == begins]
    after = [o for o in footage if edit.start(o) + edit.span(o, "in") == ends]
    assert before or after, f"{layer_id} meets no other footage layer at a cut; move it instead"
    for other in before:
        edit.set_span(other, "out", begins + delta - edit.start(other), given=by)
    for other in after:
        assert ends + delta >= edit.start(other), (
            f"{other['id']}'s in point would move before its start, where its layer time begins; "
            f"move {other['id']} earlier first"
        )
        edit.set_span(other, "in", ends + delta - edit.start(other), given=by)
    edit.set_start(layer, start + delta)
    for other in before + after:
        assert edit.span(other, "in") <= edit.span(other, "out"), f"{other['id']}: the slide puts its in after its out"
    _resolves(project, edit)
    return edit.save()


def ripple_delete(project: Project, name: str, layer_id: str, expect: str | None = None) -> dict[str, Any]:
    """Remove a layer and close the gap: every layer that starts at or after its out point
    moves earlier by its visible length."""
    edit = Edit.open(project, name, expect)
    layer = edit.layer(layer_id)
    visible_in = edit.start(layer) + edit.span(layer, "in")
    visible_out = edit.start(layer) + edit.span(layer, "out")
    length = visible_out - visible_in
    edit.data["layers"] = [other for other in edit.data["layers"] if other["id"] != layer_id]
    edit.changes.append(f"{layer_id} removed")
    for other in edit.data["layers"]:
        if other["type"] == "audio" and edit.comp.beats and other["id"] in edit.comp.beats.from_layers:
            continue  # the music the grid comes from stays where it is
        if edit.start(other) + edit.span(other, "in") >= visible_out:
            edit.set_start(other, edit.start(other) - length)
    return edit.save()


def split(project: Project, name: str, layer_id: str, at: str, expect: str | None = None) -> dict[str, Any]:
    """Cut a layer in two at comp position `at`: the first part ends there and a copy
    starts there, both with the same start, source and keys, as After Effects' split does.
    The copy is named after the layer with the next free number (`shot.2`, then `shot.3`
    for the next split, never `shot.2.2`)."""
    edit = Edit.open(project, name, expect)
    layer = edit.layer(layer_id)
    point = resolve_position(parse_literal(at, Field.POSITION), rate=edit.rate, grid=edit.grid)
    start = edit.start(layer)
    assert start + edit.span(layer, "in") < point < start + edit.span(layer, "out"), (
        f"{at} is not inside {layer_id}'s visible range"
    )
    base = re.sub(r"\.\d+$", "", layer_id)
    taken = {other["id"] for other in edit.data["layers"]}
    second = dict(layer, id=next(f"{base}.{k}" for k in range(2, 100000) if f"{base}.{k}" not in taken))
    edit.set_span(layer, "out", point - start, given=at)
    second["in"] = span_text(layer.get("in"), at, point - start, start, edit.rate, edit.grid,
                             frames_land=layer["type"] == "footage")
    edit.data["layers"].insert(edit.data["layers"].index(layer) + 1, second)
    edit.changes.append(f"{layer_id} split at {at}: {second['id']} from {second['in']}")
    return edit.save()


def snap(project: Project, name: str, layer_id: str, expect: str | None = None) -> dict[str, Any]:
    """Put a layer's visible in and out points on the nearest beats of the grid, written as
    beat spans; the start stays."""
    edit = Edit.open(project, name, expect)
    assert edit.grid is not None, f"comp {name} has no beat grid"
    layer = edit.layer(layer_id)
    start = edit.start(layer)
    for side in ("in", "out"):
        point = start + edit.span(layer, side)
        nearest = min(edit.grid.times, key=lambda beat: abs(beat - point))
        assert nearest >= start, f"{layer_id}: the nearest beat to its {side} point is before its start"
        old = layer.get(side, "0s")
        layer[side] = format_literal(span_literal(nearest - start, "b", anchor=start, rate=edit.rate, grid=edit.grid))
        edit.changes.append(f"{layer_id} {side} point snapped {old} to {layer[side]} (from start)")
    assert edit.span(layer, "in") <= edit.span(layer, "out"), f"{layer_id}: snapping puts in after out"
    return edit.save()


SIZES = ("fit", "fill", "own")


def place(project: Project, name: str, media: str, at: str, size: str = "fit", length: str | None = None,
          source: str | None = None, to: str | None = None, expect: str | None = None) -> dict[str, Any]:
    """Put media on the comp at position `at`, on top of the stack: from `source` (a source
    time: `f:` at the media's rate or seconds for footage, seconds for sound; its first
    frame or sample when absent), for `length` (a span from `at`, written as given), or up
    to the comp position `to` (written by `span_text`'s rule, as a split writes its cut),
    or as long as it lasts, cut off at the comp's end. The layer is named after the media (with
    `.2`, `.3` for more). Sound goes in as an audio layer at unity gain. Footage of another
    size is scaled to `fit` the comp (all of it seen, as Resolve's default for a mismatched
    resolution is), to `fill` it (no edge showing, cropped), or left at its `own` size; the
    scale is written out, never implied. Every frame the layer shows must be in the media.
    A photo goes in as a graphics layer of lungecuta's photo page (`_photo`)."""
    edit = Edit.open(project, name, expect)
    _place(project, edit, media, at, size, length, source, to)
    _resolves(project, edit)  # every frame it shows is in the media
    return edit.save()


# What `cut` takes for each shot, by the names `lungecuta op place` gives its options
SHOT_FIELDS = {"media", "at", "for", "to", "source", "size"}


def cut(project: Project, name: str, shots: list[dict[str, Any]], expect: str | None = None) -> dict[str, Any]:
    """Place a list of shots in one edit, each as `place` places it (`media`, `at`, and
    `for` or `to`, with `source` and `size` if given), in order, so each later one goes on
    top: the comp is checked once and written once, or not at all if any shot fails."""
    assert shots, "a cut needs a shot"
    edit = Edit.open(project, name, expect)
    for k, shot in enumerate(shots):
        assert set(shot) <= SHOT_FIELDS and {"media", "at"} <= set(shot), (
            f"shot {k}: {sorted(shot)}; a shot has media and at, and for or to, source and size ({sorted(SHOT_FIELDS)})")
        _place(project, edit, shot["media"], shot["at"], shot.get("size", "fit"), shot.get("for"), shot.get("source"),
               shot.get("to"))
    _resolves(project, edit)
    return edit.save()


def _place(project: Project, edit: Edit, media: str, at: str, size: str, length: str | None, source: str | None,
           to: str | None) -> None:
    """`place` on an open edit."""
    assert size in SIZES, f"{size!r} is not a size ({', '.join(SIZES)})"
    record = load_record(project, media)
    if record.image is not None:
        assert source is None, f"{media} is a photo: it has no source in point"
        return _photo(project, edit, media, at, size, length, to)
    layer_id = _free_id(edit, media)
    start = resolve_position(parse_literal(at, Field.POSITION), rate=edit.rate, grid=edit.grid)
    end = resolve_position(parse_literal(edit.data["end"], Field.POSITION), rate=edit.rate, grid=edit.grid)
    assert start < end, f"{at} is at or after the comp's end ({edit.data['end']})"
    sound = record.wav is not None
    if sound:
        seconds, media_rate = Fraction(record.wav.samples, record.wav.rate), None
    else:
        video = record.probe.video
        assert video is not None, f"{media} has neither video nor sound"
        media_rate = parse_rate(record.time.rate)
        seconds = Fraction(video.frames) / media_rate
    source = source or ("0s" if sound else "f:0")
    literal = parse_literal(source, Field.SOURCE_START)
    assert isinstance(literal, Seconds if sound else Frames | Seconds), (
        f"{media}'s source in point is a source time in {'seconds' if sound else 'frames or seconds'}, not {source}"
    )
    offset = Fraction(literal.ticks if isinstance(literal, Seconds) else literal.count * ticks_per_frame(media_rate),
                      TICKS_PER_SECOND)
    assert 0 <= offset < seconds, f"{media} has {float(seconds):.3f} s; it cannot start at {source}"
    assert length is None or to is None, "a layer runs for a length or up to a position, not both"
    if to is not None:
        out = _up_to(edit, to, start, frames_land=not sound)
    elif length is None:
        # As long as the media lasts, cut off at the comp's end: up to the comp's last frame,
        # counted as the comp counts frames, each time on the first frame at or after it.
        frames = min(int((seconds - offset) * edit.rate),
                     first_frame_at_or_after(end, edit.rate) - first_frame_at_or_after(start, edit.rate))
        out = f"f:{frames}"
    else:
        out = _length(edit, length, start)
    kind = "audio" if sound else "footage"
    layer = {"id": layer_id, "type": kind, "media": media, "start": at, "source_start": source, "out": out}
    if kind == "footage" and size != "own":
        shown = shown_size(record.probe.video.size, record.probe.video.rotation)
        comp_w, comp_h = edit.data["size"]
        ratios = (comp_w / shown[0], comp_h / shown[1])
        scale = min(ratios) if size == "fit" else max(ratios)
        if scale != 1:
            layer["transform"] = {"scale": round(scale, 6)}
    graphics = [k for k, other in enumerate(edit.data["layers"]) if other["type"] == "graphics"]
    # Footage goes under the graphics (they are top overlays); everything else on top.
    index = graphics[0] if graphics and layer["type"] == "footage" else len(edit.data["layers"])
    edit.data["layers"].insert(index, layer)
    edit.changes.append(f"{media} placed at {at} as {layer_id}, {out} long"
                        + (f", from {source}" if offset else "")
                        + (f", scaled {layer['transform']['scale']} to {size} the comp" if "transform" in layer else ""))


def _free_id(edit: Edit, base: str) -> str:
    """`base`, or `base.2`, `base.3` ... where it is taken."""
    taken = {layer["id"] for layer in edit.data["layers"]}
    return base if base not in taken else next(f"{base}.{k}" for k in range(2, 1000) if f"{base}.{k}" not in taken)


def graphic(project: Project, name: str, page: str, at: str, length: str | None = None,
            params: dict[str, Any] | None = None, to: str | None = None, expect: str | None = None) -> dict[str, Any]:
    """Put the web page `page` (`graphics/NAME.html`, 05-motion-graphics.md) on top of the
    comp at position `at` for `length` (a span, written as given) or up to position `to`,
    drawn in display space over everything, with `params` handed to the page. The layer is
    named after the page (with `.2`, `.3` for more)."""
    edit = Edit.open(project, name, expect)
    _graphic(project, edit, page, at, length, params, to, page.removeprefix("graphics/").removesuffix(".html"))
    _resolves(project, edit)
    return edit.save()


def _graphic(project: Project, edit: Edit, page: str, at: str, length: str | None, params: dict[str, Any] | None,
             to: str | None, named: str) -> None:
    """`graphic` on an open edit, the layer named `named`."""
    assert (length is None) != (to is None), "a graphic runs for a length or up to a position, one of the two"
    assert (project.directory / page).is_file(), f"{page} is not in the project"
    layer_id = _free_id(edit, named)
    start = resolve_position(parse_literal(at, Field.POSITION), rate=edit.rate, grid=edit.grid)
    out = _length(edit, length, start) if to is None else _up_to(edit, to, start, frames_land=True)
    layer = {"id": layer_id, "type": "graphics", "page": page, "space": "display", "start": at, "out": out}
    if params:
        layer["params"] = params
    edit.data["layers"].append(layer)
    edit.changes.append(f"{page} placed at {at} as {layer_id}, {out} long"
                        + (f", with {', '.join(sorted(params))}" if params else ""))


PHOTO_PAGE = "graphics/photo.html"  # lungecuta's page for a photo, written into a project that places one
PHOTO_FITS = {"fit": "contain", "fill": "cover", "own": "none"}  # place's sizes as the page's CSS object-fit


def _photo(project: Project, edit: Edit, media: str, at: str, size: str, length: str | None, to: str | None) -> None:
    """A photo placed: a graphics layer of lungecuta's photo page showing it, read where it
    lies, all of it seen (`fit`), filling the frame (`fill`) or at its own pixels (`own`).
    A photo has no length of its own, so it needs one. The page is written into
    `graphics/` the first time; one already there is the project's and is kept."""
    assert length is not None or to is not None, f"{media} is a photo: give how long it shows (--for or --to)"
    page = project.directory / PHOTO_PAGE
    if not page.exists():
        write_atomic(page, (Path(__file__).resolve().parents[1] / "web" / "photo.html").read_text())
    _graphic(project, edit, PHOTO_PAGE, at, length, {"media": media, "fit": PHOTO_FITS[size]}, to, media)


def _length(edit: Edit, length: str, start: int) -> str:
    """A layer's out point given as its length from `start`: written as given."""
    span = resolve_span(parse_literal(length, Field.LAYER), anchor=start, rate=edit.rate, grid=edit.grid)
    assert span > 0, f"a layer {length} long shows nothing"
    return length


def _up_to(edit: Edit, to: str, start: int, *, frames_land: bool) -> str:
    """A layer's out point given as the comp position it ends at, written as a span from
    `start` in the unit `to` was given in when that holds it, else (for a layer shown on
    frames) to the frame `to` lands on, as a split writes its cut."""
    point = resolve_position(parse_literal(to, Field.POSITION), rate=edit.rate, grid=edit.grid)
    assert point > start, f"{to} is not after the layer's start"
    return span_text(None, to, point - start, start, edit.rate, edit.grid, frames_land=frames_land)
