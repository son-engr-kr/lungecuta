"""lc commands that make and check what leaves the project: render, verify, flash, fcpxml, graphic, the patch card, and the editor's server."""
from __future__ import annotations

import json
from pathlib import Path

import click

from ..encode import PRESETS
from ..fcpxml import export as export_fcpxml
from ..graphics import render as render_graphic
from ..patches import read_patches, write_card
from ..render import render as render_comp
from ..ticks import parse_rate
from ..verify import verify as verify_render
from .common import main, parse_size, print_json, project_of, project_option, rate_option, size_option, threads_option


@main.command()
@click.argument("comp")
@click.option(
    "--out",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Where the FCPXML goes; by default cache/lc/fcpxml/COMP.fcpxml in the project.",
)
@project_option
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
    print_json(export_fcpxml(project_of(project_dir), comp, out))


@main.command()
@click.argument("page", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@size_option
@rate_option
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
    print_json(render_graphic(page, out, size=parse_size(size), rate=parse_rate(rate), frames=frames))


@main.command("patch-card")
@click.argument("directory", type=click.Path(file_okay=False, path_type=Path))
@size_option
@rate_option
@click.option("--frames", type=click.IntRange(min=1), default=150, show_default=True, help="Length of the card.")
def patch_card(directory: Path, size: str, rate: str, frames: int) -> None:
    """A card of known colour patches in DIRECTORY: card.json (each patch's code and
    rectangle), card.html and card.mov (ProRes 4444 with alpha, by lc graphic).

    Put card.mov over footage in a Resolve timeline at full frame, export, and read the
    export with lc patches to see what Resolve did to a Rec.709 graphic. The card's own
    file is read back first and must hold every patch's code.
    """
    print_json(write_card(directory, size=parse_size(size), rate=parse_rate(rate), frames=frames))


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
    print_json(read_patches(file, spec, index=frame, matrix=matrix, range_=range_, chroma_location=chroma_location))


@main.command("render")
@click.argument("comp")
@click.option("--preset", required=True, type=click.Choice(sorted(PRESETS)), help="The delivery preset.")
@click.option("--out", type=click.Path(dir_okay=False, path_type=Path),
              help="The file to write; by default cache/lc/render/COMP.PRESET.EXT in the project.")
@threads_option
@click.option("--workers", type=click.IntRange(min=1),
              help="Frames rendered at once (OCIO single-threaded in each); by default the core count.")
@project_option
def render_command(comp: str, preset: str, out: Path | None, threads: int | None, workers: int | None,
                   project_dir: Path | None) -> None:
    """Render comp COMP to a delivery file, with a manifest of what decided it.

    Every frame goes through the same chain as lc still at the comp's size; music layers
    are mixed at unity gain at 48 kHz. Display-encoded R'G'B' becomes BT.709 limited-range
    Y'CbCr by ITU-T H.273, chroma decimated co-sited, 8-bit output dithered, all four tags
    set on the frames. The manifest (FILE.manifest.json) records the comp's and media's
    hashes, the colour settings and the versions of everything that decided the pixels.
    """
    print_json(render_comp(project_of(project_dir), comp, preset, out, threads, workers))


@main.command("flash")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@threads_option
@project_option
def flash_command(file: Path, threads: int | None, project_dir: Path | None) -> None:
    """WCAG 2.2's general flash threshold on FILE, every frame decoded: no more than three
    flashes (pairs of opposing changes of 0.1 in relative luminance, the darker under 0.8)
    in any second over more than a quarter of any 10 degree field, the field taken as a
    third of the frame's height. Luminance is the display's: the one FILE's manifest names
    (lc render's FILE.manifest.json), or the project's. Red flashes are not checked. Exits
    with status 1 when the file fails."""
    from ..flash import flash

    manifest = file.with_name(file.name + ".manifest.json")
    if manifest.is_file():
        settings = json.loads(manifest.read_text())["color"]
        config_name, display, view = settings["config"], settings["display"], settings["view"]
    else:
        settings = project_of(project_dir).file.color
        config_name, display, view = settings.config, settings.display, settings.view
    result = flash(file, config_name=config_name, display=display, view=view, threads=threads)
    print_json(result)
    if not result["ok"]:
        raise SystemExit(1)


@main.command("verify")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--comp", required=True, help="The comp FILE was rendered from.")
@click.option("--preset", required=True, type=click.Choice(sorted(PRESETS)), help="The preset it was rendered with.")
@threads_option
@project_option
def verify_command(file: Path, comp: str, preset: str, threads: int | None, project_dir: Path | None) -> None:
    """Check FILE against its comp and preset: codec, profile, pixel format, size, rate,
    frame count, the four colour tags, audio format; each cut's first and last frames,
    decoded from the file, against the engine's own reference render (bias per channel,
    PSNR, and closer to its own frame than to a neighbour's); integrated loudness within
    0.2 LU of the preset's target and true peak at or under its ceiling, by ffmpeg's
    ebur128 (EBU R 128-2023). Exits with status 1 when a check fails.
    """
    result = verify_render(project_of(project_dir), comp, file, preset, threads=threads)
    print_json(result)
    if not result["ok"]:
        raise SystemExit(1)


@main.command()
@click.option("--host", default="127.0.0.1", show_default=True, help="The address to listen on.")
@click.option("--port", default=8770, show_default=True, help="The port to listen on.")
@click.option("--open/--no-open", "open_browser", default=True, show_default=True, help="Open the editor in the browser.")
@project_option
def serve(host: str, port: int, open_browser: bool, project_dir: Path | None) -> None:
    """Open the project in the editor: a server on HOST:PORT for the browser UI, with the
    viewer, the timeline and its beat grid, playback, and edits that go through the same
    operations as lc op, journalled for lc changes. Files the agent edits meanwhile show up
    in the editor as they change."""
    from ..server import serve as run_server

    run_server(project_of(project_dir).directory, host, port, open_browser)
