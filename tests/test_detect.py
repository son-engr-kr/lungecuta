from pathlib import Path

import pytest

from lungecuta.detect import detect
from lungecuta.schema.media import AudioProbe, Probe, StreamTags, VideoProbe
from lungecuta.sony import read_sidecar, sidecar_path

SONY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<NonRealTimeMeta xmlns="urn:schemas-professionalDisc:nonRealTimeMeta:ver.2.20">
  <Duration value="{duration}"/>
  <LtcChangeTable tcFps="30" halfStep="false">
    <LtcChange frameCount="0" value="55210000" status="increment"/>
  </LtcChangeTable>
  <VideoFormat>
    <VideoFrame videoCodec="AVC140_3840_2160_H422P@L51" captureFps="{capture}" formatFps="{written}"/>
  </VideoFormat>
  <Device manufacturer="Sony" modelName="ILCE-7CM2"/>
  <RecordingMode type="normal" cacheRec="false"/>
  <AcquisitionRecord>
    <Group name="CameraUnitMetadataSet">
      <Item name="CaptureGammaEquation" value="{gamma}"/>
      <Item name="CaptureColorPrimaries" value="{primaries}"/>
      <Item name="CodingEquations" value="{coding}"/>
    </Group>
  </AcquisitionRecord>
</NonRealTimeMeta>
"""
SLOG3 = dict(gamma="s-log3-cine", primaries="s-gamut3-cine", coding="rec709")
HLG = dict(gamma="hlg-slog3ootf", primaries="rec2020", coding="rec2020ncl")
UNTAGGED = dict(color_range="pc", color_space="unknown", color_transfer="unknown", color_primaries="unknown")


def clip(tmp_path: Path, *, xml: dict | None, frames: int = 705, rate: str = "30000/1001", tags: dict = UNTAGGED,
         duration: int = 705, capture: str = "29.97p", written: str = "29.97p") -> tuple[Path, Probe]:
    path = tmp_path / "C0001.MP4"
    if xml is not None:
        sidecar_path(path).write_text(SONY_XML.format(duration=duration, capture=capture, written=written, **xml))
    video = VideoProbe(codec="h264", profile="High 4:2:2", pix_fmt="yuv422p10le", size=(3840, 2160), rate=rate,
                       frames=frames, tags=StreamTags(**tags), chroma_location="left", rotation=0)
    probe = Probe(video=video, audio=[AudioProbe(codec="pcm_s24be", channels=2, layout="stereo", rate=48000)],
                  data_streams=1, timecode="00:00:21:15", creation_time=None)
    return path, probe


def test_the_sidecar_is_read_by_the_name_the_camera_writes(tmp_path):
    path, _ = clip(tmp_path, xml=SLOG3)
    assert sidecar_path(path).name == "C0001M01.XML"
    xml = read_sidecar(path)
    assert (xml.capture_gamma, xml.capture_primaries, xml.coding_equations) == ("s-log3-cine", "s-gamut3-cine", "rec709")
    assert (xml.capture_fps, xml.format_fps, xml.recording_mode, xml.ltc_start) == ("29.97p", "29.97p", "normal", "55210000")
    assert xml.duration == 705 and xml.model == "ILCE-7CM2"
    assert read_sidecar(tmp_path / "C0002.MP4") is None


def test_slog3_is_decided_from_the_sidecar(tmp_path):
    detection = detect(*clip(tmp_path, xml=SLOG3))
    assert detection.decided
    color = detection.color
    assert (color.space, color.matrix, color.range) == ("S-Log3 S-Gamut3.Cine", "bt709", "full")
    assert color.evidence == {
        "source": "sony-xml", "file": "C0001M01.XML", "CaptureGammaEquation": "s-log3-cine",
        "CaptureColorPrimaries": "s-gamut3-cine", "CodingEquations": "rec709", "stream_tags": UNTAGGED,
    }
    time = detection.time
    assert (time.rate, time.capture_rate, time.start_tc) == ("30000/1001", "30000/1001", "00:00:21:15")


def test_hlg_is_undecided_with_its_evidence(tmp_path):
    tags = dict(color_range="tv", color_space="bt2020nc", color_transfer="arib-std-b67", color_primaries="bt2020")
    detection = detect(*clip(tmp_path, xml=HLG, tags=tags))
    assert not detection.decided
    assert detection.color.status == "undecided"
    assert detection.color.reason == "no mapping for sony-xml hlg-slog3ootf + rec2020"
    assert detection.color.evidence["CodingEquations"] == "rec2020ncl"
    assert detection.color.evidence["stream_tags"] == tags
    assert detection.time.status == "decided"


def test_a_near_miss_is_not_matched_to_a_nearby_row(tmp_path):
    # S-Log3 with S-Gamut3 has no file on disk yet, so it has no row.
    detection = detect(*clip(tmp_path, xml=dict(SLOG3, gamma="s-log3", primaries="s-gamut3")))
    assert detection.color.reason == "no mapping for sony-xml s-log3 + s-gamut3"


def test_a_clip_without_sidecar_is_never_assumed_rec709(tmp_path):
    tags = dict(color_range="tv", color_space="bt709", color_transfer="bt709", color_primaries="bt709")
    detection = detect(*clip(tmp_path, xml=None, tags=tags))
    assert detection.color.status == "undecided"
    assert detection.color.reason == "no camera sidecar, and no mapping for stream tags bt709/bt709/bt709"
    assert detection.time.status == "decided" and detection.time.capture_rate is None


def test_a_stream_that_does_not_state_its_range_is_undecided(tmp_path):
    detection = detect(*clip(tmp_path, xml=SLOG3, tags=dict(UNTAGGED, color_range="unknown")))
    assert "does not state its range" in detection.color.reason


def test_a_sidecar_of_another_length_describes_another_clip(tmp_path):
    detection = detect(*clip(tmp_path, xml=SLOG3, duration=704))
    assert detection.color.status == detection.time.status == "undecided"
    assert detection.color.reason == "sidecar C0001M01.XML says 704 frames and the clip has 705"


def test_slow_and_quick_is_undecided_until_a_real_file_confirms_it(tmp_path):
    detection = detect(*clip(tmp_path, xml=SLOG3, capture="59.94p"))
    assert detection.color.status == "decided"
    assert detection.time.status == "undecided"
    assert "slow-and-quick" in detection.time.reason


def test_a_sidecar_rate_that_disagrees_with_the_stream_is_undecided(tmp_path):
    detection = detect(*clip(tmp_path, xml=SLOG3, capture="59.94p", written="59.94p"))
    assert detection.time.reason == "sidecar formatFps 59.94p does not match the stream's rate 30000/1001"


def test_an_unknown_sony_rate_has_no_row(tmp_path):
    detection = detect(*clip(tmp_path, xml=SLOG3, capture="23.98p", written="23.98p"))
    assert detection.time.reason == "no mapping for sony-xml rates 23.98p / 23.98p"


def test_an_unsupported_rate_is_an_error_at_import(tmp_path):
    with pytest.raises(AssertionError, match="not supported"):
        detect(*clip(tmp_path, xml=SLOG3, rate="15"))
