"""What each stage of a footage import costs, and how many parity reads to run at once.

Usage: uv run python bench/import_cost.py PROJECT KEY
  (PROJECT holds an imported clip KEY with a decided colour space and its proxy)

Times the decoder check, a rebuild of the proxy (checked identical, frame by frame, to the
one on disk) and the parity test with 1, 2, 4, 8 and 15 reads at once (checked to give the
same numbers every time).
"""
import subprocess
import sys
import time
from pathlib import Path

from lungecuta import proxy
from lungecuta.decode import decoder_check
from lungecuta.media import Project, load_record, original
from lungecuta.ticks import parse_rate


def framemd5(path: Path) -> list[str]:
    lines = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0", "-f", "framemd5", "-"],
                           capture_output=True, text=True, check=True).stdout.splitlines()
    return [line.rsplit(",", 1)[-1].strip() for line in lines if line and not line.startswith("#")]


def main() -> None:
    project = Project.load(Path(sys.argv[1]))
    record = load_record(project, sys.argv[2])
    file, video, made = original(project, record), record.probe.video, project.directory / record.proxy.path
    rate = parse_rate(video.rate)
    t = time.perf_counter()
    check = decoder_check(file, pix_fmt=video.pix_fmt, size=video.size, frames=video.frames)
    print(f"decoder check: {time.perf_counter() - t:.1f} s, {check.checked.result}, {check.checked.frames} frames")
    again = made.with_name(f"bench-{made.name}")
    again.unlink(missing_ok=True)
    t = time.perf_counter()
    proxy.build_proxy(file, again, size=record.proxy.size, coded_size=video.size, source_pix_fmt=video.pix_fmt,
                      source_chroma_location=video.chroma_location, source_range=record.proxy.range,
                      profile=record.proxy.profile, proxy_range=record.proxy.range, frames=video.frames, rate=rate)
    built = time.perf_counter() - t
    same = framemd5(again) == framemd5(made)
    again.unlink()
    print(f"proxy build: {built:.1f} s, {video.frames} frames, identical to the proxy on disk: {same}")
    common = dict(
        indices=record.proxy.parity.frames, rate=rate, coded_size=video.size, size=record.proxy.size,
        source_pix_fmt=video.pix_fmt, source_chroma_location=video.chroma_location,
        proxy_pix_fmt=proxy.PROFILES[record.proxy.profile].decoded_pix_fmt, matrix=record.color.matrix,
        source_range=record.proxy.range, proxy_range=record.proxy.range, config=project.file.color.config,
        spaces=[None, record.color.space],
    )
    first = None
    for reads in (1, 2, 4, 8, 15):
        proxy.PARITY_READS = reads
        t = time.perf_counter()
        numbers = proxy.parity(file, made, **common)
        first = first or numbers
        print(f"parity, {reads:2d} reads at once: {time.perf_counter() - t:.1f} s, same numbers: {numbers == first}")
    print(f"recorded: {record.proxy.parity.codes == first[0] and record.proxy.parity.di == first[1]}")


if __name__ == "__main__":
    main()
