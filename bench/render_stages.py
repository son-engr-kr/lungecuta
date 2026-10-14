"""What one comp frame costs in `lungecuta render`, stage by stage, on one thread (as each of the
render's workers runs it) or on all cores.

Usage: uv run python bench/render_stages.py PROJECT COMP FRAME [THREADS]

The frame's raw layers come from the render's own sequential read; each stage of
`compose.compose` is then timed on them, three times, and the mean printed in ms. The
frame has footage only (no graphics): `finish` is the output view.
"""
import sys
import time
from pathlib import Path

from lungecuta import color, compose
from lungecuta.comp import load_comp, resolve
from lungecuta.decode import frame_rgb
from lungecuta.encode import PRESETS, frame_bytes
from lungecuta.media import Project


def main() -> None:
    project = Project.load(Path(sys.argv[1]))
    resolved = resolve(project, load_comp(project, sys.argv[2]))
    index = int(sys.argv[3])
    threads = int(sys.argv[4]) if len(sys.argv) > 4 else None
    assert not resolved.overlays, "a frame with footage only"
    raws = next(raws for k, raws in enumerate(compose.raw_layers(project, resolved)) if k == index)
    settings = project.file.color
    spent: dict[str, float] = {}

    def timed(name, work):
        start = time.perf_counter()
        result = work()
        spent[name] = spent.get(name, 0.0) + time.perf_counter() - start
        return result

    for _ in range(3):
        footage = {}
        for cut, sources in raws:
            video = cut.record.probe.video
            into = color.cpu_processor(settings.config, cut.record.color.space, settings.working)
            working = []
            for data, weight in sources:
                decoded = timed("frame_rgb", lambda: frame_rgb(
                    data, video.size, pix_fmt=video.pix_fmt, chroma_location=video.chroma_location,
                    matrix=cut.record.color.matrix, range_=cut.record.color.range))
                timed("input transform", lambda: color.apply(into, decoded, threads))
                working.append((decoded, weight))
            shown = timed("shown (grade)", lambda: compose.shown(project, cut, working, threads))
            footage[cut.layer.id] = timed("place", lambda: compose.in_comp(project, cut, index, shown,
                                                                            tuple(resolved.comp.size)))
        out = timed("finish (output view)", lambda: compose.finish(project, resolved, index, footage, {}, threads))
        timed("frame_bytes", lambda: frame_bytes(out, PRESETS["web-h264"], seed=index))
    print({name: round(seconds / 3 * 1000, 1) for name, seconds in spent.items()}, "ms")


if __name__ == "__main__":
    main()
