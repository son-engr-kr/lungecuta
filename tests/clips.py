"""Small synthetic clips made with ffmpeg, and hand-written media records for them, for the
tests of the commands that look at frames (the sample media are in test_samples.py)."""
import json
from pathlib import Path

from lungecuta.files import content_hash, json_text
from lungecuta.probe import probe
from lungecuta.schema.media import (
    Analyzer,
    BeatMap,
    ColorDecided,
    ColorUndecided,
    DecodeRecord,
    DecoderCheck,
    FileStat,
    MediaPath,
    MediaRecord,
    TimeDecided,
    WavRecord,
)
from lungecuta.tools import run

SPACE = "S-Log3 S-Gamut3.Cine"
SIZE = (128, 72)


def make_clip(path: Path, rotation: int | None = None, *, hflip: bool = False, frames: int = 3) -> Path:
    """Frames of ffmpeg's moving test pattern as lossless all-intra H.264, 10-bit 4:2:2 with
    its chroma sited left, like the camera's (ProRes does not carry chroma siting)."""
    plain = path.with_name(f"plain{frames}-{path.name}")
    if not plain.exists():
        run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size={SIZE[0]}x{SIZE[1]}:rate=30000/1001",
             "-frames:v", str(frames), "-c:v", "libx264", "-qp", "0", "-g", "1", "-pix_fmt", "yuv422p10le",
             "-chroma_sample_location", "left", str(plain)])
    tags = [] if rotation is None else ["-display_rotation", str(rotation)]
    run(["ffmpeg", "-v", "error", *tags, *(["-display_hflip"] if hflip else []), "-i", str(plain),
         "-c", "copy", str(path)])
    return path


CLICK = 1.0  # seconds of the sound a clip with sound has its one click at


def clip_with_sound(path: Path, *, frames: int = 70, lead: float = 0.0) -> Path:
    """The test pattern with a sound: silence and one click of 0.5 at CLICK seconds of the
    sound, as 24-bit PCM at 48 kHz like the camera's; with `lead`, the sound starts that
    many seconds after the picture (by its stream's start time)."""
    plain = make_clip(path.with_name(f"silent-{path.name}"), frames=frames)
    seconds = frames * 1001 / 30000 - lead
    run(["ffmpeg", "-v", "error", "-i", str(plain), *(["-itsoffset", str(lead)] if lead else []), "-f", "lavfi",
         "-i", f"aevalsrc=if(eq(n\\,{round(CLICK * 48000)})\\,0.5\\,0):s=48000:c=stereo:d={seconds:.6f}",
         "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "pcm_s24le", str(path)])
    return path


def write_record(project: Path, clip: Path, *, decided: bool = True, start_tc: str | None = None,
                 proxy: bool = False) -> str:
    """A media record for a clip under the project root, decided by hand (no sidecar exists);
    with `proxy`, an FFV1 proxy built as `lungecuta import` builds it, at the project's proxy_height."""
    probed = probe(clip)
    stat = clip.stat()
    color = (ColorDecided(status="decided", space=SPACE, matrix="bt709", range="limited", evidence={"source": "test"})
             if decided else ColorUndecided(status="undecided", reason="no mapping for test", evidence={}))
    record = MediaRecord(
        key=clip.stem,
        path=MediaPath(root="project", rel=clip.relative_to(project).as_posix()),
        hash=content_hash(clip),
        stat=FileStat(size=stat.st_size, mtime_ns=stat.st_mtime_ns),
        probe=probed,
        time=TimeDecided(status="decided", rate=probed.video.rate, capture_rate=None, start_tc=start_tc, evidence={}),
        color=color,
        decode=DecodeRecord(decoder="software", checked=DecoderCheck(
            method="framemd5", frames=probed.video.frames, against="videotoolbox", result="unchecked", detail="test")),
        proxy=_proxy(project, clip, probed) if proxy else None,
        wav=None,
        beats=None,
        derived_from=None,
    )
    (project / "media" / f"{clip.stem}.json").write_text(json_text(record.model_dump(mode="json")))
    return clip.stem


def _proxy(project: Path, clip: Path, probed):
    from lungecuta.project import PROJECT_FILE
    from lungecuta.proxy import build_proxy, proxy_size
    from lungecuta.schema.media import Parity, ProxyRecord
    from lungecuta.ticks import parse_rate

    video = probed.video
    height = json.loads((project / PROJECT_FILE).read_text())["proxy_height"]
    size = proxy_size(video.size, height)
    relative = Path("cache") / "proxy" / f"{clip.stem}.mkv"
    (project / relative).parent.mkdir(parents=True, exist_ok=True)
    build_proxy(clip, project / relative, size=size, coded_size=video.size, source_pix_fmt=video.pix_fmt,
                source_chroma_location=video.chroma_location, source_range="limited", profile="ffv1-444",
                proxy_range="limited", frames=video.frames, rate=parse_rate(video.rate))
    return ProxyRecord(path=relative.as_posix(), profile="ffv1-444", pix_fmt="yuv444p10le", size=size,
                       matrix="bt709", range="limited",
                       parity=Parity(frames=[], codes=None, di=None, reason="a test record"))


def stored(project: Path, key: str) -> MediaRecord:
    return MediaRecord.model_validate_json((project / "media" / f"{key}.json").read_text())


def drum_track(*, rate: int, bpm: float = 120.0, first: float = 0.25, bars: int = 12):
    """A synthetic 4/4 drum track whose beats are known: a kick on every beat (louder on
    the downbeat), snare on 2 and 4, eighth-note hats, and a bass note a bar that marks
    each downbeat. Returns float32 stereo samples, the beat times and the downbeat indices."""
    import numpy as np

    beat = 60 / bpm
    out = np.zeros(int((first + bars * 4 * beat + 1.0) * rate))
    t = np.arange(int(0.25 * rate)) / rate
    kick = np.sin(2 * np.pi * np.cumsum(50 + 100 * np.exp(-t / 0.02)) / rate) * np.exp(-t / 0.12)
    rng = np.random.default_rng(0)
    snare = rng.standard_normal(t.size) * np.exp(-t / 0.05) * 0.5
    hat_t = np.arange(int(0.05 * rate)) / rate
    hat = rng.standard_normal(hat_t.size) * np.exp(-hat_t / 0.01) * 0.2
    notes = [55.0 * 2 ** (step / 12) for step in (0, 5, 7, 3)]
    times = []
    for k in range(bars * 4):
        at = first + k * beat
        times.append(at)
        i = round(at * rate)
        out[i:i + kick.size] += kick * (1.0 if k % 4 == 0 else 0.7)
        if k % 4 in (1, 3):
            out[i:i + snare.size] += snare
        for half in (0.0, 0.5):
            j = round((at + half * beat) * rate)
            out[j:j + hat.size] += hat
        if k % 4 == 0:
            length = round(4 * beat * rate)
            tb = np.arange(length) / rate
            out[i:i + length] += 0.3 * np.sin(2 * np.pi * notes[(k // 4) % 4] * tb) * np.exp(-tb / 1.5)
    out /= np.abs(out).max() * 1.1
    return np.stack([out, out], 1).astype(np.float32), times, list(range(0, bars * 4, 4))


def write_music(project: Path, key: str, *, times: list[float], downbeats: list[int], seconds: float,
                silent: bool = False) -> str:
    """A music record written by hand: a 48 kHz WAV as both the original (under the
    project's footage/) and the cache WAV, with the beat map given. The sound is a quiet
    220 Hz tone with a click on every beat, or silence."""
    import numpy as np

    from lungecuta.music import wav_bytes

    t = np.arange(round(seconds * 48000)) / 48000
    signal = np.zeros_like(t) if silent else 0.05 * np.sin(2 * np.pi * 220 * t)
    if not silent:
        for beat in times:
            first = round(beat * 48000)
            signal[first:first + 480] += 0.4 * np.exp(-np.arange(signal[first:first + 480].size) / 96)
    signal = np.stack([signal, signal], 1).astype(np.float32)
    data = wav_bytes(signal, 48000)
    source = project / "footage" / f"{key}.wav"
    source.write_bytes(data)
    cached = project / "cache" / "audio" / f"{key}.wav"
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(data)
    stat = source.stat()
    analyzer = Analyzer(name="beat_this", version="test", checkpoint="final0", checkpoint_hash="sha256:" + "0" * 64,
                        postprocessing="minimal", device="cpu")
    record = MediaRecord(
        key=key,
        path=MediaPath(root="project", rel=source.relative_to(project).as_posix()),
        hash=content_hash(source),
        stat=FileStat(size=stat.st_size, mtime_ns=stat.st_mtime_ns),
        probe=probe(source),
        time=None, color=None, decode=None, proxy=None,
        wav=WavRecord(path=cached.relative_to(project).as_posix(), rate=48000, channels=2, format="pcm_f32le",
                      samples=round(seconds * 48000), hash=content_hash(cached),
                      peak=round(float(np.abs(signal).max()), 6), resampled_from=None),
        beats=BeatMap(analyzer=analyzer, times=times, downbeats=downbeats, manual=[]),
        derived_from=None,
    )
    (project / "media" / f"{key}.json").write_text(json_text(record.model_dump(mode="json")))
    return key


# The hand-written music: 120 BPM, the first beat at 0.25 s, a downbeat every four beats.
BEATS = [0.25 + 0.5 * k for k in range(20)]


def comp_json(**changes):
    comp = {
        "id": "main", "size": [128, 72], "rate": "30000/1001", "end": "b:12",
        "beats": {"from_layers": ["music"]},
        "markers": [{"t": "b:4", "name": "drop", "note": "the hit"}],
        "layers": [
            {"id": "music", "type": "audio", "media": "track", "start": "0s", "out": "10s", "source_start": "0s"},
            {"id": "shot1", "type": "footage", "media": "C0001", "start": "b:0", "out": "b:4", "source_start": "f:2",
             "markers": [{"t": "b:1", "name": "hit"}]},
            {"id": "shot2", "type": "footage", "media": "C0001", "start": "b:4", "out": "b:4", "source_start": "0s",
             "time": {"speed": 0.5}},
        ],
    }
    comp.update(changes)
    return comp


def put_comp(project, comp):
    (project / "comps" / f"{comp['id']}.json").write_text(json.dumps(comp))
    settings = json.loads((project / "project.json").read_text())
    if f"comps/{comp['id']}.json" not in settings["comps"]:
        settings["comps"].append(f"comps/{comp['id']}.json")
        (project / "project.json").write_text(json.dumps(settings))


def cut_project(tmp_path: Path) -> Path:
    """A project with the hand-written music, a 70-frame clip at timecode 00:00:21:15, and comp `main`."""
    from lungecuta.project import init_project

    directory = init_project(tmp_path / "edit.lungecuta")
    (directory / "footage").mkdir()
    settings = json.loads((directory / "project.json").read_text())
    settings["proxy_height"] = 36  # the 128x72 clips get 64x36 proxies, and the editor previews at 64x36
    (directory / "project.json").write_text(json.dumps(settings))
    write_music(directory, "track", times=BEATS, downbeats=list(range(0, 20, 4)), seconds=10.0)
    write_record(directory, make_clip(directory / "footage" / "C0001.mov", frames=70), start_tc="00:00:21:15",
                 proxy=True)
    put_comp(directory, comp_json())
    return directory


def beat_map(times, downbeats=(0,), manual=()) -> BeatMap:
    """A beat map with a placeholder analyzer, for tests of matching."""
    analyzer = Analyzer(name="beat_this", version="1.1.0", checkpoint="final0", checkpoint_hash="sha256:" + "0" * 64,
                        postprocessing="minimal", device="cpu")
    return BeatMap(analyzer=analyzer, times=list(times), downbeats=list(downbeats), manual=list(manual))


# A 128x72 title page with the past pages' contract: an opaque box in the colour its params
# give, and a white box that fades in over the first second of layer time.
TITLE = """<!doctype html><html><head><style>html,body{margin:0;background:transparent}</style></head><body>
<canvas id="c" width="128" height="72"></canvas><script>
const params = JSON.parse(new URLSearchParams(location.search).get("params") || "{}");
const ctx = document.getElementById("c").getContext("2d");
window.renderAt = (t) => {
  ctx.clearRect(0, 0, 128, 72);
  ctx.fillStyle = params.colour;                       // opaque: its codes come through as they are
  ctx.fillRect(4, 4, 40, 20);
  ctx.fillStyle = `rgba(255, 255, 255, ${Math.min(1, t)})`;  // fades in over the first second of layer time
  ctx.fillRect(60, 40, 40, 20);
};
window.ready = true;
</script></body></html>"""
