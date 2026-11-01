"""Voice-over lines: `lungecuta voice` (src/lungecuta/voice.py), the `speech` anchor and
captions from what a line says, on the comp in tests/clips.py (128x72 at 29.97 fps).
The model is replaced by a stand-in whose durations are known, except in the test that
runs it."""
import hashlib
import io
import json

import numpy as np
import pytest
from click.testing import CliRunner

from lungecuta import ops, voice
from lungecuta.cli import main
from lungecuta.comp import load_comp, resolve
from lungecuta.comp.resolve import spoken_tokens
from lungecuta.media import Project, load_record
from lungecuta.music import read_wav

LOOK = ".line { font: 700 24px sans-serif; color: #fff; }"
LEAD = 10  # the stand-in's frames of silence before the first phoneme (the pad's duration)


def stand_in(phonemes, voice_name, speed, seed, threads):
    """Durations the test can count: the pad LEAD frames, each phoneme 2, the end pad 4;
    a waveform of that many samples, made from the seed."""
    frames = np.array([LEAD] + [2] * len(phonemes) + [4], np.int64)
    rng = np.random.default_rng(seed)
    return (0.1 * rng.standard_normal(int(frames.sum()) * voice.SAMPLES_PER_FRAME)).astype(np.float32), frames


@pytest.fixture
def said(cut_project, monkeypatch):
    monkeypatch.setattr(voice, "synthesize", stand_in)
    return cut_project


def run(*args):
    result = CliRunner().invoke(main, list(args), catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_a_line_is_imported_as_a_sound_with_its_words(said):
    printed = run("voice", "--project", str(said), "--voice", "af_heart", "--lines",
                  json.dumps([{"key": "vo1", "text": "This is [Kokoro](/kˈOkəɹO/). It costs $5."}]))
    line = printed["said"][0]
    assert [w["text"] for w in line["words"]] == ["This", "is", "Kokoro", "It", "costs", "5"]
    record = load_record(Project.load(said), "vo1")
    speech = record.speech
    assert record.wav is not None and record.beats is None and record.wav.resampled_from == voice.RATE
    assert (speech.voice, speech.speed, speech.seed, speech.by) == ("af_heart", 1.0, 0, "kokoro")
    assert speech.phonemes == "ðˌɪs ɪz kˈOkəɹO. ˌɪt kˈɔsts fˈIv dˈɑləɹz."
    # each phoneme lasts 2 frames after the pad's LEAD; a space between tokens is a phoneme too
    first, second = speech.tokens[:2]
    assert (first.text, first.start, first.end) == ("This", LEAD * 600, (LEAD + 2 * 4) * 600)
    assert second.start == first.end + 2 * 600, "the space after This"
    dollar, five = speech.tokens[-3], speech.tokens[-2]
    assert (dollar.text, dollar.start == dollar.end == five.start) == ("$", True), "a token with no phonemes"
    assert speech.spoken == (LEAD * 600, five.end), "from the first word to the last, not the final mark"
    assert speech.samples == (LEAD + 2 * len(speech.phonemes) + 4) * 600
    assert (said / record.path.rel).is_file() and record.path.root == "project"
    signal, rate = read_wav(said / record.path.rel)
    assert rate == voice.RATE and signal.shape == (speech.samples, 1), "the model's output, nothing trimmed"


def test_a_word_misaki_cannot_read_is_refused_before_anything_is_said(said):
    with pytest.raises(AssertionError, match="misaki cannot read 'Qzxvbt'"):
        voice.speak(Project.load(said), [voice.Line(key="ok", text="Fine."), voice.Line(key="no", text="Qzxvbt.")],
                    voice="af_heart")
    assert not (said / "media" / "ok.json").exists()


def test_only_english_voices_and_lines_with_a_voice(said):
    project = Project.load(said)
    with pytest.raises(AssertionError, match="not an English voice"):
        voice.speak(project, [voice.Line(key="a", text="Hola.")], voice="ef_dora")
    with pytest.raises(AssertionError, match="say which voice"):
        voice.speak(project, [voice.Line(key="a", text="Hello.")])


def test_a_line_s_own_voice_and_speed_win(said):
    voice.speak(Project.load(said), [voice.Line(key="a", text="Hello.", voice="bf_emma", speed=0.9),
                                     voice.Line(key="b", text="Hello.")], voice="am_michael", speed=1.1)
    a, b = (load_record(Project.load(said), k).speech for k in ("a", "b"))
    assert (a.voice, a.speed, b.voice, b.speed) == ("bf_emma", 0.9, "am_michael", 1.1)
    assert a.phonemes != b.phonemes, "a British voice reads with misaki's British lexicon"


def test_speech_puts_the_first_word_on_the_layer_s_start(said):
    voice.speak(Project.load(said), [voice.Line(key="vo1", text="Here we go.")], voice="af_heart")
    project = Project.load(said)
    ops.place(project, "main", "vo1", "b:2", anchor="speech")
    edit = ops.Edit.open(project, "main")
    layer = next(layer for layer in edit.comp.layers if layer.id == "vo1")
    assert layer.anchor == "speech"
    b2 = round((0.25 + 2 * 0.5) * 705600000)  # b:2 in ticks
    tokens = spoken_tokens(project, layer, edit.rate, edit.grid)
    assert tokens[0][1] == b2, "the first word begins on b:2"
    sound = next(s for s in resolve(project, edit.comp).sounds if s.layer.id == "vo1")
    assert sound.first == round(1.25 * 48000) - LEAD * 600 * 2, "the silence before it plays before b:2"


def test_captions_say_each_word_where_the_comp_plays_it(said):
    voice.speak(Project.load(said), [voice.Line(key="vo1", text="One two. Three!"),
                                     voice.Line(key="vo2", text="Four [Kokoro](/kˈOkəɹO/).",
                                                caption="Four, Kokoro.")], voice="af_heart")
    project = Project.load(said)
    ops.place(project, "main", "vo1", "1s", anchor="speech")
    ops.place(project, "main", "vo2", "3s", source="0.1s")
    ops.captions(Project.load(said), "main", None, LOOK, voices=["vo1", "vo2"], by="sentence")
    lines = json.loads((said / "comps" / "main.json").read_text())["layers"][-1]["params"]["lines"]
    frame = 600 / 24000 * 2  # a phoneme of the stand-in
    assert [[w["text"] for w in line.get("words", [])] or line["text"] for line in lines] == [
        ["One", "two."], ["Three!"], "Four, Kokoro."]
    one, three, four = lines
    tokens = load_record(project, "vo1").speech.tokens  # One, two, ".", Three, "!"
    assert one["from"] == 0, "the captions' layer starts on the first word"
    assert one["words"][1]["at"] == pytest.approx((len(tokens[0].phonemes) + 1) * frame, abs=1e-5), "One and a space"
    after_two = len(tokens[1].phonemes) + len(tokens[2].phonemes) + 1
    assert three["from"] == pytest.approx(one["words"][1]["at"] + after_two * frame, abs=1e-5)
    assert one["to"] == pytest.approx(one["words"][1]["at"] + len(tokens[1].phonemes) * frame, abs=1e-5), (
        "a line ends with its last word, not the pause of its full stop")
    # vo2 starts at 3 s from 0.1 s into its file: its first word is LEAD frames in
    assert four["from"] == pytest.approx(3 - 1 + (LEAD * 600 / 24000 - 0.1), abs=1e-5)


def test_captions_refuse_a_line_played_faster(said):
    voice.speak(Project.load(said), [voice.Line(key="vo1", text="Go.")], voice="af_heart")
    project = Project.load(said)
    ops.place(project, "main", "vo1", "1s")
    comp = json.loads((said / "comps" / "main.json").read_text())
    comp["layers"][-1]["effects"] = [{"type": "repitch", "percent": 110}]
    (said / "comps" / "main.json").write_text(json.dumps(comp))
    with pytest.raises(AssertionError, match="repitchd|repitch"):
        ops.captions(Project.load(said), "main", None, LOOK, voices=["vo1"])


def test_a_lost_file_is_made_again_identical(said):
    voice.speak(Project.load(said), [voice.Line(key="vo1", text="Again.")], voice="af_heart", seed=3)
    project = Project.load(said)
    record = load_record(project, "vo1")
    path = said / record.path.rel
    before = path.read_bytes()
    path.unlink()
    assert run("voice", "--project", str(said), "--again", "vo1") == {"made": ["vo1"]}
    assert path.read_bytes() == before
    assert run("voice", "--project", str(said), "--again", "vo1") == {"made": []}, "nothing lost, nothing made"


def test_lint_asks_no_licence_of_a_voice_over_line(said):
    from lungecuta.lint import lint

    voice.speak(Project.load(said), [voice.Line(key="vo1", text="Mine.")], voice="af_heart")
    ops.place(Project.load(said), "main", "vo1", "1s")
    warned = [w["message"] for w in lint(Project.load(said), "main")["warnings"] if w["rule"] == "music_licence"]
    assert warned == ["track has no licence record (lungecuta licence track --name ... --source ...)"]


def test_fetching_checks_each_file_and_discards_another(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "CACHE", tmp_path)
    good = {f.name: f"{f.name} bytes".encode() for f in voice.FILES}
    files = tuple(voice.ModelFile(name, len(data), hashlib.sha256(data).hexdigest()) for name, data in good.items())
    monkeypatch.setattr(voice, "FILES", files)
    served = []

    def opener(request, timeout):
        name = request.full_url.rsplit("/", 1)[1]
        served.append(name)
        return io.BytesIO(good[name])

    assert voice.fetch(opener=opener) and voice.ready() and served == [f.name for f in files]
    assert not voice.fetch(opener=opener), "in place: nothing fetched"
    (tmp_path / "kokoro" / "v1.0" / files[0].name).unlink()
    good[files[0].name] = b"something else, as long"[: files[0].size].ljust(files[0].size, b".")
    with pytest.raises(AssertionError, match="not the pinned file"):
        voice.fetch(opener=opener)
    assert not (tmp_path / "kokoro" / "v1.0" / files[0].name).exists()
    assert not (tmp_path / "kokoro" / "v1.0" / f"{files[0].name}.part").exists()


def test_the_model_says_a_line_the_same_way_twice(cut_project):
    """Kokoro itself, fetched on first use: same line, seed and threads, same samples; the
    waveform is 600 samples a frame; another seed keeps the timing."""
    phonemes, _ = voice.phonemize("Every photo gets a suggestion.", "af_heart")
    a, frames = voice.synthesize(phonemes, "af_heart", 1.0, 0, 4)
    b, again = voice.synthesize(phonemes, "af_heart", 1.0, 0, 4)
    c, other = voice.synthesize(phonemes, "af_heart", 1.0, 1, 4)
    assert a.tobytes() == b.tobytes() and (frames == again).all()
    assert (frames == other).all() and a.tobytes() != c.tobytes()
    assert a.size == int(frames.sum()) * 600 and 0.01 < np.abs(a).max() < 1
