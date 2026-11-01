"""Media records, one file each under `media/` (docs/design/03-project-format.md#media)."""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from .project import RootName

MediaKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
Rate = Annotated[str, StringConstraints(pattern=r"^[1-9]\d*(/[1-9]\d*)?$")]


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MediaPath(_Record):
    """A file as a path relative to a named media root (`project` is the project itself)."""

    root: RootName
    rel: str

    @field_validator("rel")
    @classmethod
    def _relative_inside_the_root(cls, rel: str) -> str:
        path = PurePosixPath(rel)
        assert not path.is_absolute() and rel == str(path), f"{rel!r} is not a plain relative path"
        assert ".." not in path.parts, f"{rel!r} leaves its root"
        return rel


class FileStat(_Record):
    """What `lungecuta` compares on open to decide whether a file must be hashed again."""

    size: int
    mtime_ns: int


class StreamTags(_Record):
    """The stream's colour tags as ffprobe names them; `unknown` where the stream has none."""

    color_range: str
    color_space: str
    color_transfer: str
    color_primaries: str


class VideoProbe(_Record):
    codec: str
    profile: str | None
    pix_fmt: str
    size: tuple[int, int]
    rate: Rate
    frames: int
    tags: StreamTags
    chroma_location: str = Field(description="where chroma samples sit, as ffprobe names it; unspecified when not stated")
    rotation: int


class AudioProbe(_Record):
    codec: str
    channels: int
    layout: str | None
    rate: int


class Probe(_Record):
    video: VideoProbe | None
    audio: list[AudioProbe]
    data_streams: int
    timecode: str | None
    creation_time: str | None


class TimeDecided(_Record):
    status: Literal["decided"]
    rate: Rate
    capture_rate: Rate | None = Field(description="the camera's capture rate, when a sidecar states it")
    start_tc: str | None
    evidence: dict[str, Any]


class TimeUndecided(_Record):
    status: Literal["undecided"]
    rate: Rate
    reason: str
    start_tc: str | None
    evidence: dict[str, Any]


class ColorDecided(_Record):
    status: Literal["decided"]
    space: str = Field(description="an OpenColorIO colour space of the project's config")
    matrix: Literal["bt709"]
    range: Literal["full", "limited"]
    evidence: dict[str, Any]


class ColorUndecided(_Record):
    status: Literal["undecided"]
    reason: str
    evidence: dict[str, Any]


class DecoderCheck(_Record):
    # "bytes": the frames compared byte for byte; "framemd5": by ffmpeg's framemd5 (records
    # imported before 2026-10-13); both find the same decoders identical
    method: Literal["bytes", "framemd5"]
    frames: int  # how many frames were compared: all of the first 300, or up to the first that was not identical
    against: Literal["videotoolbox"]
    result: Literal["identical", "different", "failed", "unchecked"]
    detail: str | None


class DecodeRecord(_Record):
    decoder: Literal["software", "videotoolbox"]
    checked: DecoderCheck


class ParityNumbers(_Record):
    """|proxy - area-downscaled original| over every pixel and channel, in 10-bit codes."""

    p99: float
    max: float
    bias: tuple[float, float, float] = Field(description="mean signed difference per channel")
    p99_blocks: float = Field(description="p99 after averaging 4x4 blocks: level rather than noise")


class Parity(_Record):
    """The proxy's parity test (docs/design/02-media.md#proxies). The gate is `codes`, in
    the file's own R'G'B' codes (provisional); `di` is recorded for the open decision."""

    frames: list[int]
    codes: ParityNumbers | None
    di: ParityNumbers | None
    reason: str | None = Field(description="why there are no numbers yet")


class ProxyRecord(_Record):
    path: str
    profile: Literal["ffv1-444", "ffv1-422", "prores-proxy", "prores-lt", "prores-hq", "prores-4444"]
    pix_fmt: Literal["yuv444p10le", "yuv422p10le", "yuv444p12le"]
    size: tuple[int, int]
    matrix: Literal["bt709"] | None = Field(description="the original's matrix, once it is decided")
    range: Literal["full", "limited"] = Field(description="the original's range, kept without conversion")
    parity: Parity


class WavRecord(_Record):
    """Music decoded once at import to a 48 kHz float WAV in the cache, which the beat
    analysis, the mix and the Phase 0 FCPXML all read (D14)."""

    path: str
    rate: Literal[48000]
    channels: int = Field(gt=0)
    format: Literal["pcm_f32le"]
    samples: int = Field(gt=0, description="sample frames per channel")
    hash: Sha256
    peak: float = Field(description="largest absolute sample; above 1 the decode goes over full scale")
    resampled_from: int | None = Field(description="the original's rate, when it was not 48 kHz")


class Analyzer(_Record):
    name: Literal["beat_this"]
    version: str
    checkpoint: str
    checkpoint_hash: Sha256
    postprocessing: Literal["minimal"]
    device: Literal["cpu"]


class ManualBeat(_Record):
    index: int = Field(ge=0)
    time: float


class BeatMap(_Record):
    """Beats of a music file, in seconds from the first sample of its WAV
    (docs/design/03-project-format.md#beat-maps)."""

    analyzer: Analyzer
    times: list[float]
    downbeats: list[int] = Field(description="indices into times")
    manual: list[ManualBeat] = Field(description="beats moved by hand, which re-analysis keeps")

    @model_validator(mode="after")
    def _consistent(self) -> BeatMap:
        times = self.times
        assert times and times[0] >= 0, "a beat map holds at least one beat, at or after 0 s"
        assert all(a < b for a, b in zip(times, times[1:])), "beat times must increase"
        downbeats = self.downbeats
        assert all(a < b for a, b in zip(downbeats, downbeats[1:])), "downbeats must increase"
        assert all(0 <= index < len(times) for index in downbeats), "every downbeat must be one of the beats"
        indices = [beat.index for beat in self.manual]
        assert len(set(indices)) == len(indices), "a beat is listed twice in manual"
        for beat in self.manual:
            assert beat.index < len(times) and times[beat.index] == beat.time, (
                f"manual beat {beat.index} says {beat.time} s and times says otherwise"
            )
        return self


class Licence(_Record):
    """Under what terms a file may be used, as checked by a person (09-agent-interface.md,
    "music is cleared": checked on the source's page, not a search filter)."""

    name: str = Field(description="the licence, as the source states it (CC0, a stock licence and its number)")
    source: str = Field(description="where it was checked: the page that states it")
    checked: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="the date it was checked, YYYY-MM-DD")
    note: str | None = None


class ImageRecord(_Record):
    """A photo (developed: JPEG, PNG or TIFF), shown as a graphics layer's page shows an
    image, so its colour is the browser's: an embedded ICC profile is converted to the
    sRGB the pages render in, and an untagged file is taken as sRGB, as browsers take it
    (*proposed*, 05-motion-graphics.md, Photos)."""

    format: Literal["jpeg", "png", "tiff"]
    size: tuple[int, int] = Field(description="width and height as shown, after the file's EXIF orientation")
    orientation: int = Field(ge=1, le=8, description="the EXIF orientation, 1 when the file has none")
    icc: str | None = Field(description="the embedded ICC profile's description; none when untagged")
    captured: str | None = Field(description="EXIF DateTimeOriginal as the file gives it, camera clock, no zone")


class Derivation(_Record):
    """How a derived clip was made from its original (`derived_from`), so that it can be
    made again: by Gyroflow (`lungecuta stabilize`), frame for frame, its colour and time
    the original's decisions."""

    by: Literal["gyroflow"]
    version: str
    mode: Literal["stabilize", "rolling-shutter"]
    preset: dict[str, Any] = Field(description="the preset Gyroflow was given, its stabilization settings")
    output: dict[str, Any] = Field(description="Gyroflow's output parameters, the codec and its options")
    original_hash: Sha256 = Field(description="the original's hash when the clip was made")


class SpokenToken(_Record):
    """One token of a spoken line as misaki splits the text (a word, a number, a mark of
    punctuation), and where the model speaks it: from its first phoneme's first sample to
    its last phoneme's end, by the model's own durations (07-audio.md, Voice-over)."""

    text: str = Field(description="as written, the pronunciation markup taken off")
    phonemes: str
    space: bool = Field(description="whether the text has a space after it")
    start: int = Field(ge=0, description="the sample of the voice's file where it begins")
    end: int = Field(ge=0, description="the sample where it ends; equal to start for a token with no phonemes")

    @property
    def word(self) -> bool:
        """A word or a number, not a mark: its text holds a letter or a digit."""
        return any(c.isalnum() for c in self.text)


class Speech(_Record):
    """How `lungecuta voice` made a voice-over line (07-audio.md, Voice-over), so that it can
    be made again and its words placed: the model and its files by hash, the text as given
    (with misaki's markup), the phonemes spoken, and every token's place in the file."""

    by: Literal["kokoro"]
    model: str = Field(description="the model's name and version")
    model_hash: Sha256
    voices_hash: Sha256
    onnxruntime: str = Field(description="the version that ran it, on the CPU")
    misaki: str = Field(description="the version that turned the text into phonemes")
    voice: str
    speed: float = Field(gt=0, description="the model's speed: durations divided by it")
    seed: int = Field(description="onnxruntime's seed, for the vocoder's random phase and noise")
    threads: int = Field(gt=0, description="onnxruntime's threads, which change the samples (bench/RESULTS.md)")
    text: str
    caption: str | None = Field(description="what captions show instead of the text, when the line gave one")
    phonemes: str
    rate: int = Field(gt=0, description="the model's sample rate, the file's")
    samples: int = Field(gt=0)
    tokens: list[SpokenToken]

    @model_validator(mode="after")
    def _in_order(self) -> Speech:
        assert any(token.word and token.end > token.start for token in self.tokens), "a spoken line says a word"
        for a, b in zip(self.tokens, self.tokens[1:]):
            assert a.start <= a.end <= b.start, f"tokens {a.text!r} and {b.text!r} are out of order"
        assert self.tokens[-1].end <= self.samples, "the last token ends after the file"
        return self

    @property
    def spoken(self) -> tuple[int, int]:
        """From the first word's start to the last word's end, in samples: the line's
        speech without the model's silence before it and the pause of its last mark."""
        said = [token for token in self.tokens if token.word and token.end > token.start]
        return said[0].start, said[-1].end


class MediaRecord(_Record):
    """`media/KEY.json`: one imported file, its decisions and the evidence for them. A file
    with a video stream has `time`, `color` and `decode` (and a proxy); a music file has
    `wav` and `beats` instead; a voice-over line made by `lungecuta voice` is a sound with
    `speech`."""

    key: MediaKey
    path: MediaPath
    hash: Sha256
    stat: FileStat
    probe: Probe
    time: TimeDecided | TimeUndecided | None = Field(discriminator="status")
    color: ColorDecided | ColorUndecided | None = Field(discriminator="status")
    decode: DecodeRecord | None
    proxy: ProxyRecord | None
    wav: WavRecord | None
    beats: BeatMap | None
    derived_from: MediaKey | None
    licence: Licence | None = None
    image: ImageRecord | None = None
    derivation: Derivation | None = None
    speech: Speech | None = None

    @model_validator(mode="after")
    def _video_or_music(self) -> MediaRecord:
        video = (self.time, self.color, self.decode)
        if self.probe.video is not None:
            assert all(part is not None for part in video), f"{self.key}: footage needs time, color and decode"
            assert self.wav is None and self.beats is None, f"{self.key}: footage has no beat map in Phase 0"
        else:
            assert all(part is None for part in video) and self.proxy is None, (
                f"{self.key}: a file without video has no time, color, decode or proxy"
            )
            # music and sounds have their WAV (music its beats too); a photo has neither
            assert (self.wav is None) == (self.image is not None), f"{self.key}: a file without video needs its wav"
            assert self.image is None or (self.beats is None and not self.probe.audio), f"{self.key}: a photo has no sound"
        return self
