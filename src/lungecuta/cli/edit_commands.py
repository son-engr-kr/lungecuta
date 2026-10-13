"""lc commands that change a comp or check what changed: the op group, diff, lint and audio-check."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from .. import ops
from .common import main, print_json, project_of, project_option


@main.command("audio-check")
@click.argument("comp")
@click.option("--from", "start", help="Where the span starts, as a comp position (the comp's start when absent).")
@click.option("--to", "end", help="Where it ends (the comp's end when absent).")
@project_option
def audio_check_command(comp: str, start: str | None, end: str | None, project_dir: Path | None) -> None:
    """For every cut in the span, its offset in milliseconds to the nearest beat and
    whether that beat is a downbeat; the mix's RMS on every half beat (dBFS, before the
    master chain) and its slope in dB per beat, which says whether a build climbs.
    Offsets to onsets are not written yet."""
    from ..audio_check import audio_check

    print_json(audio_check(project_of(project_dir), comp, start, end))


@main.command("lint")
@click.argument("comp")
@project_option
def lint_command(comp: str, project_dir: Path | None) -> None:
    """The past projects' review rules a machine can check, as warnings, each threshold in
    project.json's `lint`: graphics on screen long enough to read, no source frame shown by
    two layers, a licence record on every music file. The rules not checked yet are listed."""
    from ..lint import lint

    print_json(lint(project_of(project_dir), comp))


@main.command("diff")
@click.argument("comp")
@click.argument("before", default="HEAD")
@click.argument("after", default="working")
@project_option
def diff_command(comp: str, before: str, after: str, project_dir: Path | None) -> None:
    """What changed in COMP between two revisions, in editing terms. A revision is a git
    revision of the repository the project is in, a path to a comp file, or `working`
    (the file as it is now); by default HEAD against working."""
    from ..diff import diff

    print_json(diff(project_of(project_dir), comp, before, after))


@main.group()
def op() -> None:
    """Edit operations on a comp, the same functions the editor calls (06-editing.md).

    Amounts are signed spans: f:-3, -0.5s, b:1, b:-0.5 (a beat amount counts beats on the
    comp's grid). A changed value keeps the unit it was written in. The comp is written
    only if it is still the file that was read; the changes are printed in editing terms.
    """


@op.command("move")
@click.argument("comp")
@click.argument("layer")
@click.option("--by", required=True, help="How far: a signed span.")
@project_option
def op_move(comp: str, layer: str, by: str, project_dir: Path | None) -> None:
    """Move LAYER: its start changes, and its keys, in and out points come along."""
    print_json(ops.move(project_of(project_dir), comp, layer, by))


@op.command("trim")
@click.argument("comp")
@click.argument("layer")
@click.option("--in", "in_", help="Move the in point by this signed span.")
@click.option("--out", help="Move the out point by this signed span.")
@project_option
def op_trim(comp: str, layer: str, in_: str | None, out: str | None, project_dir: Path | None) -> None:
    """Move LAYER's in or out point; its start, keys and source stay."""
    assert (in_ is None) != (out is None), "give --in or --out"
    print_json(ops.trim(project_of(project_dir), comp, layer, "in" if in_ else "out", in_ or out))


@op.command("slip")
@click.argument("comp")
@click.argument("layer")
@click.option("--by", required=True, help="How far, in source time: f:-3 at the media's rate, or seconds.")
@project_option
def op_slip(comp: str, layer: str, by: str, project_dir: Path | None) -> None:
    """Move the content under LAYER's fixed in and out points (its source start)."""
    print_json(ops.slip(project_of(project_dir), comp, layer, by))


@op.command("slide")
@click.argument("comp")
@click.argument("layer")
@click.option("--by", required=True, help="How far: a signed span, counted from the layer's in point.")
@project_option
def op_slide(comp: str, layer: str, by: str, project_dir: Path | None) -> None:
    """Slide LAYER between its neighbours: it moves with its content, the layer before it
    ends later or earlier and the layer after it starts later or earlier with it."""
    print_json(ops.slide(project_of(project_dir), comp, layer, by))


@op.command("roll")
@click.argument("comp")
@click.argument("left")
@click.argument("right")
@click.option("--by", required=True, help="How far the cut moves: a signed span.")
@project_option
def op_roll(comp: str, left: str, right: str, by: str, project_dir: Path | None) -> None:
    """Move the cut between LEFT and RIGHT: LEFT's out and RIGHT's in move together."""
    print_json(ops.roll(project_of(project_dir), comp, left, right, by))


@op.command("ripple-delete")
@click.argument("comp")
@click.argument("layer")
@project_option
def op_ripple_delete(comp: str, layer: str, project_dir: Path | None) -> None:
    """Remove LAYER and move every later layer earlier by its length (the music the beat
    grid comes from stays)."""
    print_json(ops.ripple_delete(project_of(project_dir), comp, layer))


@op.command("split")
@click.argument("comp")
@click.argument("layer")
@click.option("--at", required=True, help="Where, as a comp position.")
@project_option
def op_split(comp: str, layer: str, at: str, project_dir: Path | None) -> None:
    """Cut LAYER in two at a comp position; the second part is LAYER.2."""
    print_json(ops.split(project_of(project_dir), comp, layer, at))


@op.command("place")
@click.argument("comp")
@click.argument("media")
@click.option("--at", required=True, help="The comp position the layer starts at.")
@project_option
def op_place(comp: str, media: str, at: str, project_dir: Path | None) -> None:
    """Put MEDIA on the comp at a position, as long as it lasts: footage under any graphics,
    music as an audio layer."""
    print_json(ops.place(project_of(project_dir), comp, media, at))


@op.command("snap")
@click.argument("comp")
@click.argument("layer")
@project_option
def op_snap(comp: str, layer: str, project_dir: Path | None) -> None:
    """Put LAYER's in and out points on the nearest beats, written as beat spans."""
    print_json(ops.snap(project_of(project_dir), comp, layer))


@op.command("style")
@click.argument("comp")
@click.argument("layer")
@click.argument("style")
@click.option("--at", required=True, help="The comp beat the style's anchor lands on (b:64, bar:16).")
@click.option("--intensity", type=float, help="0 is no effect, 1 the style as written (its own default when absent).")
@project_option
def op_style(comp: str, layer: str, style: str, at: str, intensity: float | None, project_dir: Path | None) -> None:
    """Place STYLE (styles/STYLE.json) on footage LAYER, anchored on a beat; it replaces
    the layer's earlier style instance. The instance stays linked: editing the style
    changes every placement."""
    print_json(ops.style(project_of(project_dir), comp, layer, style, at, intensity))


@op.command("unstyle")
@click.argument("comp")
@click.argument("layer")
@project_option
def op_unstyle(comp: str, layer: str, project_dir: Path | None) -> None:
    """Take LAYER's style instance off."""
    print_json(ops.unstyle(project_of(project_dir), comp, layer))


@op.command("key")
@click.argument("comp")
@click.argument("layer")
@click.argument("prop", type=click.Choice(ops.KEYED))
@click.option("--at", required=True, help="The comp position of the key (f:120, b:16, 4.2s).")
@click.option("--value", required=True,
              help="A number, a pair as 0.4,0.6 (position, scale x and y), or a source time for remap (f:80, 2.5s).")
@click.option("--ease", type=click.Choice(["in", "out", "in_out", "hold"]), help="The segment arriving at this key.")
@project_option
def op_key(comp: str, layer: str, prop: str, at: str, value: str, ease: str | None, project_dir: Path | None) -> None:
    """Set PROP's key on LAYER at a comp position, replacing one already there; the first
    key of a constant property makes it animated."""
    if prop == "remap":
        parsed: Any = value
    elif "," in value:
        parsed = [float(part) for part in value.split(",")]
    else:
        parsed = float(value)
    print_json(ops.key(project_of(project_dir), comp, layer, prop, at, parsed, ease))


@op.command("unkey")
@click.argument("comp")
@click.argument("layer")
@click.argument("prop", type=click.Choice(ops.KEYED))
@click.option("--at", required=True, help="The comp position of the key.")
@project_option
def op_unkey(comp: str, layer: str, prop: str, at: str, project_dir: Path | None) -> None:
    """Delete PROP's key on LAYER at a comp position; the last transform key leaves its
    value as the property's constant."""
    print_json(ops.unkey(project_of(project_dir), comp, layer, prop, at))


@op.command("set")
@click.argument("comp")
@click.argument("layer")
@click.argument("prop", type=click.Choice(["position", "scale", "opacity", "rotation", "anchor", "gain", "fade_in",
                                           "fade_out"]))
@click.option("--value", required=True,
              help="A number, a pair as 0.4,0.6, a span for a fade (b:2, 1.5s), or none to take a fade off.")
@project_option
def op_set(comp: str, layer: str, prop: str, value: str, project_dir: Path | None) -> None:
    """Set PROP of LAYER to a constant: a footage layer's transform, an audio layer's gain
    in dB or its fades. A property with keys takes a key instead (lc op key)."""
    if prop in ("fade_in", "fade_out"):
        parsed: Any = None if value == "none" else value
    elif "," in value:
        parsed = [float(part) for part in value.split(",")]
    else:
        parsed = float(value)
    print_json(ops.set_value(project_of(project_dir), comp, layer, prop, parsed))


@op.command("masks")
@click.argument("comp")
@click.argument("layer")
@click.option("--masks", "masks", required=True,
              help='The masks as JSON, in order: \'[{"shape": "ellipse", "size": [0.6, 0.8], "feather": 0.05}]\', [] for none.')
@project_option
def op_masks(comp: str, layer: str, masks: str, project_dir: Path | None) -> None:
    """Replace LAYER's masks: rectangles and ellipses in the layer's own frame (fractions
    of its width and height), feathered, inverted, combined by add, subtract or intersect."""
    print_json(ops.set_masks(project_of(project_dir), comp, layer, json.loads(masks)))


@op.command("transition")
@click.argument("comp")
@click.argument("kind", type=click.Choice(["dissolve", "dip", "fade_in", "fade_out"]))
@click.argument("layers", nargs=-1, required=True)
@click.option("--length", required=True, help="How long: b:1, 0.5s, f:12; a dissolve and a dip centre on the cut.")
@project_option
def op_transition(comp: str, kind: str, layers: tuple[str, ...], length: str, project_dir: Path | None) -> None:
    """Put a dissolve or dip on the cut between two footage LAYERS (outgoing, incoming), or
    a fade on one layer; it replaces one of the same kind there."""
    print_json(ops.transition(project_of(project_dir), comp, kind, list(layers), length))


@op.command("untransition")
@click.argument("comp")
@click.argument("kind", type=click.Choice(["dissolve", "dip", "fade_in", "fade_out"]))
@click.argument("layers", nargs=-1, required=True)
@project_option
def op_untransition(comp: str, kind: str, layers: tuple[str, ...], project_dir: Path | None) -> None:
    """Take a transition off."""
    print_json(ops.untransition(project_of(project_dir), comp, kind, list(layers)))


@op.command("remap")
@click.argument("comp")
@click.argument("layer")
@project_option
def op_remap(comp: str, layer: str, project_dir: Path | None) -> None:
    """Enable time remapping on LAYER: keys at its in and out points with the source times
    shown there, every frame checked to stay the same."""
    print_json(ops.remap(project_of(project_dir), comp, layer))


@op.command("grade")
@click.argument("comp")
@click.argument("layer")
@click.option("--ops", "operations", required=True,
              help='The grade as JSON, the operations in order: \'[{"op": "primary", "contrast": 1.1}]\', [] for none.')
@project_option
def op_grade(comp: str, layer: str, operations: str, project_dir: Path | None) -> None:
    """Replace LAYER's grade (docs/design/04-color.md): primary, cdl and lut operations."""
    print_json(ops.set_grade(project_of(project_dir), comp, layer, json.loads(operations)))
