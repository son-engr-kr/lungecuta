"""Music on import: one decode to a 48 kHz float WAV in the cache, and the beat map Beat
This! finds in that WAV (docs/design/02-media.md#music-and-beats, D14)."""
from __future__ import annotations

import contextlib
import struct
import sys
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

import numpy as np

from .files import content_hash, write_atomic
from .schema.media import Analyzer, AudioProbe, BeatMap, ManualBeat, WavRecord
from .tools import run

WAV_RATE = 48000
WAV_DIR = Path("cache") / "audio"
# Beat This!'s default checkpoint ("final0": the main model, trained on all data except
# GTZAN, seed 0) and its default postprocessing ("minimal"), from its README and
# beat_this/inference.py. The DBN postprocessing needs madmom, which is excluded (D14).
CHECKPOINT = "final0"
# The CPU, because PyTorch MPS has not been checked against CPU results (AGENTS.md).
DEVICE = "cpu"
_IEEE_FLOAT = 3


def decode_audio(path: Path, stream: AudioProbe) -> np.ndarray:
    """The first audio stream as float32 samples (frames x channels) at its own rate, as
    ffmpeg's decoder gives them (encoder delay removed where the file states it)."""
    out = run([
        "ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-vn", "-f", "f32le", "-c:a", "pcm_f32le", "-",
    ]).stdout
    signal = np.frombuffer(out, np.float32)
    assert signal.size and signal.size % stream.channels == 0, f"{path.name}: ffmpeg gave {signal.size} samples"
    return signal.reshape(-1, stream.channels)


def to_wav_rate(signal: np.ndarray, rate: int) -> np.ndarray:
    """Resampled to 48 kHz by soxr at its default quality ("HQ"), the resampler Beat This!
    uses itself; a 48 kHz file is left as it is."""
    if rate == WAV_RATE:
        return signal
    import soxr

    return np.ascontiguousarray(soxr.resample(signal, rate, WAV_RATE), dtype=np.float32)


def wav_bytes(signal: np.ndarray, rate: int) -> bytes:
    """A WAVE file of IEEE float samples, with the `fact` chunk a non-PCM format needs."""
    assert signal.dtype == np.float32 and signal.ndim == 2, "expected float32 frames x channels"
    frames, channels = signal.shape
    data = np.ascontiguousarray(signal).astype("<f4").tobytes()
    fmt = struct.pack("<HHIIHHH", _IEEE_FLOAT, channels, rate, rate * channels * 4, channels * 4, 32, 0)
    chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    chunks += b"fact" + struct.pack("<II", 4, frames)
    chunks += b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """Samples (frames x channels, float32) and rate of a float WAVE file as `wav_bytes`
    writes it."""
    data = path.read_bytes()
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE", f"{path} is not a WAVE file"
    at, fmt, samples = 12, None, None
    while at + 8 <= len(data):
        kind, size = data[at:at + 4], struct.unpack("<I", data[at + 4:at + 8])[0]
        body = data[at + 8:at + 8 + size]
        if kind == b"fmt ":
            fmt = struct.unpack("<HHIIHH", body[:16])
        elif kind == b"data":
            samples = body
        at += 8 + size + (size & 1)
    assert fmt is not None and samples is not None, f"{path} has no fmt or data chunk"
    tag, channels, rate, _, _, bits = fmt
    assert (tag, bits) == (_IEEE_FLOAT, 32), f"{path} is not 32-bit float (format {tag}, {bits} bits)"
    return np.frombuffer(samples, "<f4").reshape(-1, channels), rate


def write_wav(project_dir: Path, key: str, path: Path, stream: AudioProbe) -> tuple[WavRecord, np.ndarray]:
    signal = to_wav_rate(decode_audio(path, stream), stream.rate)
    relative = WAV_DIR / f"{key}.wav"
    target = project_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(target, wav_bytes(signal, WAV_RATE))
    record = WavRecord(
        path=relative.as_posix(),
        rate=WAV_RATE,
        channels=signal.shape[1],
        format="pcm_f32le",
        samples=signal.shape[0],
        hash=content_hash(target),
        peak=round(float(np.abs(signal).max()), 6),
        resampled_from=None if stream.rate == WAV_RATE else stream.rate,
    )
    return record, signal


def checkpoint_file() -> Path:
    """Where torch.hub keeps the checkpoint Beat This! downloads on first use."""
    import torch

    return Path(torch.hub.get_dir()) / "checkpoints" / f"beat_this-{CHECKPOINT}.ckpt"


def fetch_checkpoint() -> Path:
    """The checkpoint, downloaded first if torch.hub does not hold it yet. torch.hub
    announces a download on stdout, which carries a command's JSON (and, under lungecuta
    mcp, the result a tool returns), so the announcement goes to stderr."""
    if not checkpoint_file().is_file():
        from beat_this.inference import load_checkpoint

        with contextlib.redirect_stdout(sys.stderr):
            load_checkpoint(CHECKPOINT)
    return checkpoint_file()


def analyse(signal: np.ndarray, rate: int) -> BeatMap:
    """Beats and downbeats of a signal, in seconds from its first sample. Beat This!
    moves every downbeat onto the nearest beat, so each is one of the beat times."""
    from beat_this.inference import Audio2Beats

    fetch_checkpoint()
    tracker = Audio2Beats(checkpoint_path=CHECKPOINT, device=DEVICE, float16=False, dbn=False)
    beats, downbeats = tracker(signal.astype(np.float64), rate)
    times = [float(t) for t in beats]
    index = {t: k for k, t in enumerate(times)}
    for t in downbeats:
        assert float(t) in index, f"downbeat {t} s is not one of the beats"
    analyzer = Analyzer(
        name="beat_this", version=version("beat-this"), checkpoint=CHECKPOINT,
        checkpoint_hash=content_hash(checkpoint_file()), postprocessing="minimal", device=DEVICE,
    )
    return BeatMap(analyzer=analyzer, times=times, downbeats=sorted(index[float(t)] for t in downbeats), manual=[])


def tempo(beats: BeatMap) -> float | None:
    """Beats per minute from the mean beat interval, as the UI shows it; never an input."""
    times = beats.times
    if len(times) < 2:
        return None
    return round(60 * (len(times) - 1) / (times[-1] - times[0]), 3)


@dataclass(frozen=True)
class Reanalysis:
    """A new beat map matched to the current one (docs/design/03-project-format.md#beat-maps):
    each new beat to the nearest current beat within half the median beat interval."""

    current: BeatMap
    new: BeatMap
    pairs: dict[int, int]  # current index -> new index, for beats matched one to one
    inserted: list[int]  # new indices with no current beat of their own
    removed: list[int]  # current indices that no new beat matched

    @property
    def keeps_every_index(self) -> bool:
        """Every current beat has exactly one match, every new beat is matched, and the
        downbeats fall on the same indices, so no beat or bar literal changes meaning."""
        return (
            not self.inserted and not self.removed and len(self.new.times) == len(self.current.times)
            and all(k == j for k, j in self.pairs.items()) and self.new.downbeats == self.current.downbeats
        )

    def merged(self) -> BeatMap:
        """The new map with the manual beats carried over by the matching: a manual beat
        keeps its time at the index of the new beat it matched, and is dropped when no new
        beat matched it (listed in `dropped_manual`)."""
        times = list(self.new.times)
        manual = []
        for beat in self.current.manual:
            if beat.index in self.pairs:
                index = self.pairs[beat.index]
                times[index] = beat.time
                manual.append(ManualBeat(index=index, time=beat.time))
        return BeatMap(analyzer=self.new.analyzer, times=times, downbeats=self.new.downbeats, manual=manual)

    @property
    def dropped_manual(self) -> list[int]:
        return [beat.index for beat in self.current.manual if beat.index not in self.pairs]


def match(current: BeatMap, new: BeatMap) -> Reanalysis:
    old, fresh = np.array(current.times), np.array(new.times)
    assert old.size >= 2, "matching needs a current map of at least two beats"
    window = float(np.median(np.diff(old))) / 2
    claims: dict[int, list[int]] = {}
    unclaimed = []
    for j, t in enumerate(fresh):
        k = int(np.argmin(np.abs(old - t)))
        if abs(old[k] - t) <= window:
            claims.setdefault(k, []).append(j)
        else:
            unclaimed.append(j)
    pairs, inserted = {}, list(unclaimed)
    for k, js in claims.items():
        nearest = min(js, key=lambda j: abs(fresh[j] - old[k]))
        pairs[k] = nearest
        inserted += [j for j in js if j != nearest]
    removed = [k for k in range(old.size) if k not in claims]
    return Reanalysis(current=current, new=new, pairs=pairs, inserted=sorted(inserted), removed=removed)
