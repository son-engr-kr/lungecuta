"""lc commands that look at frames: still, sheet and scope, of a comp or of one imported clip."""
from __future__ import annotations

from pathlib import Path

import click

from ..scope import scope as scope_frame
from ..sheet import COLUMNS, ROWS, sheet as contact_sheet
from ..still import still as still_frame
from .common import main, one_of, print_json, project_of, project_option, threads_option


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
@threads_option
@click.option(
    "--out",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Where the PNG goes; by default cache/lc/still/ in the project.",
)
@project_option
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
    one_of(comp, key)
    if comp is not None:
        from ..look import comp_still

        print_json(comp_still(project_of(project_dir), comp, at, height=height, threads=threads, out=out))
        return
    print_json(still_frame(project_of(project_dir), key, at, height=height, threads=threads, out=out))


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
@threads_option
@click.option(
    "--out",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Where the first page goes, the others with _p1, _p2 before the suffix; "
    "by default cache/lc/sheet/ in the project.",
)
@project_option
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
    one_of(comp, key)
    if comp is not None:
        from ..look import comp_sheet

        print_json(comp_sheet(project_of(project_dir), comp, every, start=start, end=end, columns=columns, rows=rows,
                               height=height, threads=threads, out=out))
        return
    assert start is None and end is None, "--from and --to are for a comp's sheet"
    print_json(contact_sheet(
        project_of(project_dir), key, every, columns=columns, rows=rows, height=height, threads=threads, out=out,
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
@threads_option
@click.option(
    "--out",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Where the PNG goes; by default cache/lc/scope/ in the project.",
)
@project_option
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
    one_of(comp, key)
    if comp is not None:
        from ..look import comp_scope

        print_json(comp_scope(project_of(project_dir), comp, at, height=height, threads=threads, out=out))
        return
    print_json(scope_frame(project_of(project_dir), key, at, height=height, threads=threads, out=out))
