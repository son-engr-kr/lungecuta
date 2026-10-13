"""The `lc` command (docs/design/09-agent-interface.md)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from . import journal, ops
from .beats import reanalyse
from .detect import detect as detect_clip
from .encode import PRESETS
from .fcpxml import export as export_fcpxml
from .files import json_text, write_atomic
from .graphics import render as render_graphic
from .media import Project, import_file, relink as relink_root, set_root, summary
from .patches import read_patches, write_card
from .probe import probe
from .project import find_project, init_project
from .render import render as render_comp
from .schema import SCHEMAS, schema_text
from .scope import scope as scope_frame
from .sheet import COLUMNS, ROWS
from .sheet import sheet as contact_sheet
from .still import still as still_frame
from .ticks import parse_rate
from .validate import validate as validate_project
from .verify import verify as verify_render


def _print_json(data: Any) -> None:
    """Numbers and paths go to stdout as one JSON object, so an agent can parse them."""
    click.echo(json.dumps(data, ensure_ascii=False))


def _project(directory: Path | None) -> Project:
    """`--project DIR`, or else the nearest project.json above the current directory."""
    return Project.load(find_project(directory or Path.cwd()))


_project_option = click.option(
    "--project",
    "project_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="The project directory; by default the nearest one holding project.json.",
)


_threads_option = click.option(
    "--threads", type=click.IntRange(min=1), help="OpenColorIO threads; by default the machine's core count."
)


@click.group()
def main() -> None:
    """lungecuta: edit short, music-driven pieces shot in log, with an AI agent."""


@main.command()
@click.argument("directory", type=click.Path(file_okay=False, path_type=Path))
def init(directory: Path) -> None:
    """Create a new project in DIRECTORY, which must be empty or not exist yet.

    Writes project.json with the colour defaults, roots.local.json (empty) and a
    .gitignore for it and cache/, the empty media/, comps/, grades/, styles/,
    graphics/, luts/ and cache/ directories, and the guide a coding agent reads
    (AGENTS.md for Codex, CLAUDE.md importing it for Claude Code; see lc guide). The
    .lc suffix is a convention only.
    """
    _print_json({"project": str(init_project(directory))})


@main.command()
@click.option("--write", is_flag=True, help="Write the guide into the project instead of printing it.")
@_project_option
def guide(write: bool, project_dir: Path | None) -> None:
    """The guide a coding agent reads in a project: its files, time literals, the steps of
    every edit and the commands that check them. --write puts it into the project's
    AGENTS.md (read by Codex) between its markers, keeping the rest of the file, and makes
    CLAUDE.md import AGENTS.md (read by Claude Code); lc init already does this."""
    from .guide import guide_text, write_guide

    if write:
        _print_json(write_guide(find_project(project_dir or Path.cwd())))
    else:
        _print_json({"guide": guide_text()})


@main.command()
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.pass_context
def detect(context: click.Context, file: Path) -> None:
    """The colour space and time of FILE, with every piece of evidence, without importing.

    Exits with status 1 when either is undecided.
    """
    detection = detect_clip(file, probe(file))
    _print_json({
        "file": file.name,
        "time": detection.time.model_dump(mode="json"),
        "color": detection.color.model_dump(mode="json"),
    })
    if not detection.decided:
        context.exit(1)


@main.command()
@click.argument("name")
@click.argument("place", type=click.Path(exists=True, file_okay=False, path_type=Path))
@_project_option
def root(name: str, place: Path, project_dir: Path | None) -> None:
    """List media root NAME in project.json and place it at PLACE on this machine
    (roots.local.json, never committed)."""
    project = _project(project_dir)
    set_root(project.directory, name, place)
    _print_json({"root": name, "place": str(place.resolve())})


@main.command()
@click.option("--root", "name", required=True, help="The media root whose files have moved.")
@click.argument("place", type=click.Path(exists=True, file_okay=False, path_type=Path))
@_project_option
def relink(name: str, place: Path, project_dir: Path | None) -> None:
    """Point media root NAME at PLACE, where its files are now.

    Each record's file is found under PLACE by its size and then its hash, never by name
    alone. Every file of the root must be found, once each; a record whose file now sits
    at another path under PLACE gets that path. Nothing is written otherwise.
    """
    _print_json(relink_root(_project(project_dir), name, place))


@main.command("import")
@click.argument("files", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--root", "root_name", required=True, help="The media root the files lie under.")
@click.option("--key", help="The media key, for a single file whose stem is taken already.")
@_project_option
def import_(files: tuple[Path, ...], root_name: str, key: str | None, project_dir: Path | None) -> None:
    """Import FILES as media records under media/: probe, rates and timecode, colour
    decision with evidence, decoder check, proxy with its parity test."""
    assert key is None or len(files) == 1, "--key names one file"
    project = _project(project_dir)
    imported = [summary(import_file(project, file, root=root_name, key=key)) for file in files]
    _print_json({"imported": imported})


def _one_of(comp: str | None, key: str | None) -> None:
    assert (comp is None) != (key is None), "name a comp, or a clip with --media, not both and not neither"


@main.command()
@click.argument("comp", required=False)
@click.option("--media", "key", help="The media key of an imported clip, instead of a comp.")
@click.option(
    "--at",
    required=True,
    help="With a comp, a comp position (f:120, 4.5s, b:16+0.5, bar:4, tc:00:00:04:15), landing on the first "
    "frame at or after it. With --media, source time in the clip: f:N counts frames from its first presented "
    "frame at its own rate; seconds name the frame whose interval holds them.",
)
@click.option(
    "--height",
    type=click.IntRange(min=1),
    help="Height in lines of the picture; by default the comp's or the clip's own size.",
)
@_threads_option
@click.option(
    "--out",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Where the PNG goes; by default cache/lc/still/ in the project.",
)
@_project_option
def still(
    comp: str | None, key: str | None, at: str, height: int | None, threads: int | None, out: Path | None,
    project_dir: Path | None,
) -> None:
    """One frame at final quality: of COMP as lc render delivers it, or of an imported clip
    read from its original.

    A comp's frame is composed as the render composes it, graphics included, at the
    comp's size; --height area-averages that picture, as a player shows a smaller copy,
    and the numbers come from the full frame. The JSON says which layers are on screen
    and where the frame is on the beat grid.

    A clip's frame is decoded with the record's matrix and range, sent through the clip's
    input transform into the working space, turned by its rotation tag, and sent through
    the project's display and view (by default Rec.1886 Rec.709 with the ACES 2.0 SDR
    view) with CPU OpenColorIO. A clip whose colour space is undecided has no still.

    The PNG is 8-bit and untagged, and holds display-encoded code values for the
    project's display and view: it is for looking at. The numbers printed as JSON
    (per channel: minimum, 1st, 50th and 99th percentiles and maximum, in 10-bit output
    codes) come from the float frame before it is quantized.
    """
    _one_of(comp, key)
    if comp is not None:
        from .look import comp_still

        _print_json(comp_still(_project(project_dir), comp, at, height=height, threads=threads, out=out))
        return
    _print_json(still_frame(_project(project_dir), key, at, height=height, threads=threads, out=out))


@main.command()
@click.argument("comp", required=False)
@click.option("--media", "key", help="The media key of an imported clip, instead of a comp.")
@click.option(
    "--every",
    required=True,
    help="The interval between frames. With a comp, a span: b:1 (a beat), f:30, 0.1s for a dense sheet. "
    "With --media, f:N or seconds that are a whole number of frames, from frame 0.",
)
@click.option("--from", "start", help="With a comp: the position the sheet starts at (the comp's start).")
@click.option("--to", "end", help="With a comp: the position it stops before (the comp's end).")
@click.option("--columns", type=click.IntRange(min=1), default=COLUMNS, show_default=True, help="Tiles a row.")
@click.option("--rows", type=click.IntRange(min=1), default=ROWS, show_default=True, help="Rows a page.")
@click.option(
    "--height",
    type=click.IntRange(min=1),
    help="Tile height in lines of the frame as shown; by default 320 pixels on the long side.",
)
@_threads_option
@click.option(
    "--out",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Where the first page goes, the others with _p1, _p2 before the suffix; "
    "by default cache/lc/sheet/ in the project.",
)
@_project_option
def sheet(
    comp: str | None, key: str | None, every: str, start: str | None, end: str | None, columns: int, rows: int,
    height: int | None, threads: int | None, out: Path | None, project_dir: Path | None,
) -> None:
    """A contact sheet: of COMP, a frame at every interval as lc still COMP shows it, each
    labelled with its frame, seconds, beat and the layers on screen; or of an imported clip,
    a frame at every interval from frame 0, each labelled with its frame number and its
    seconds from the first frame (to the millisecond).

    A clip's frames come from one decode of the original and go through the same chain as
    lc still, each area-downscaled to the tile size in the working space. Pages are
    8-bit PNGs, untagged, holding display-encoded code values for the project's
    display and view. The JSON lists every tile with the numbers lc still prints,
    computed on the float tile.
    """
    _one_of(comp, key)
    if comp is not None:
        from .look import comp_sheet

        _print_json(comp_sheet(_project(project_dir), comp, every, start=start, end=end, columns=columns, rows=rows,
                               height=height, threads=threads, out=out))
        return
    assert start is None and end is None, "--from and --to are for a comp's sheet"
    _print_json(contact_sheet(
        _project(project_dir), key, every, columns=columns, rows=rows, height=height, threads=threads, out=out,
    ))


@main.command()
@click.argument("comp", required=False)
@click.option("--media", "key", help="The media key of an imported clip, instead of a comp.")
@click.option("--at", required=True, help="A comp position, or with --media a source time, as for lc still.")
@click.option(
    "--height",
    type=click.IntRange(min=1),
    help="Analyse the frame at this many lines (a clip's area-downscaled in the working space, a comp's on "
    "its display-encoded values); by default at its own size.",
)
@_threads_option
@click.option(
    "--out",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Where the PNG goes; by default cache/lc/scope/ in the project.",
)
@_project_option
def scope(
    comp: str | None, key: str | None, at: str, height: int | None, threads: int | None, out: Path | None,
    project_dir: Path | None,
) -> None:
    """Waveform, RGB parade and vectorscope of one frame: of COMP as lc still COMP gives it,
    or of an imported clip read from the original through the same chain as lc still.

    Computed on the display-encoded output of the project's display and view. Luma is
    Y' = 0.2126 R' + 0.7152 G' + 0.0722 B' on the encoded values, and Cb, Cr come from
    the BT.709 matrix. The waveforms run from code 0 at the bottom to 1023 at the top
    with a line every 128 codes; values outside are drawn on the edge. The vectorscope
    has Cb to the right and Cr up, with boxes at the 75 % (larger) and 100 % colour
    bars. Brightness is the count of pixels on a log scale.

    Printed as JSON: luma 1st, 50th and 99th percentiles; the fractions of pixels with
    any channel at or below 0 (low), at or above 1 (high), or either (any); per-channel
    minimum, percentiles and maximum; and the median and 99th percentile of the chroma
    magnitude. All are 10-bit output codes, except the fractions, from the float frame
    before it is quantized.
    """
    _one_of(comp, key)
    if comp is not None:
        from .look import comp_scope

        _print_json(comp_scope(_project(project_dir), comp, at, height=height, threads=threads, out=out))
        return
    _print_json(scope_frame(_project(project_dir), key, at, height=height, threads=threads, out=out))


@main.command()
@click.argument("key")
@click.option(
    "--replace",
    is_flag=True,
    help="Apply the new map even though beat or bar indices shift: the person's decision, after reading "
    "what a run without it lists.",
)
@_project_option
@click.pass_context
def beats(context: click.Context, key: str, replace: bool, project_dir: Path | None) -> None:
    """Run the beat analysis of music KEY again on its WAV.

    Each new beat is matched to the nearest current one within half the median beat
    interval. The new times are written only if every current beat has exactly one
    match and the downbeats stay on the same indices, so every beat and bar literal keeps
    its musical beat; manual beats keep their times. Otherwise nothing is written, the
    inserted and removed beats are listed, and the command exits with status 1.
    """
    result = reanalyse(_project(project_dir), key, replace=replace)
    _print_json(result)
    if not result["applied"]:
        context.exit(1)


@main.command()
@click.argument("comp")
@click.option(
    "--out",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Where the FCPXML goes; by default cache/lc/fcpxml/COMP.fcpxml in the project.",
)
@_project_option
def fcpxml(comp: str, out: Path | None, project_dir: Path | None) -> None:
    """FCPXML 1.10 of comp COMP (comps/COMP.json), for Resolve's File > Import > Timeline.

    Footage layers become clips on one picture track, cut on the first frame at or after
    each in and out point, constant speed as a time map; music layers become clips of the
    WAV their beats were found in, on tracks below; markers land on the first frame at or
    after their time. Source in points are written against each clip's start timecode.
    The FCPXML names media by absolute path, so it belongs to this machine.

    Printed as JSON: every cut with its comp frames, timeline timecode, source frames and
    source timecode, and the lc still command that shows its first frame, to check against
    Resolve's viewer after import.
    """
    _print_json(export_fcpxml(_project(project_dir), comp, out))


@main.command()
@_project_option
def validate(project_dir: Path | None) -> None:
    """Check the project: project.json, roots.local.json and every media record against
    their schemas, every original found and unchanged since import (and every music WAV),
    and every comp resolved to frames. The first problem stops it with its message.

    Printed as JSON: the media count, the keys still undecided in colour or time (which no
    comp can place), and each comp's frames, cuts, sounds and beats.
    """
    _print_json(validate_project(_project(project_dir)))


def _size(text: str) -> tuple[int, int]:
    width, _, height = text.partition("x")
    assert width.isdigit() and height.isdigit(), f"a size is WIDTHxHEIGHT, such as 1920x1080, not {text!r}"
    return int(width), int(height)


_size_option = click.option("--size", required=True, help="WIDTHxHEIGHT in pixels, such as 1920x1080.")
_rate_option = click.option("--rate", required=True, help="Frame rate as ffprobe writes it: 30000/1001, 25, 60.")


@main.command()
@click.argument("page", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@_size_option
@_rate_option
@click.option("--frames", type=click.IntRange(min=1), required=True, help="How many frames, from t = 0.")
@click.option("--out", type=click.Path(dir_okay=False, path_type=Path), required=True, help="The .mov to write.")
def graphic(page: Path, size: str, rate: str, frames: int, out: Path) -> None:
    """Render a web page to ProRes 4444 with alpha, for import into Resolve.

    The page sets window.ready = true once loaded and draws frame t (seconds) completely
    in window.renderAt(t), the contract of the past projects' pages. Each frame is seeked
    and captured on a transparent background in the pinned Chromium; the page is served
    only its own directory and every other request is refused. Frames are 8-bit sRGB
    code values, written as BT.709 Y'CbCr in limited range, 10-bit 4:4:4 with alpha, tagged
    1-1-1.
    """
    _print_json(render_graphic(page, out, size=_size(size), rate=parse_rate(rate), frames=frames))


@main.command("patch-card")
@click.argument("directory", type=click.Path(file_okay=False, path_type=Path))
@_size_option
@_rate_option
@click.option("--frames", type=click.IntRange(min=1), default=150, show_default=True, help="Length of the card.")
def patch_card(directory: Path, size: str, rate: str, frames: int) -> None:
    """A card of known colour patches in DIRECTORY: card.json (each patch's code and
    rectangle), card.html and card.mov (ProRes 4444 with alpha, by lc graphic).

    Put card.mov over footage in a Resolve timeline at full frame, export, and read the
    export with lc patches to see what Resolve did to a Rec.709 graphic. The card's own
    file is read back first and must hold every patch's code.
    """
    _print_json(write_card(directory, size=_size(size), rate=parse_rate(rate), frames=frames))


@main.command()
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--card", "card_file", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True,
              help="The card.json lc patch-card wrote.")
@click.option("--at", "frame", type=click.IntRange(min=0), default=0, show_default=True, help="The frame index to read.")
@click.option("--matrix", type=click.Choice(["bt709"]), help="The file's Y'CbCr matrix, if it is not tagged.")
@click.option("--range", "range_", type=click.Choice(["full", "limited"]), help="The file's range, if it is not tagged.")
@click.option("--chroma-location", type=click.Choice(["left", "center", "topleft", "top", "bottomleft", "bottom"]),
              help="Where the file's chroma samples sit, if it does not say (a patch's middle is flat, so any will do).")
def patches(file: Path, card_file: Path, frame: int, matrix: str | None, range_: str | None,
            chroma_location: str | None) -> None:
    """Read FILE's frame at every patch of a card: the mean R'G'B' of each patch's middle,
    in 8-bit codes, against the code the card holds, and the largest difference.

    FILE is decoded with its tagged matrix and range (or the ones given), by ITU-T H.273.
    The card's own ProRes reads back within half a code; an export adds its codec's
    rounding, which the mean over a patch's middle mostly averages out.
    """
    spec = json.loads(card_file.read_text())
    _print_json(read_patches(file, spec, index=frame, matrix=matrix, range_=range_, chroma_location=chroma_location))


@main.command("render")
@click.argument("comp")
@click.option("--preset", required=True, type=click.Choice(sorted(PRESETS)), help="The delivery preset.")
@click.option("--out", type=click.Path(dir_okay=False, path_type=Path),
              help="The file to write; by default cache/lc/render/COMP.PRESET.EXT in the project.")
@_threads_option
@click.option("--workers", type=click.IntRange(min=1),
              help="Frames rendered at once (OCIO single-threaded in each); by default the core count.")
@_project_option
def render_command(comp: str, preset: str, out: Path | None, threads: int | None, workers: int | None,
                   project_dir: Path | None) -> None:
    """Render comp COMP to a delivery file, with a manifest of what decided it.

    Every frame goes through the same chain as lc still at the comp's size; music layers
    are mixed at unity gain at 48 kHz. Display-encoded R'G'B' becomes BT.709 limited-range
    Y'CbCr by ITU-T H.273, chroma decimated co-sited, 8-bit output dithered, all four tags
    set on the frames. The manifest (FILE.manifest.json) records the comp's and media's
    hashes, the colour settings and the versions of everything that decided the pixels.
    """
    _print_json(render_comp(_project(project_dir), comp, preset, out, threads, workers))


@main.command("audio-check")
@click.argument("comp")
@click.option("--from", "start", help="Where the span starts, as a comp position (the comp's start when absent).")
@click.option("--to", "end", help="Where it ends (the comp's end when absent).")
@_project_option
def audio_check_command(comp: str, start: str | None, end: str | None, project_dir: Path | None) -> None:
    """For every cut in the span, its offset in milliseconds to the nearest beat and
    whether that beat is a downbeat; the mix's RMS on every half beat (dBFS, before the
    master chain) and its slope in dB per beat, which says whether a build climbs.
    Offsets to onsets are not written yet."""
    from .audio_check import audio_check

    _print_json(audio_check(_project(project_dir), comp, start, end))


@main.command("lint")
@click.argument("comp")
@_project_option
def lint_command(comp: str, project_dir: Path | None) -> None:
    """The past projects' review rules a machine can check, as warnings, each threshold in
    project.json's `lint`: graphics on screen long enough to read, no source frame shown by
    two layers, a licence record on every music file. The rules not checked yet are listed."""
    from .lint import lint

    _print_json(lint(_project(project_dir), comp))


@main.command("flash")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@_threads_option
@_project_option
def flash_command(file: Path, threads: int | None, project_dir: Path | None) -> None:
    """WCAG 2.2's general flash threshold on FILE, every frame decoded: no more than three
    flashes (pairs of opposing changes of 0.1 in relative luminance, the darker under 0.8)
    in any second over more than a quarter of any 10 degree field, the field taken as a
    third of the frame's height. Luminance is the display's: the one FILE's manifest names
    (lc render's FILE.manifest.json), or the project's. Red flashes are not checked. Exits
    with status 1 when the file fails."""
    from .flash import flash

    manifest = file.with_name(file.name + ".manifest.json")
    if manifest.is_file():
        settings = json.loads(manifest.read_text())["color"]
        config_name, display, view = settings["config"], settings["display"], settings["view"]
    else:
        settings = _project(project_dir).file.color
        config_name, display, view = settings.config, settings.display, settings.view
    result = flash(file, config_name=config_name, display=display, view=view, threads=threads)
    _print_json(result)
    if not result["ok"]:
        raise SystemExit(1)


@main.command("licence")
@click.argument("key")
@click.option("--name", required=True, help="The licence as its source states it (CC0, a stock licence and its number).")
@click.option("--source", required=True, help="The page where it was checked.")
@click.option("--checked", required=True, help="The date it was checked, YYYY-MM-DD.")
@click.option("--note", help="Anything else the next person should know.")
@_project_option
def licence_command(key: str, name: str, source: str, checked: str, note: str | None, project_dir: Path | None) -> None:
    """Record under what terms media KEY may be used, as a person checked it on its source."""
    from .media import load_record
    from .schema.media import Licence

    project = _project(project_dir)
    record = load_record(project, key)
    record.licence = Licence(name=name, source=source, checked=checked, note=note)
    path = project.directory / "media" / f"{key}.json"
    write_atomic(path, json_text(record.model_dump(mode="json")))
    _print_json({"media": key, "licence": record.licence.model_dump()})


@main.command("diff")
@click.argument("comp")
@click.argument("before", default="HEAD")
@click.argument("after", default="working")
@_project_option
def diff_command(comp: str, before: str, after: str, project_dir: Path | None) -> None:
    """What changed in COMP between two revisions, in editing terms. A revision is a git
    revision of the repository the project is in, a path to a comp file, or `working`
    (the file as it is now); by default HEAD against working."""
    from .diff import diff

    _print_json(diff(_project(project_dir), comp, before, after))


@main.command("verify")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--comp", required=True, help="The comp FILE was rendered from.")
@click.option("--preset", required=True, type=click.Choice(sorted(PRESETS)), help="The preset it was rendered with.")
@_threads_option
@_project_option
def verify_command(file: Path, comp: str, preset: str, threads: int | None, project_dir: Path | None) -> None:
    """Check FILE against its comp and preset: codec, profile, pixel format, size, rate,
    frame count, the four colour tags, audio format; each cut's first and last frames,
    decoded from the file, against the engine's own reference render (bias per channel,
    PSNR, and closer to its own frame than to a neighbour's); integrated loudness within
    0.2 LU of the preset's target and true peak at or under its ceiling, by ffmpeg's
    ebur128 (EBU R 128-2023). Exits with status 1 when a check fails.
    """
    result = verify_render(_project(project_dir), comp, file, preset, threads=threads)
    _print_json(result)
    if not result["ok"]:
        raise SystemExit(1)


@main.command("new-comp")
@click.argument("name")
@click.option("--size", required=True, help="WIDTHxHEIGHT, such as 3840x2160 or 1080x1920.")
@click.option("--rate", required=True, help="Frame rate as ffprobe writes it: 30000/1001, 25, 60.")
@click.option("--end", required=True, help="Where the comp ends, a position: 30s, f:900, or with music b:64.")
@click.option("--music", help="A music record to put at 0s and take the beat grid from.")
@_project_option
def new_comp(name: str, size: str, rate: str, end: str, music: str | None, project_dir: Path | None) -> None:
    """Create comp NAME (comps/NAME.json) and list it in project.json."""
    _print_json(ops.new_comp(_project(project_dir), name, size=_size(size), rate=rate, end=end, music=music))


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
@_project_option
def op_move(comp: str, layer: str, by: str, project_dir: Path | None) -> None:
    """Move LAYER: its start changes, and its keys, in and out points come along."""
    _print_json(ops.move(_project(project_dir), comp, layer, by))


@op.command("trim")
@click.argument("comp")
@click.argument("layer")
@click.option("--in", "in_", help="Move the in point by this signed span.")
@click.option("--out", help="Move the out point by this signed span.")
@_project_option
def op_trim(comp: str, layer: str, in_: str | None, out: str | None, project_dir: Path | None) -> None:
    """Move LAYER's in or out point; its start, keys and source stay."""
    assert (in_ is None) != (out is None), "give --in or --out"
    _print_json(ops.trim(_project(project_dir), comp, layer, "in" if in_ else "out", in_ or out))


@op.command("slip")
@click.argument("comp")
@click.argument("layer")
@click.option("--by", required=True, help="How far, in source time: f:-3 at the media's rate, or seconds.")
@_project_option
def op_slip(comp: str, layer: str, by: str, project_dir: Path | None) -> None:
    """Move the content under LAYER's fixed in and out points (its source start)."""
    _print_json(ops.slip(_project(project_dir), comp, layer, by))


@op.command("slide")
@click.argument("comp")
@click.argument("layer")
@click.option("--by", required=True, help="How far: a signed span, counted from the layer's in point.")
@_project_option
def op_slide(comp: str, layer: str, by: str, project_dir: Path | None) -> None:
    """Slide LAYER between its neighbours: it moves with its content, the layer before it
    ends later or earlier and the layer after it starts later or earlier with it."""
    _print_json(ops.slide(_project(project_dir), comp, layer, by))


@op.command("roll")
@click.argument("comp")
@click.argument("left")
@click.argument("right")
@click.option("--by", required=True, help="How far the cut moves: a signed span.")
@_project_option
def op_roll(comp: str, left: str, right: str, by: str, project_dir: Path | None) -> None:
    """Move the cut between LEFT and RIGHT: LEFT's out and RIGHT's in move together."""
    _print_json(ops.roll(_project(project_dir), comp, left, right, by))


@op.command("ripple-delete")
@click.argument("comp")
@click.argument("layer")
@_project_option
def op_ripple_delete(comp: str, layer: str, project_dir: Path | None) -> None:
    """Remove LAYER and move every later layer earlier by its length (the music the beat
    grid comes from stays)."""
    _print_json(ops.ripple_delete(_project(project_dir), comp, layer))


@op.command("split")
@click.argument("comp")
@click.argument("layer")
@click.option("--at", required=True, help="Where, as a comp position.")
@_project_option
def op_split(comp: str, layer: str, at: str, project_dir: Path | None) -> None:
    """Cut LAYER in two at a comp position; the second part is LAYER.2."""
    _print_json(ops.split(_project(project_dir), comp, layer, at))


@op.command("place")
@click.argument("comp")
@click.argument("media")
@click.option("--at", required=True, help="The comp position the layer starts at.")
@_project_option
def op_place(comp: str, media: str, at: str, project_dir: Path | None) -> None:
    """Put MEDIA on the comp at a position, as long as it lasts: footage under any graphics,
    music as an audio layer."""
    _print_json(ops.place(_project(project_dir), comp, media, at))


@op.command("snap")
@click.argument("comp")
@click.argument("layer")
@_project_option
def op_snap(comp: str, layer: str, project_dir: Path | None) -> None:
    """Put LAYER's in and out points on the nearest beats, written as beat spans."""
    _print_json(ops.snap(_project(project_dir), comp, layer))


@op.command("style")
@click.argument("comp")
@click.argument("layer")
@click.argument("style")
@click.option("--at", required=True, help="The comp beat the style's anchor lands on (b:64, bar:16).")
@click.option("--intensity", type=float, help="0 is no effect, 1 the style as written (its own default when absent).")
@_project_option
def op_style(comp: str, layer: str, style: str, at: str, intensity: float | None, project_dir: Path | None) -> None:
    """Place STYLE (styles/STYLE.json) on footage LAYER, anchored on a beat; it replaces
    the layer's earlier style instance. The instance stays linked: editing the style
    changes every placement."""
    _print_json(ops.style(_project(project_dir), comp, layer, style, at, intensity))


@op.command("unstyle")
@click.argument("comp")
@click.argument("layer")
@_project_option
def op_unstyle(comp: str, layer: str, project_dir: Path | None) -> None:
    """Take LAYER's style instance off."""
    _print_json(ops.unstyle(_project(project_dir), comp, layer))


@op.command("key")
@click.argument("comp")
@click.argument("layer")
@click.argument("prop", type=click.Choice(ops.KEYED))
@click.option("--at", required=True, help="The comp position of the key (f:120, b:16, 4.2s).")
@click.option("--value", required=True,
              help="A number, a pair as 0.4,0.6 (position, scale x and y), or a source time for remap (f:80, 2.5s).")
@click.option("--ease", type=click.Choice(["in", "out", "in_out", "hold"]), help="The segment arriving at this key.")
@_project_option
def op_key(comp: str, layer: str, prop: str, at: str, value: str, ease: str | None, project_dir: Path | None) -> None:
    """Set PROP's key on LAYER at a comp position, replacing one already there; the first
    key of a constant property makes it animated."""
    if prop == "remap":
        parsed: Any = value
    elif "," in value:
        parsed = [float(part) for part in value.split(",")]
    else:
        parsed = float(value)
    _print_json(ops.key(_project(project_dir), comp, layer, prop, at, parsed, ease))


@op.command("unkey")
@click.argument("comp")
@click.argument("layer")
@click.argument("prop", type=click.Choice(ops.KEYED))
@click.option("--at", required=True, help="The comp position of the key.")
@_project_option
def op_unkey(comp: str, layer: str, prop: str, at: str, project_dir: Path | None) -> None:
    """Delete PROP's key on LAYER at a comp position; the last transform key leaves its
    value as the property's constant."""
    _print_json(ops.unkey(_project(project_dir), comp, layer, prop, at))


@op.command("set")
@click.argument("comp")
@click.argument("layer")
@click.argument("prop", type=click.Choice(["position", "scale", "opacity", "rotation", "anchor", "gain", "fade_in",
                                           "fade_out"]))
@click.option("--value", required=True,
              help="A number, a pair as 0.4,0.6, a span for a fade (b:2, 1.5s), or none to take a fade off.")
@_project_option
def op_set(comp: str, layer: str, prop: str, value: str, project_dir: Path | None) -> None:
    """Set PROP of LAYER to a constant: a footage layer's transform, an audio layer's gain
    in dB or its fades. A property with keys takes a key instead (lc op key)."""
    if prop in ("fade_in", "fade_out"):
        parsed: Any = None if value == "none" else value
    elif "," in value:
        parsed = [float(part) for part in value.split(",")]
    else:
        parsed = float(value)
    _print_json(ops.set_value(_project(project_dir), comp, layer, prop, parsed))


@op.command("masks")
@click.argument("comp")
@click.argument("layer")
@click.option("--masks", "masks", required=True,
              help='The masks as JSON, in order: \'[{"shape": "ellipse", "size": [0.6, 0.8], "feather": 0.05}]\', [] for none.')
@_project_option
def op_masks(comp: str, layer: str, masks: str, project_dir: Path | None) -> None:
    """Replace LAYER's masks: rectangles and ellipses in the layer's own frame (fractions
    of its width and height), feathered, inverted, combined by add, subtract or intersect."""
    _print_json(ops.set_masks(_project(project_dir), comp, layer, json.loads(masks)))


@op.command("transition")
@click.argument("comp")
@click.argument("kind", type=click.Choice(["dissolve", "dip", "fade_in", "fade_out"]))
@click.argument("layers", nargs=-1, required=True)
@click.option("--length", required=True, help="How long: b:1, 0.5s, f:12; a dissolve and a dip centre on the cut.")
@_project_option
def op_transition(comp: str, kind: str, layers: tuple[str, ...], length: str, project_dir: Path | None) -> None:
    """Put a dissolve or dip on the cut between two footage LAYERS (outgoing, incoming), or
    a fade on one layer; it replaces one of the same kind there."""
    _print_json(ops.transition(_project(project_dir), comp, kind, list(layers), length))


@op.command("untransition")
@click.argument("comp")
@click.argument("kind", type=click.Choice(["dissolve", "dip", "fade_in", "fade_out"]))
@click.argument("layers", nargs=-1, required=True)
@_project_option
def op_untransition(comp: str, kind: str, layers: tuple[str, ...], project_dir: Path | None) -> None:
    """Take a transition off."""
    _print_json(ops.untransition(_project(project_dir), comp, kind, list(layers)))


@op.command("remap")
@click.argument("comp")
@click.argument("layer")
@_project_option
def op_remap(comp: str, layer: str, project_dir: Path | None) -> None:
    """Enable time remapping on LAYER: keys at its in and out points with the source times
    shown there, every frame checked to stay the same."""
    _print_json(ops.remap(_project(project_dir), comp, layer))


@op.command("grade")
@click.argument("comp")
@click.argument("layer")
@click.option("--ops", "operations", required=True,
              help='The grade as JSON, the operations in order: \'[{"op": "primary", "contrast": 1.1}]\', [] for none.')
@_project_option
def op_grade(comp: str, layer: str, operations: str, project_dir: Path | None) -> None:
    """Replace LAYER's grade (docs/design/04-color.md): primary, cdl and lut operations."""
    _print_json(ops.set_grade(_project(project_dir), comp, layer, json.loads(operations)))


@main.command()
@_project_option
def status(project_dir: Path | None) -> None:
    """What is in the project, for an agent arriving: media (footage with its colour space
    and whether it is usable, music with its tempo and whether its licence is recorded),
    comps (size, rate, length, layers by type, markers, transitions), how many editor
    changes lc changes has not shown yet, and each render in cache/lc/render/ with whether
    its comp and media are still the ones it was made from. Changes nothing."""
    from .status import status as project_status

    _print_json(project_status(_project(project_dir)))


@main.command()
@click.option("--peek", is_flag=True, help="Show the changes without moving the agent's place on.")
@_project_option
def changes(peek: bool, project_dir: Path | None) -> None:
    """What the editor wrote since the agent last asked, from the journal of UI writes:
    each entry's file, kind (edit, undo, redo) and changes in editing terms. Run it before
    editing a file by hand, so as not to overwrite what the person just did."""
    _print_json(journal.changes(_project(project_dir).directory, peek=peek))


@main.command()
@click.option("--host", default="127.0.0.1", show_default=True, help="The address to listen on.")
@click.option("--port", default=8770, show_default=True, help="The port to listen on.")
@click.option("--open/--no-open", "open_browser", default=True, show_default=True, help="Open the editor in the browser.")
@_project_option
def serve(host: str, port: int, open_browser: bool, project_dir: Path | None) -> None:
    """Open the project in the editor: a server on HOST:PORT for the browser UI, with the
    viewer, the timeline and its beat grid, playback, and edits that go through the same
    operations as lc op, journalled for lc changes. Files the agent edits meanwhile show up
    in the editor as they change."""
    from .server import serve as run_server

    run_server(_project(project_dir).directory, host, port, open_browser)


@main.command()
@click.option("--write", is_flag=True, help="Regenerate the schema files instead of printing them.")
@click.option(
    "--dir",
    "directory",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("schema"),
    show_default=True,
    help="Where the schema files are written.",
)
def schema(write: bool, directory: Path) -> None:
    """The JSON Schema of every project file, generated from the engine's models."""
    if not write:
        _print_json({name: json.loads(schema_text(model)) for name, model in SCHEMAS.items()})
        return
    directory.mkdir(exist_ok=True)
    written = []
    for name, model in SCHEMAS.items():
        write_atomic(directory / name, schema_text(model))
        written.append(str(directory / name))
    _print_json({"written": written})
