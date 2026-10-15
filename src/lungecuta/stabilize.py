"""`lungecuta stabilize`: a clip stabilized, or only its rolling shutter corrected, from the
gyro, IBIS and lens data the camera recorded, by the pinned Gyroflow (`gyroflow.py`), into a
derived clip in the project's cache, imported as derived media of the original
(docs/design/02-media.md, Carrying the decision through the ingest scripts).

The derived clip holds every frame of the original, frame for frame, so a source time means
the same picture in both, and takes the original's colour and time decisions, since it has
no sidecar of its own: Gyroflow passes the stream's tags and range through (checked on each
clip) and moves its code values by a fraction of a code (`bench/RESULTS.md`, Gyroflow). By
default it is lossless H.264 (all intra, the original's pixel format), so the only change is
Gyroflow's warp; `output` gives other codecs, and `preset` any of Gyroflow's stabilization
settings, both as Gyroflow takes them.

- `stabilize`: Gyroflow's own defaults, which read the readout time, the lens and the IBIS
  movement from the clip.
- `rolling-shutter`: the smoothing method "No smoothing", which makes the stabilized path
  the measured one, so only the per-scanline warp is left, and a static zoom
  (`adaptive_zoom_window` -1), one crop for the whole clip, so the framing does not
  breathe as Gyroflow's default dynamic zoom makes it (*proposed*). Lens correction stays
  at Gyroflow's 1.0: on a Sony clip recorded with the camera's distortion compensation, 0
  adds the lens distortion back (measured, `bench/RESULTS.md`)."""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from . import gyroflow
from .decode import decoder_check
from .files import content_hash, json_text, write_new
from .media import Project, _proxy, load_record, original
from .probe import probe
from .schema.media import ColorDecided, Derivation, FileStat, MediaPath, MediaRecord
from .schema.project import PROJECT_ROOT

DERIVED_DIR = Path("cache") / "lungecuta" / "derived"
MODES = {"stabilize": {}, "rolling-shutter": {"stabilization": {"method": "No smoothing", "adaptive_zoom_window": -1}}}
SUFFIXES = {"rolling-shutter": "rs", "stabilize": "stab"}
# Lossless all-intra H.264 in the original's pixel format, so the only change is the warp
LOSSLESS = {"codec": "H.264/AVC", "use_gpu": False, "encoder_options": "-qp 0 -g 1"}
# The original's PCM, kept as it is (Gyroflow's names for ffmpeg's codecs, from its CLI help)
PCM = {"pcm_s16be": "PCM (s16be)", "pcm_s16le": "PCM (s16le)", "pcm_s24be": "PCM (s24be)", "pcm_s24le": "PCM (s24le)"}
CONTAINERS = {"ProRes": ".mov", "DNxHD": ".mov", "CineForm": ".mov"}  # anything else Gyroflow writes as .MP4


def merged(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """`over` laid on `base`, objects merged key by key."""
    out = copy.deepcopy(base)
    for key, value in over.items():
        out[key] = merged(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def settings(record: MediaRecord, mode: str, preset: dict[str, Any] | None,
             output: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Gyroflow's preset and output parameters for `record` in `mode`."""
    assert mode in MODES, f"{mode!r} is not a mode ({', '.join(MODES)})"
    video = record.probe.video
    chosen = merged({"version": 2} | MODES[mode], preset or {})
    given = output or {}
    out = LOSSLESS | {"pixel_format": video.pix_fmt, "audio": False}
    if record.probe.audio and given.get("audio", True):
        codec = record.probe.audio[0].codec
        assert codec in PCM or "audio_codec" in given, (
            f"{record.key}'s sound is {codec}, which Gyroflow would encode again; give --output "
            "'{\"audio\": false}' (its sound is then not in the stabilized clip) or an audio_codec")
        out |= {"audio": True, "audio_codec": PCM.get(codec)}
    out = merged(out, given)
    assert "output_path" not in out, "lungecuta chooses where the clip is written"
    assert out["codec"] not in ("EXR Sequence", "PNG Sequence"), "an image sequence is not a clip lungecuta imports"
    return chosen, out


def stabilize(project: Project, key: str, *, mode: str = "stabilize", preset: dict[str, Any] | None = None,
              output: dict[str, Any] | None = None, into: str | None = None, proxy: bool = True) -> MediaRecord:
    """Make the derived clip of `key` and import it as `into` (by default `KEY.stab` or
    `KEY.rs`)."""
    record = load_record(project, key)
    video = record.probe.video
    assert video is not None and record.image is None, f"{key} is not footage"
    assert record.derived_from is None, f"{key} is derived from {record.derived_from}; stabilize the original"
    chosen, out = settings(record, mode, preset, output)
    into = into or f"{key}.{SUFFIXES[mode]}"
    record_path = project.directory / "media" / f"{into}.json"
    assert not record_path.exists(), f"media key {into!r} exists already; give another with --key"
    source = original(project, record)
    made = {"original": record.hash, "preset": chosen, "output": out, "gyroflow": gyroflow.RELEASE.version}
    digest = hashlib.sha256(json.dumps(made, sort_keys=True).encode()).hexdigest()[:16]
    place = project.directory / DERIVED_DIR
    place.mkdir(parents=True, exist_ok=True)
    target = place / f"{into}.{digest}{CONTAINERS.get(out['codec'], '.MP4')}"
    if not target.exists():
        # Gyroflow is given a link to the original in a folder of its own, so that nothing it
        # writes beside its input (a project file, for one) lands beside the original
        with tempfile.TemporaryDirectory(dir=place, prefix=".work-") as work:
            link = Path(work) / source.name
            link.symlink_to(source)
            partial = Path(work) / f"out{target.suffix}"
            gyroflow.run([str(link), "-f", "--preset", json.dumps(chosen), "-p", json.dumps(out | {"output_path": str(partial)})])
            assert partial.is_file(), f"Gyroflow wrote no clip for {key}"
            partial.rename(target)
    probed = probe(target)
    made_video = probed.video
    assert made_video is not None and made_video.frames == video.frames, (
        f"Gyroflow wrote {made_video.frames if made_video else 0} frames of {key}'s {video.frames}")
    assert made_video.rate == video.rate, f"Gyroflow wrote {made_video.rate} fps for {video.rate}"
    assert made_video.tags.color_range == video.tags.color_range, (
        f"Gyroflow wrote range {made_video.tags.color_range} for {video.tags.color_range}")
    evidence = {"derived_from": key, "by": f"Gyroflow {gyroflow.RELEASE.version}", "mode": mode}
    color = record.color
    if isinstance(color, ColorDecided):
        color = color.model_copy(update={"evidence": color.evidence | evidence})
    stat = target.stat()
    derived = MediaRecord(
        key=into, path=MediaPath(root=PROJECT_ROOT, rel=target.relative_to(project.directory).as_posix()),
        hash=content_hash(target), stat=FileStat(size=stat.st_size, mtime_ns=stat.st_mtime_ns), probe=probed,
        time=record.time.model_copy(update={"evidence": record.time.evidence | evidence}), color=color,
        decode=decoder_check(target, pix_fmt=made_video.pix_fmt, size=made_video.size, frames=made_video.frames),
        proxy=_proxy(project, target, into, color, made_video) if proxy else None, wav=None, beats=None,
        derived_from=key, licence=record.licence,
        derivation=Derivation(by="gyroflow", version=gyroflow.RELEASE.version, mode=mode, preset=chosen, output=out,
                              original_hash=record.hash),
    )
    write_new(record_path, json_text(derived.model_dump(mode="json")))
    return derived
