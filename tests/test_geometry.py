import numpy as np
import pytest

from lungecuta.geometry import _area_weights, area_resize, oriented, scaled_size, shown_size, turns


def test_area_resize_averages_blocks_at_an_integer_factor():
    rng = np.random.default_rng(0)
    image = rng.random((16, 24, 3), dtype=np.float32)
    expected = image.reshape(4, 4, 6, 4, 3).mean(axis=(1, 3))
    np.testing.assert_allclose(area_resize(image, (6, 4)), expected, rtol=0, atol=1e-6)


def test_area_resize_covers_fractional_pixels():
    for size_in, size_out in ((10, 3), (2160, 540), (7, 7), (1912, 540)):
        weights = _area_weights(size_in, size_out)
        np.testing.assert_allclose(weights.sum(axis=1), 1, atol=1e-6)
        np.testing.assert_allclose(weights.sum(axis=0) * size_in / size_out, 1, atol=1e-5)
    flat = np.full((9, 10, 3), 0.25, np.float32)
    np.testing.assert_allclose(area_resize(flat, (3, 4)), 0.25, atol=1e-7)


def test_area_resize_only_shrinks():
    with pytest.raises(AssertionError, match="only shrinks"):
        area_resize(np.zeros((4, 4, 3), np.float32), (8, 8))


def test_a_rotation_tag_is_a_counterclockwise_quarter_turn():
    assert [turns(r) for r in (0, 90, 180, 270, -90, -180, 360)] == [0, 1, 2, 3, 3, 2, 0]
    with pytest.raises(AssertionError, match="not a quarter turn"):
        turns(45)
    image = np.arange(2 * 3 * 3, dtype=np.float32).reshape(2, 3, 3)
    turned = oriented(image, 90)
    assert turned.shape == (3, 2, 3) and turned.flags.c_contiguous
    # Counterclockwise: the top row's last pixel becomes the top-left corner.
    assert np.array_equal(turned[0, 0], image[0, -1])
    assert oriented(image, 0) is image
    assert np.array_equal(oriented(image, -90), oriented(image, 270))


def test_sizes_follow_the_rotation_and_keep_the_shape():
    assert shown_size((3840, 2160), 90) == (2160, 3840)
    assert shown_size((3840, 2160), 180) == (3840, 2160)
    assert scaled_size((3840, 2160), 540) == (960, 540)
    assert scaled_size((2160, 3840), 960) == (540, 960)
    assert scaled_size((96, 54), 27) == (48, 27)
    with pytest.raises(AssertionError, match="not a whole number"):
        scaled_size((3840, 2160), 100)


def test_placing_at_half_size_averages_pixel_pairs_exactly():
    from lungecuta.geometry import place

    frame = np.random.default_rng(2).random((8, 12, 3), dtype=np.float32)
    out, alpha = place(frame, (6, 4), position=(0.5, 0.5), anchor=(0.5, 0.5), scale=(0.5, 0.5))
    np.testing.assert_allclose(out, frame.reshape(4, 2, 6, 2, 3).mean(axis=(1, 3)), atol=1e-6)
    assert np.all(alpha == 1)


def test_a_layer_smaller_than_the_comp_is_transparent_around_it():
    from lungecuta.geometry import place

    frame = np.ones((4, 4, 3), np.float32)
    out, alpha = place(frame, (8, 8), position=(0.5, 0.5), anchor=(0.5, 0.5), scale=(1, 1))
    assert alpha[2:6, 2:6].min() == 1 and alpha[0, 0] == 0 and out[0, 0].max() == 0
    # Half a pixel off the grid, the edge pixels are half covered and premultiplied.
    out, alpha = place(frame, (8, 8), position=(0.5 + 0.5 / 8, 0.5), anchor=(0.5, 0.5), scale=(1, 1))
    assert alpha[4, 2] == 0.5 and out[4, 2, 0] == 0.5


def test_enlarging_interpolates_linearly_between_pixel_centres():
    from lungecuta.geometry import place

    frame = np.zeros((2, 2, 3), np.float32)
    frame[:, 1] = 1
    out, alpha = place(frame, (4, 2), position=(0.5, 0.5), anchor=(0.5, 0.5), scale=(2, 1))
    assert out[0, :, 0].tolist() == [0, 0.25, 0.75, 1]


def test_a_quarter_turn_is_a_clockwise_rot90():
    import numpy as np

    from lungecuta.geometry import place

    rng = np.random.default_rng(3)
    frame = rng.random((8, 8, 3)).astype(np.float32)
    placed, alpha = place(frame, (8, 8), position=(0.5, 0.5), anchor=(0.5, 0.5), scale=(1.0, 1.0), rotation=90)
    np.testing.assert_allclose(placed, np.rot90(frame, k=-1), atol=1e-5)  # clockwise on screen
    np.testing.assert_allclose(alpha, 1.0, atol=1e-5)
    same, _ = place(frame, (8, 8), position=(0.5, 0.5), anchor=(0.5, 0.5), scale=(1.0, 1.0), rotation=360)
    np.testing.assert_array_equal(same, place(frame, (8, 8), position=(0.5, 0.5), anchor=(0.5, 0.5), scale=(1.0, 1.0))[0])


def test_a_turned_layer_leaves_its_corners_uncovered():
    import numpy as np

    from lungecuta.geometry import place

    frame = np.ones((20, 20, 3), np.float32)
    placed, alpha = place(frame, (40, 40), position=(0.5, 0.5), anchor=(0.5, 0.5), scale=(1.0, 1.0), rotation=45)
    assert alpha[20, 20, 0] == 1.0 and alpha[0, 0, 0] == 0.0
    # the diamond's area is the square's: 400 pixels of coverage
    assert alpha.sum() == pytest.approx(400, rel=0.01)
    np.testing.assert_allclose(placed, alpha * frame[:1, :1], atol=1e-6)  # premultiplied by its coverage


def masks(*items):
    from lungecuta.schema.comp import Mask

    return [Mask.model_validate(item) for item in items]


def test_a_rectangle_mask_cuts_the_layer_with_an_antialiased_edge():
    import numpy as np

    from lungecuta.geometry import mask_alpha

    alpha = mask_alpha(masks({"shape": "rectangle", "size": [0.5, 0.5]}), (40, 20))[..., 0]
    assert alpha[10, 20] == 1 and alpha[0, 0] == 0
    # its edge at x = 10 and 30, y = 5 and 15: whole pixels in, whole pixels out
    assert alpha[5:15, 10:30].min() == 1 and alpha[:, :10].max() == 0 and alpha[:5].max() == 0
    inverted = mask_alpha(masks({"shape": "rectangle", "size": [0.5, 0.5], "invert": True}), (40, 20))[..., 0]
    np.testing.assert_allclose(inverted, 1 - alpha)


def test_an_ellipse_holds_its_area_and_a_feather_ramps_it():
    import math

    import numpy as np

    from lungecuta.geometry import mask_alpha

    alpha = mask_alpha(masks({"shape": "ellipse", "size": [0.5, 0.8]}), (200, 100))[..., 0]
    assert alpha.sum() == pytest.approx(math.pi * 50 * 40, rel=0.01)
    soft = mask_alpha(masks({"shape": "ellipse", "size": [0.5, 0.8], "feather": 0.1}), (200, 100))[..., 0]
    assert soft.sum() == pytest.approx(alpha.sum(), rel=0.02)  # a centred ramp keeps the area
    row = soft[50]
    assert 0 < row[150] < 1 and np.all(np.diff(row[100:170]) <= 1e-6)  # falls across the edge, never back


def test_masks_combine_in_order():
    from lungecuta.geometry import mask_alpha

    left = {"shape": "rectangle", "centre": [0.25, 0.5], "size": [0.5, 1.0]}
    middle = {"shape": "rectangle", "centre": [0.5, 0.5], "size": [0.5, 1.0]}
    union = mask_alpha(masks(left, middle), (40, 10))[..., 0]
    assert union[:, :30].min() == 1 and union[:, 30:].max() == 0
    cut = mask_alpha(masks(left, middle | {"mode": "subtract"}), (40, 10))[..., 0]
    assert cut[:, :10].min() == 1 and cut[:, 10:].max() == 0
    both = mask_alpha(masks(left, middle | {"mode": "intersect"}), (40, 10))[..., 0]
    assert both[:, 10:20].min() == 1 and both[:, :10].max() == 0 and both[:, 20:].max() == 0


def test_a_mask_travels_with_the_layer_through_scale_and_turn():
    import numpy as np

    from lungecuta.geometry import mask_alpha, place

    frame = np.ones((20, 40, 3), np.float32)
    mask = mask_alpha(masks({"shape": "rectangle", "centre": [0.25, 0.5], "size": [0.5, 1.0]}), (40, 20))
    placed, alpha = place(frame, (80, 40), position=(0.5, 0.5), anchor=(0.5, 0.5), scale=(2.0, 2.0), mask=mask)
    assert alpha[20, 10, 0] == 1 and alpha[20, 70, 0] == 0  # the left half of the layer only
    np.testing.assert_allclose(placed, np.broadcast_to(alpha, placed.shape), atol=1e-6)
    turned, alpha = place(frame, (40, 40), position=(0.5, 0.5), anchor=(0.5, 0.5), scale=(1.0, 1.0), rotation=90,
                          mask=mask)
    assert alpha[5, 20, 0] == 1 and alpha[35, 20, 0] == 0  # a clockwise quarter turn puts the left half on top
