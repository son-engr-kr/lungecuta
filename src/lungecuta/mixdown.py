"""The comp's sound: music layers summed at the mix rate through their gains and fades,
mastered at a preset's targets (`audio.py`), and encoded as the preset delivers it with
the ceiling held in the delivered file (docs/design/07-audio.md)."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from .audio import master, true_peak_db
from .effects import apply as apply_effects, pan_gains, source_length
from .comp import MIX_RATE, Nest, Resolved, Sound
from .encode import Preset
from .media import Project, footage_wav, music_wav
from .music import read_wav, wav_bytes
from .ticks import nearest_sample, ticks_per_frame, ticks_per_sample
from .tools import run, tool


def comp_samples(resolved: Resolved) -> int:
    """The comp's length at the mix rate, its end landing on the nearest sample."""
    return nearest_sample(resolved.end * ticks_per_frame(resolved.rate), MIX_RATE)


def mix(project: Project, resolved: Resolved) -> np.ndarray:
    """The audio layers summed, each through its effects, pan, gain and fades, and every precomp's own mix
    where its layer plays (its layer time is the precomp's time, as in After Effects),
    frames x 2 channels at 48 kHz, before the master chain (float, so a sum over full
    scale is kept for the limiter)."""
    total = comp_samples(resolved)
    out = np.zeros((total, 2), np.float32)
    for sound in resolved.sounds:
        piece = layer_sound(project, sound, total)
        out[sound.first:sound.first + len(piece)] += piece
    for nest in resolved.nests:
        inner = mix(project, nest.resolved)
        first, end = nest_samples(nest)
        offset = nearest_signed_sample(nest.start)
        assert 0 <= first - offset and end - offset <= inner.shape[0], (
            f"layer {nest.layer.id} plays {nest.layer.comp}'s sound outside it"
        )
        out[first:min(end, total)] += inner[first - offset:min(end, total) - offset]
    return out


def layer_sound(project: Project, sound: Sound, total: int) -> np.ndarray:
    """What one audio layer adds to the mix from its first comp sample, up to comp sample
    `total`: its source through its effects, pan, gain and fades, frames x 2 channels."""
    signal, rate = read_wav(footage_wav(project, sound.record) if sound.footage else music_wav(project, sound.record))
    assert rate == MIX_RATE and signal.shape[1] in (1, 2), f"{sound.record.key}: a {rate} Hz WAV"
    count = min(sound.end, total) - sound.first
    effects = sound.layer.effects
    piece = signal[sound.source_first:sound.source_first + source_length(effects, count)]
    assert len(piece) == source_length(effects, count), (
        f"layer {sound.layer.id} needs {source_length(effects, count) / MIX_RATE:.3f} s of {sound.record.key}'s sound "
        f"from {sound.source_first / MIX_RATE:.3f} s, which has {len(signal) / MIX_RATE:.3f} s")
    piece = piece if piece.shape[1] == 2 else np.repeat(piece, 2, axis=1)
    if effects:
        piece = apply_effects(effects, piece, MIX_RATE, count)
    piece = piece[:count]
    if sound.layer.pan:
        piece = piece * pan_gains(sound.layer.pan)
    return piece * sound.envelope()[:count, None].astype(np.float32)


def nearest_signed_sample(ticks: int) -> int:
    """The sample nearest a time that may be before the comp's start, halves rounding up."""
    per = ticks_per_sample(MIX_RATE)
    return (2 * ticks + per) // (2 * per)


def nest_samples(nest: Nest) -> tuple[int, int]:
    """The comp samples a precomp layer plays its sound on: from its visible in point to its
    visible out point, each on the nearest sample, as an audio layer's are."""
    return nearest_sample(nest.visible[0], MIX_RATE), nearest_sample(nest.visible[1], MIX_RATE)


def mastered(project: Project, resolved: Resolved, preset: Preset) -> tuple[np.ndarray, dict[str, Any]]:
    return master(mix(project, resolved), MIX_RATE, target_lufs=preset.loudness, ceiling_dbtp=preset.true_peak)


DELIVERY_PASSES = 12  # a bound on the search, which lowers the ceiling by more than PEAK_STEP each pass
PEAK_STEP = 0.01  # dB: the precision the delivered true peak is reported at


def under_ceiling(peak_at: Callable[[float], float], limit: float,
                  passes: int = DELIVERY_PASSES) -> tuple[float, float]:
    """The first ceiling, from `limit` down, at which `peak_at(ceiling)` (the true peak of
    the master under that ceiling, encoded and decoded) is at or under `limit`, and that
    peak. The encoded peak follows the ceiling only on the whole: AAC's quantization held it
    still while the ceiling fell 0.06 dB and moved it up 0.02 dB as the ceiling fell further,
    its excess over the ceiling ranging from -0.05 to 0.17 dB (`bench/aac_peak_ladder.py`,
    bench/RESULTS.md), so no slope between two passes can be trusted. Each pass that fails lowers the ceiling by the peak's excess over one
    reporting step under `limit`, more than PEAK_STEP, and the ceiling never rises."""
    ceiling, aim = limit, limit - PEAK_STEP
    for _ in range(passes):
        peak = peak_at(ceiling)
        if peak <= limit:
            return ceiling, peak
        ceiling -= peak - aim
    raise AssertionError(f"the encoded audio stayed over {limit} dBTP after {passes} passes "
                         f"(last {peak:.3f} under a ceiling of {ceiling + peak - aim:.3f})")


def delivered_audio(project: Project, resolved: Resolved, preset: Preset, base: Path) -> tuple[Path, list[str], dict]:
    """The comp's sound encoded as the preset delivers it: the file to mux, the arguments
    that mux it, and what the master chain did.

    A lossy codec raises the true peak (AAC at 256 kbps by 0.2 to 0.5 dB on the music
    sample, `bench/master_chain.py`), and the ceiling holds for the delivered file. So
    lossy audio is encoded on its own, decoded and measured, and mastered again under a
    lower ceiling until the decoded file is under the preset's ceiling (`under_ceiling`);
    then it is muxed as it is. 24-bit PCM is muxed from the master directly."""
    mixed = mix(project, resolved)
    wav = base.with_name(f".{base.stem}.mix.wav")
    encoded = base.with_name(f".{base.stem}.audio.m4a")
    signal, chain = master(mixed, MIX_RATE, target_lufs=preset.loudness, ceiling_dbtp=preset.true_peak)
    if preset.audio[1].startswith("pcm_") or chain.get("silent"):
        wav.write_bytes(wav_bytes(signal, MIX_RATE))
        return wav, list(preset.audio), chain
    chains = {preset.true_peak: chain}

    def peak_at(ceiling: float) -> float:
        mastered = signal if ceiling == preset.true_peak else None
        if mastered is None:
            mastered, chains[ceiling] = master(mixed, MIX_RATE, target_lufs=preset.loudness, ceiling_dbtp=ceiling)
        wav.write_bytes(wav_bytes(mastered, MIX_RATE))
        run([tool("ffmpeg"), "-v", "error", "-y", "-i", str(wav), *preset.audio, str(encoded)])
        decoded = np.frombuffer(run([tool("ffmpeg"), "-v", "error", "-i", str(encoded), "-f", "f32le", "-"]).stdout,
                                np.float32).reshape(-1, 2)
        return true_peak_db(decoded, MIX_RATE)

    ceiling, peak = under_ceiling(peak_at, preset.true_peak)
    wav.unlink()
    return encoded, ["-c:a", "copy"], chains[ceiling] | {"ceiling_used": round(ceiling, 3),
                                                        "delivered_true_peak": round(peak, 3)}
