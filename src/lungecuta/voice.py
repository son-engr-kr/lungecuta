"""`lungecuta voice`: voice-over lines spoken by a local text-to-speech model
(docs/design/07-audio.md, Voice-over), each imported as a sound whose record says how it
was made and where every word is.

The model is Kokoro-82M v1.0 (hexgrad, Apache-2.0), as the ONNX graph and voice file
kokoro-onnx publishes, run by onnxruntime on the CPU; nothing leaves the machine. Text
becomes phonemes through misaki, the G2P hexgrad's own pipeline uses, with its lexicon and
no fallback: misaki's fallback for a word it does not know is espeak-ng, which is GPL-3.0
and loaded in-process, so a word misaki cannot read is refused and the line gives its
pronunciation in misaki's markup, `[Kokoro](/kˈOkəɹO/)`. Only the English voices are taken
(`a*` American, `b*` British); the model's other languages need G2Ps lungecuta does not
have, and it has no Korean voice.

A line is one pass of the model, never cut into batches: a line over the model's 510
phonemes is refused. The model's output is kept as it is, its silence before and after
included (nothing is trimmed); the record gives where each token is spoken, from the
durations the model predicts for every phoneme, each a frame of `SAMPLES_PER_FRAME`
samples, so captions and the `speech` anchor follow the words. The vocoder draws a random
phase and noise, so each line is made in a session of its own seeded with `seed`, and the
same line, voice, speed, seed and thread count give the same samples (`bench/voice_check.py`,
`bench/RESULTS.md`)."""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import sys
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from importlib.metadata import version
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from .files import content_hash
from .media import Project, _music, load_record
from .music import wav_bytes
from .probe import probe
from .schema.media import FileStat, MediaKey, MediaPath, MediaRecord, Speech, SpokenToken
from .schema.project import PROJECT_ROOT
from .tools import CACHE, _CHUNK, _sha256

VOICE_DIR = Path("cache") / "lungecuta" / "voice"
MODEL_NAME = "Kokoro-82M v1.0"
RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1"
# Samples a frame of the model's durations: "Multiply by 600 to go from pred_dur frames to
# sample_rate 24000" (hexgrad's kokoro/pipeline.py, join_timestamps), the decoder's upsampling
# (config: 10 x 6 x hop 5, twice); every waveform is 600 times its durations' sum (measured,
# bench/voice_check.py, and asserted on each line)
SAMPLES_PER_FRAME = 600
RATE = 24000  # the model's sample rate (the same comment; the model card's 24 kHz)
MAX_PHONEMES = 510  # 512 positions (config max_position_embeddings) less the pad at each end
UNKNOWN = "❓"  # what misaki writes for a word it cannot read


@dataclass(frozen=True)
class ModelFile:
    name: str
    size: int
    sha256: str  # as GitHub publishes it beside the release asset

    @property
    def url(self) -> str:
        return f"{RELEASE}/{self.name}"


GRAPH = ModelFile("kokoro-v1.0.onnx", 325505369, "beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a")
VOICES = ModelFile("voices-v1.0.bin", 28214398, "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d")
FILES = (GRAPH, VOICES)


def directory() -> Path:
    return CACHE / "kokoro" / "v1.0"


def ready() -> bool:
    """Both files in place, judged by size (`fetch` checked their hashes)."""
    return all((directory() / f.name).is_file() and (directory() / f.name).stat().st_size == f.size for f in FILES)


def fetch(*, opener=urllib.request.urlopen) -> bool:
    """Download the files that are missing, each checked by size and SHA-256 before it is
    moved into place, under a lock so that commands started together download once.
    Progress goes to stderr. Returns whether it fetched anything."""
    place = directory()
    place.mkdir(parents=True, exist_ok=True)
    fetched = False
    with (place / ".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for file in FILES:
            target = place / file.name
            if target.is_file() and target.stat().st_size == file.size:
                continue
            print(f"lungecuta: fetching {MODEL_NAME}'s {file.name} ({file.size / 1e6:.0f} MB) from {file.url}",
                  file=sys.stderr)
            part = place / f"{file.name}.part"
            request = urllib.request.Request(file.url, headers={"User-Agent": "lungecuta"})
            with opener(request, timeout=60) as response, part.open("wb") as out:
                while chunk := response.read(_CHUNK):
                    out.write(chunk)
            pinned = part.stat().st_size == file.size and _sha256(part) == file.sha256
            if not pinned:
                part.unlink()
            assert pinned, f"{file.url} is not the pinned file; the download was discarded"
            os.replace(part, target)
            fetched = True
    return fetched


def _onnxruntime():
    """onnxruntime, imported with its telemetry off. From 1.29.0 its macOS and Linux wheels
    send Microsoft a process's device id, CPU and memory and, sampled, each session's model
    name, graph and weight hashes (its 1.29.0 release notes; core/platform/telemetry_1ds.cc);
    `ORT_DISABLE_TELEMETRY=1` before initialization stops all of it for the process
    (core/platform/telemetry_environment.h), so it is set before the first import."""
    if "onnxruntime" not in sys.modules:
        os.environ["ORT_DISABLE_TELEMETRY"] = "1"
    assert os.environ.get("ORT_DISABLE_TELEMETRY") == "1", "onnxruntime was imported with its telemetry on"
    import onnxruntime

    return onnxruntime


class Line(BaseModel):
    """One voice-over line as `lungecuta voice` takes it."""

    model_config = ConfigDict(extra="forbid")

    key: MediaKey
    text: str = Field(min_length=1, description="what is said, with misaki's markup for a pronunciation")
    caption: str | None = Field(default=None, description="what captions show instead, when it differs")
    voice: str | None = None
    speed: float | None = Field(default=None, gt=0)


@lru_cache(maxsize=2)
def _g2p(british: bool):
    from misaki import en

    return en.G2P(trf=False, british=british, fallback=None)


def phonemize(text: str, voice: str) -> tuple[str, list[dict[str, Any]]]:
    """The phonemes misaki reads `text` as, for an American or British voice, and its tokens
    (text, phonemes, space after); a word it cannot read is refused."""
    assert voice[:1] in ("a", "b"), f"{voice} is not an English voice (a* American, b* British)"
    # misaki reads a run of spaces or a line break as a word it cannot read
    assert text == text.strip() and not re.search(r"\s\s|[^\S ]", text), (
        f"{text!r}: a line is one run of text, words separated by single spaces")
    with contextlib.redirect_stdout(sys.stderr):  # misaki prints a number it cannot read; stdout carries the JSON
        phonemes, tokens = _g2p(voice[0] == "b")(text)
    unread = [token.text for token in tokens if token.phonemes is None or UNKNOWN in token.phonemes]
    assert not unread, (f"misaki cannot read {', '.join(map(repr, unread))}; give the pronunciation in its markup, "
                        "[word](/phonemes/), as the phonemes misaki prints for words like it")
    parts = [{"text": token.text, "phonemes": token.phonemes, "space": bool(token.whitespace)} for token in tokens]
    joined = "".join(token.phonemes + token.whitespace for token in tokens)
    assert joined == phonemes, f"misaki's phonemes {phonemes!r} are not its tokens' {joined!r}"
    assert all(token.whitespace in ("", " ") for token in tokens), "a token is followed by a space or nothing"
    return phonemes, parts


def _graph_config(session) -> dict[str, Any]:
    return json.loads(session.get_modelmeta().custom_metadata_map["kokoro_config"])


def synthesize(phonemes: str, voice: str, speed: float, seed: int, threads: int) -> tuple[np.ndarray, np.ndarray]:
    """The model's waveform (float32 at 24 kHz) for `phonemes` and the frames it gave each
    input, the pads at either end included, in a session of its own seeded with `seed`."""
    assert phonemes, "nothing to say"
    assert len(phonemes) <= MAX_PHONEMES, (
        f"{len(phonemes)} phonemes, over the model's {MAX_PHONEMES}: split the line")
    if not ready():
        fetch()
    rt = _onnxruntime()
    rt.set_seed(seed)
    options = rt.SessionOptions()
    options.intra_op_num_threads = threads
    session = rt.InferenceSession(str(directory() / GRAPH.name), options, providers=["CPUExecutionProvider"])
    vocab = _graph_config(session)["vocab"]
    missing = sorted(set(phonemes) - set(vocab))
    assert not missing, f"phonemes {missing} are not in the model's vocabulary"
    packs = np.load(directory() / VOICES.name)
    assert voice in packs.files, f"{voice!r} is not one of the model's voices ({', '.join(packs.files)})"
    style = packs[voice][style_index(phonemes)]
    ids = np.array([[0, *(vocab[p] for p in phonemes), 0]], np.int64)
    waveform, frames = session.run(None, {"input_ids": ids, "style": style, "speed": np.array([speed], np.float32)})
    assert frames.shape == (len(phonemes) + 2,), f"the model gave {frames.shape} durations for {len(phonemes) + 2} inputs"
    assert waveform.shape == (int(frames.sum()) * SAMPLES_PER_FRAME,), (
        f"{waveform.shape[0]} samples for {int(frames.sum())} frames")
    return waveform.astype(np.float32), frames.astype(np.int64)


def style_index(phonemes: str) -> int:
    """Which row of a voice's pack styles a line of these phonemes: a voice holds one style
    for each length, and n phonemes take row n - 1 (`pack[len(ps)-1]` in hexgrad's
    kokoro/pipeline.py; kokoro-onnx 0.6.1 does the same)."""
    return len(phonemes) - 1


def placed(parts: list[dict[str, Any]], frames: np.ndarray) -> list[SpokenToken]:
    """Each token from its first phoneme's start to its last phoneme's end, in samples: the
    phonemes are input 1 on (input 0 is the pad), each lasting its frames."""
    edges = np.concatenate([[0], np.cumsum(frames)]) * SAMPLES_PER_FRAME
    out, at = [], 1
    for part in parts:
        first, at = at, at + len(part["phonemes"])
        out.append(SpokenToken(text=part["text"], phonemes=part["phonemes"], space=part["space"],
                               start=int(edges[first]), end=int(edges[at])))
        at += part["space"]
    assert at == len(frames) - 1, f"the tokens cover {at - 1} phonemes of {len(frames) - 2}"
    return out


def threads_default() -> int:
    """onnxruntime's own default on this machine, its core count; recorded, since one or two
    threads give other samples than four or more (bench/RESULTS.md)."""
    return os.cpu_count() or 1


def _made(project: Project, line: Line, voice: str, speed: float, seed: int, threads: int) -> tuple[Path, Speech]:
    """The line spoken and written as a float WAV in the cache, named by what made it."""
    phonemes, parts = phonemize(line.text, voice)
    waveform, frames = synthesize(phonemes, voice, speed, seed, threads)
    tokens = placed(parts, frames)
    hashes = {f.name: "sha256:" + f.sha256 for f in FILES}
    speech = Speech(by="kokoro", model=MODEL_NAME, model_hash=hashes[GRAPH.name], voices_hash=hashes[VOICES.name],
                    onnxruntime=version("onnxruntime"), misaki=version("misaki"), voice=voice, speed=speed, seed=seed,
                    threads=threads, text=line.text, caption=line.caption, phonemes=phonemes, rate=RATE,
                    samples=int(waveform.shape[0]), tokens=tokens)
    made = speech.model_dump(mode="json", exclude={"tokens", "samples", "caption"})
    digest = hashlib.sha256(json.dumps(made, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    target = project.directory / VOICE_DIR / f"{line.key}.{digest}.wav"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(wav_bytes(waveform[:, None], RATE))
    return target, speech



def speak(project: Project, lines: list[Line], *, voice: str | None = None, speed: float = 1.0, seed: int = 0,
          threads: int | None = None) -> list[MediaRecord]:
    """Each line spoken (in its own voice and speed, or `voice` and `speed`) and imported as a
    sound with its `speech`. Keys are checked to be free, and every line to be readable,
    before any is spoken."""
    threads = threads or threads_default()
    keys = [line.key for line in lines]
    assert lines, "give a line to say"
    assert len(set(keys)) == len(keys), f"keys repeat: {sorted({k for k in keys if keys.count(k) > 1})}"
    for key in keys:
        assert not (project.directory / "media" / f"{key}.json").exists(), (
            f"media key {key!r} exists already; give the line another key")
    chosen = [(line, line.voice or voice, line.speed or speed) for line in lines]
    for line, said_by, _ in chosen:
        assert said_by is not None, f"line {line.key}: say which voice speaks it (--voice, or the line's voice)"
        phonemize(line.text, said_by)
    return [_import(project, line.key, *_made(project, line, said_by, rate, seed, threads))
            for line, said_by, rate in chosen]


def _import(project: Project, key: str, target: Path, speech: Speech) -> MediaRecord:
    """The spoken file imported as a sound of the project, with its speech."""
    from .files import json_text, write_new

    probed = probe(target)
    stat = target.stat()
    common = dict(key=key, path=MediaPath(root=PROJECT_ROOT, rel=target.relative_to(project.directory).as_posix()),
                  hash=content_hash(target), stat=FileStat(size=stat.st_size, mtime_ns=stat.st_mtime_ns),
                  probe=probed, derived_from=None)
    record = _music(project, target, key, probed, common, sound=True).model_copy(update={"speech": speech})
    record = MediaRecord.model_validate(record.model_dump())
    write_new(project.directory / "media" / f"{key}.json", json_text(record.model_dump(mode="json")))
    return record


def again(project: Project, keys: list[str]) -> list[str]:
    """Make the files of voice-over lines again, as their records say, where the cache has
    lost them; each must come out identical to the one imported. Returns the keys made."""
    out = []
    for key in keys:
        record = load_record(project, key)
        speech = record.speech
        assert speech is not None, f"{key} is not a voice-over line"
        path = project.directory / record.path.rel
        if path.is_file():
            continue
        assert speech.model_hash == "sha256:" + GRAPH.sha256 and speech.voices_hash == "sha256:" + VOICES.sha256, (
            f"{key} was made with other model files than this lungecuta's")
        line = Line(key=key, text=speech.text, caption=speech.caption)
        target, _ = _made(project, line, speech.voice, speech.speed, speech.seed, speech.threads)
        assert target == path, f"{key}: made again as {target.name}, recorded as {path.name}"
        assert content_hash(target) == record.hash, (
            f"{key}: made again, its samples differ from the ones imported (another onnxruntime, or another "
            f"machine?); delete media/{key}.json and say it anew")
        out.append(key)
    return out


def summary(record: MediaRecord) -> dict[str, Any]:
    """What `lungecuta voice` prints for one line."""
    speech = record.speech
    first, last = speech.spoken
    return {"key": record.key, "seconds": round(speech.samples / speech.rate, 3),
            "speech": [round(first / speech.rate, 3), round(last / speech.rate, 3)], "voice": speech.voice,
            "speed": speech.speed, "phonemes": speech.phonemes,
            "words": [{"text": t.text, "at": round(t.start / speech.rate, 3)} for t in speech.tokens if t.word]}
