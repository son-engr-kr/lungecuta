import pytest

from lungecuta.keyframes import Track, progress


def test_linear_progress_is_time():
    assert [progress(u, slow_start=False, slow_end=False) for u in (0, 0.3, 1)] == [0, 0.3, 1]


@pytest.mark.parametrize("start,end", [(True, False), (False, True), (True, True)])
def test_eased_progress_runs_from_0_to_1_and_never_back(start, end):
    values = [progress(k / 100, slow_start=start, slow_end=end) for k in range(101)]
    assert values[0] == pytest.approx(0, abs=1e-12) and values[-1] == pytest.approx(1, abs=1e-12)
    assert all(a <= b + 1e-12 for a, b in zip(values, values[1:]))


def test_an_eased_end_is_flat_and_both_ends_are_symmetric():
    # Slow at the end: the last 5 % of time changes little; slow at the start, the first.
    assert 1 - progress(0.95, slow_start=False, slow_end=True) < 0.01
    assert progress(0.05, slow_start=True, slow_end=False) < 0.01
    for u in (0.1, 0.3, 0.45):
        assert progress(u, slow_start=True, slow_end=True) == pytest.approx(
            1 - progress(1 - u, slow_start=True, slow_end=True), abs=1e-9)


def test_a_track_holds_eases_and_clamps():
    track = Track(times=(0, 100, 200), values=((1.0,), (2.0,), (0.0,)), eases=(None, "out", "hold"))
    assert track.at(-50) == (1.0,) and track.at(500) == (0.0,)
    assert track.at(50)[0] == pytest.approx(1 + progress(0.5, slow_start=False, slow_end=True))
    assert track.at(150) == (2.0,), "hold keeps the key before until the key"
    assert track.at(200) == (0.0,)


def test_a_track_wants_keys_in_order_and_values_of_one_size():
    with pytest.raises(AssertionError, match="in time order"):
        Track(times=(0, 0), values=((1.0,), (2.0,)), eases=(None, None))
    with pytest.raises(AssertionError, match="same size"):
        Track(times=(0, 1), values=((1.0,), (2.0, 3.0)), eases=(None, None))


@pytest.mark.parametrize("ease", [None, "in", "out", "in_out", "hold"])
def test_a_track_integrates_exactly(ease):
    import numpy as np

    from lungecuta.keyframes import Track

    track = Track((1000, 5000, 9000), ((1.0,), (3.0,), (0.5,)), (None, ease, "in_out"))
    a, b = 0, 12000
    ticks = np.linspace(a, b, 120001)
    values = np.array([track.at(int(t))[0] if track.times[0] <= t <= track.times[-1] else 1.0 for t in ticks])
    numeric = float(np.sum((values[1:] + values[:-1]) / 2 * np.diff(ticks)))
    assert track.integral(a, b, outside=1.0) == pytest.approx(numeric, rel=2e-4)
    # additive over a split, and over a span inside one segment
    assert track.integral(a, 3000, outside=1.0) + track.integral(3000, b, outside=1.0) == pytest.approx(
        track.integral(a, b, outside=1.0))
    assert track.integral(0, 1000, outside=2.0) == pytest.approx(2000)


@pytest.mark.parametrize("ease", [None, "in", "out", "in_out", "hold"])
def test_many_evaluates_as_at_does(ease):
    import numpy as np

    from lungecuta.keyframes import Track

    track = Track((1000, 5000, 9000), ((0.0,), (-6.0,), (-60.0,)), (None, ease, "in_out"))
    ticks = np.concatenate([np.arange(0, 12000, 7), [1000, 5000, 9000]])  # the key times themselves too
    assert np.allclose(track.many(ticks), [track.at(int(t))[0] for t in ticks], atol=1e-9)
