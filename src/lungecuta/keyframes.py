"""Animated properties (docs/design/03-project-format.md#properties-keyframes-easing-expressions):
a constant, or keys in layer time, between which the value follows a temporal bezier in
After Effects' terms of speed and influence.

A key's `ease` shapes the segment that arrives at it, with the names' CSS sense:
`out` slows into the key, `in` starts slowly from the key before, `in_out` does both, and
`hold` keeps the key before's value until this one. An eased end has speed 0 and an
influence of a third of the segment, After Effects' Easy Ease; an end that is not eased
keeps the segment's mean speed, so a segment with neither is a straight line. *(proposed:
the format doc's own example eases the last key `out`, which only the CSS reading makes
mean anything; After Effects names the sides of a key instead.)*
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

INFLUENCE = 1 / 3  # After Effects' Easy Ease: influence 33.33 %, speed 0


def _bezier(p1: float, p2: float, s: float) -> float:
    """A cubic bezier from 0 to 1 with inner points p1 and p2, at parameter s."""
    return 3 * (1 - s) ** 2 * s * p1 + 3 * (1 - s) * s ** 2 * p2 + s ** 3


def _handles(slow_start: bool, slow_end: bool) -> tuple[float, float]:
    """The value handles of a segment normalized to 0..1: flat at an eased end (speed 0),
    along the mean slope (speed 1) at one that is not."""
    return (0.0 if slow_start else INFLUENCE), (1.0 if slow_end else 1 - INFLUENCE)


def progress(u: float, *, slow_start: bool, slow_end: bool) -> float:
    """The share of a segment's change done at the share `u` of its time.

    The time handles sit a third of the way in from each end (the influence), so the time
    curve 3(1-s)²s/3 + 3(1-s)s²·2/3 + s³ is s itself and the bezier's parameter is `u`."""
    assert 0 <= u <= 1, u
    if not slow_start and not slow_end:
        return u
    v1, v2 = _handles(slow_start, slow_end)
    return _bezier(v1, v2, u)


def progress_integral(u: float, *, slow_start: bool, slow_end: bool) -> float:
    """The integral of `progress` from 0 to `u`, exactly: the value curve is a cubic in u."""
    assert 0 <= u <= 1, u
    if not slow_start and not slow_end:
        return u * u / 2
    v1, v2 = _handles(slow_start, slow_end)
    return (3 * v1 * (u ** 2 / 2 - 2 * u ** 3 / 3 + u ** 4 / 4)
            + 3 * v2 * (u ** 3 / 3 - u ** 4 / 4) + u ** 4 / 4)


@dataclass(frozen=True)
class Track:
    """A property resolved to comp time: key times in ticks, values, eases."""

    times: tuple[int, ...]
    values: tuple[tuple[float, ...], ...]
    eases: tuple[str | None, ...]

    def __post_init__(self) -> None:
        assert self.times, "a track needs a key"
        assert all(a < b for a, b in zip(self.times, self.times[1:])), "keys must be in time order, one per time"
        assert len({len(v) for v in self.values}) == 1, "every key needs a value of the same size"
        for ease in self.eases:
            assert ease in (None, "in", "out", "in_out", "hold"), f"unknown ease {ease!r}"

    def at(self, ticks: int) -> tuple[float, ...]:
        if ticks <= self.times[0]:
            return self.values[0]
        if ticks >= self.times[-1]:
            return self.values[-1]
        k = next(k for k in range(1, len(self.times)) if ticks < self.times[k])
        t0, t1 = self.times[k - 1], self.times[k]
        a, b = self.values[k - 1], self.values[k]
        ease = self.eases[k]
        if ease == "hold":
            return a
        share = progress((ticks - t0) / (t1 - t0), slow_start=ease in ("in", "in_out"),
                         slow_end=ease in ("out", "in_out"))
        return tuple(x + share * (y - x) for x, y in zip(a, b))

    def integral(self, a: int, b: int, *, outside: float) -> float:
        """The integral over comp ticks from `a` to `b` (a <= b) of a one-value track that
        is `outside` before its first key and after its last, exactly: a speed track
        integrated to source time (03-project-format.md, Time remap)."""
        assert a <= b, (a, b)
        assert all(len(v) == 1 for v in self.values), "only a one-value track integrates"
        first, last = self.times[0], self.times[-1]
        total = outside * (max(0, min(b, first) - a) + max(0, b - max(a, last)))
        for k in range(1, len(self.times)):
            t0, t1 = self.times[k - 1], self.times[k]
            x, y = max(a, t0), min(b, t1)
            if x >= y:
                continue
            va, vb = self.values[k - 1][0], self.values[k][0]
            ease = self.eases[k]
            if ease == "hold":
                total += va * (y - x)
                continue
            length = t1 - t0
            ends = {"slow_start": ease in ("in", "in_out"), "slow_end": ease in ("out", "in_out")}
            total += va * (y - x) + (vb - va) * length * (
                progress_integral((y - t0) / length, **ends) - progress_integral((x - t0) / length, **ends))
        return total

    def many(self, ticks: np.ndarray) -> np.ndarray:
        """A one-value track at many comp ticks at once (an audio gain on every sample), as
        `at` evaluates each: the first and last values held outside the keys."""
        assert all(len(v) == 1 for v in self.values), "only a one-value track evaluates many at once"
        values = [v[0] for v in self.values]
        out = np.full(ticks.shape, values[0], dtype=np.float64)
        out[ticks >= self.times[-1]] = values[-1]
        for k in range(1, len(self.times)):
            t0, t1 = self.times[k - 1], self.times[k]
            inside = (ticks >= t0) & (ticks < t1)
            if not inside.any():
                continue
            ease = self.eases[k]
            if ease == "hold":
                out[inside] = values[k - 1]
                continue
            u = (ticks[inside] - t0) / (t1 - t0)
            if ease is None:
                share = u
            else:
                v1, v2 = _handles(ease in ("in", "in_out"), ease in ("out", "in_out"))
                share = _bezier(v1, v2, u)
            out[inside] = values[k - 1] + share * (values[k] - values[k - 1])
        return out
