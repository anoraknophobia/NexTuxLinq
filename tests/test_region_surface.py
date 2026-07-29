"""Tests for isolated schema-v2 RGB888 region drawing surfaces."""

from __future__ import annotations

import pytest

from nextuxlinq.rendering_v2.surface import (
    BLACK,
    RegionSurface,
    RgbColor,
    SurfaceInvariantError,
    SurfaceTypeError,
    SurfaceValidationError,
)


def pixel(surface: RegionSurface, x: int, y: int) -> bytes:
    payload = surface.to_bytes()
    offset = (y * surface.width + x) * 3
    return payload[offset : offset + 3]


def test_surface_dimensions_default_fill_and_exact_byte_length() -> None:
    surface = RegionSurface(7, 5)

    assert surface.width == 7
    assert surface.height == 5
    assert surface.byte_length == 7 * 5 * 3
    assert surface.to_bytes() == bytes(7 * 5 * 3)


def test_surface_custom_initial_color_and_fill() -> None:
    initial = RgbColor(1, 2, 3)
    replacement = RgbColor(10, 20, 30)
    surface = RegionSurface(3, 2, initial)

    assert surface.to_bytes() == initial.to_bytes() * 6

    surface.fill(replacement)
    assert surface.to_bytes() == replacement.to_bytes() * 6


def test_set_pixel_writes_inside_bounds() -> None:
    surface = RegionSurface(3, 2)
    color = RgbColor(9, 8, 7)

    assert surface.set_pixel(0, 0, color) is True
    assert surface.set_pixel(2, 1, color) is True
    assert pixel(surface, 0, 0) == color.to_bytes()
    assert pixel(surface, 2, 1) == color.to_bytes()
    assert pixel(surface, 1, 0) == BLACK.to_bytes()


@pytest.mark.parametrize(
    "x,y",
    [(-1, 0), (3, 0), (0, -1), (0, 2), (-1, -1), (3, 2)],
)
def test_set_pixel_clips_every_outside_boundary(x: int, y: int) -> None:
    surface = RegionSurface(3, 2)

    assert surface.set_pixel(x, y, RgbColor(1, 2, 3)) is False
    assert surface.to_bytes() == bytes(3 * 2 * 3)


@pytest.mark.parametrize(
    "x,y",
    [
        (True, 0),
        (0, False),
        (1.0, 0),
        (0, 1.0),
        ("0", 0),
        (0, "0"),
        (None, 0),
    ],
)
def test_set_pixel_rejects_invalid_coordinate_types(
    x: object, y: object
) -> None:
    with pytest.raises(SurfaceTypeError):
        RegionSurface(2, 2).set_pixel(  # type: ignore[arg-type]
            x, y, RgbColor(1, 2, 3)
        )


def test_fill_rect_fully_inside_surface() -> None:
    surface = RegionSurface(5, 4)
    color = RgbColor(1, 2, 3)

    assert surface.fill_rect(1, 1, 3, 2, color) == 6

    colored = {
        (x, y)
        for y in range(surface.height)
        for x in range(surface.width)
        if pixel(surface, x, y) == color.to_bytes()
    }
    assert colored == {
        (1, 1),
        (2, 1),
        (3, 1),
        (1, 2),
        (2, 2),
        (3, 2),
    }


@pytest.mark.parametrize(
    "rectangle,expected",
    [
        ((-2, 1, 4, 2), {(0, 1), (1, 1), (0, 2), (1, 2)}),
        ((3, 1, 4, 2), {(3, 1), (4, 1), (3, 2), (4, 2)}),
        ((1, -2, 2, 4), {(1, 0), (2, 0), (1, 1), (2, 1)}),
        ((1, 3, 2, 4), {(1, 3), (2, 3)}),
    ],
)
def test_fill_rect_clips_each_surface_boundary(
    rectangle: tuple[int, int, int, int],
    expected: set[tuple[int, int]],
) -> None:
    surface = RegionSurface(5, 4)
    color = RgbColor(10, 11, 12)

    assert surface.fill_rect(*rectangle, color) == len(expected)
    actual = {
        (x, y)
        for y in range(surface.height)
        for x in range(surface.width)
        if pixel(surface, x, y) == color.to_bytes()
    }
    assert actual == expected


@pytest.mark.parametrize(
    "rectangle",
    [
        (-5, 0, 2, 2),
        (5, 0, 2, 2),
        (0, -5, 2, 2),
        (0, 4, 2, 2),
    ],
)
def test_fill_rect_completely_outside_is_a_noop(
    rectangle: tuple[int, int, int, int],
) -> None:
    surface = RegionSurface(5, 4)

    assert surface.fill_rect(
        *rectangle, RgbColor(10, 20, 30)
    ) == 0
    assert surface.to_bytes() == bytes(5 * 4 * 3)


@pytest.mark.parametrize("width,height", [(0, 1), (1, 0), (0, 0)])
def test_fill_rect_zero_dimension_is_a_noop(
    width: int, height: int
) -> None:
    surface = RegionSurface(2, 2, RgbColor(3, 4, 5))
    before = surface.to_bytes()

    assert surface.fill_rect(
        -10, -10, width, height, RgbColor(1, 1, 1)
    ) == 0
    assert surface.to_bytes() == before


@pytest.mark.parametrize("width,height", [(-1, 1), (1, -1), (-1, -1)])
def test_fill_rect_rejects_negative_dimensions(
    width: int, height: int
) -> None:
    with pytest.raises(SurfaceValidationError):
        RegionSurface(2, 2).fill_rect(
            0, 0, width, height, RgbColor(1, 2, 3)
        )


@pytest.mark.parametrize(
    "rectangle",
    [
        (True, 0, 1, 1),
        (0, False, 1, 1),
        (0, 0, True, 1),
        (0, 0, 1, False),
        (0.0, 0, 1, 1),
        (0, "0", 1, 1),
        (0, 0, 1.0, 1),
        (0, 0, 1, "1"),
    ],
)
def test_fill_rect_rejects_invalid_coordinate_types(
    rectangle: tuple[object, object, object, object],
) -> None:
    with pytest.raises(SurfaceTypeError):
        RegionSurface(2, 2).fill_rect(  # type: ignore[arg-type]
            *rectangle, RgbColor(1, 2, 3)
        )


@pytest.mark.parametrize(
    "width,height",
    [
        (0, 1),
        (1, 0),
        (-1, 1),
        (1, -1),
        (91, 1),
        (1, 11),
    ],
)
def test_surface_rejects_dimensions_outside_matrix_bounds(
    width: int, height: int
) -> None:
    with pytest.raises(SurfaceValidationError):
        RegionSurface(width, height)


@pytest.mark.parametrize(
    "width,height",
    [
        (True, 1),
        (1, False),
        (1.0, 1),
        (1, 1.0),
        ("1", 1),
        (1, "1"),
    ],
)
def test_surface_rejects_noninteger_and_boolean_dimensions(
    width: object, height: object
) -> None:
    with pytest.raises(SurfaceTypeError):
        RegionSurface(width, height)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "components",
    [
        (-1, 0, 0),
        (0, -1, 0),
        (0, 0, -1),
        (256, 0, 0),
        (0, 256, 0),
        (0, 0, 256),
    ],
)
def test_rgb_rejects_components_outside_byte_range(
    components: tuple[int, int, int],
) -> None:
    with pytest.raises(SurfaceValidationError):
        RgbColor(*components)


@pytest.mark.parametrize(
    "components",
    [
        (True, 0, 0),
        (0, False, 0),
        (0, 0, True),
        (1.0, 0, 0),
        (0, "1", 0),
        (0, 0, None),
    ],
)
def test_rgb_rejects_noninteger_and_boolean_components(
    components: tuple[object, object, object],
) -> None:
    with pytest.raises(SurfaceTypeError):
        RgbColor(*components)  # type: ignore[arg-type]


def test_rgb_parses_only_canonical_schema_color() -> None:
    assert RgbColor.from_schema_color("#0A10FF") == RgbColor(10, 16, 255)

    for value in ("#0a10ff", "0A10FF", "#FFF", "#GG0000", 123):
        with pytest.raises((SurfaceTypeError, SurfaceValidationError)):
            RgbColor.from_schema_color(value)


def test_surface_operations_require_rgb_color_values() -> None:
    surface = RegionSurface(2, 2)
    with pytest.raises(SurfaceTypeError):
        surface.fill((1, 2, 3))  # type: ignore[arg-type]
    with pytest.raises(SurfaceTypeError):
        surface.set_pixel(0, 0, (1, 2, 3))  # type: ignore[arg-type]
    with pytest.raises(SurfaceTypeError):
        surface.fill_rect(0, 0, 1, 1, (1, 2, 3))  # type: ignore[arg-type]


def test_to_bytes_is_immutable_and_does_not_alias_backing_storage() -> None:
    surface = RegionSurface(2, 1, RgbColor(1, 2, 3))

    first = surface.to_bytes()
    second = surface.to_bytes()
    assert isinstance(first, bytes)
    assert first == second
    assert first is not second

    surface.set_pixel(0, 0, RgbColor(9, 9, 9))
    assert first == RgbColor(1, 2, 3).to_bytes() * 2
    assert surface.to_bytes() != first


def test_two_surfaces_never_share_mutable_data() -> None:
    left = RegionSurface(2, 1)
    right = RegionSurface(2, 1)

    left.set_pixel(0, 0, RgbColor(1, 2, 3))

    assert left.to_bytes() != right.to_bytes()
    assert right.to_bytes() == bytes(6)


def test_corrupt_private_storage_is_detected_before_export() -> None:
    surface = RegionSurface(2, 2)
    surface._data.pop()  # type: ignore[attr-defined]

    with pytest.raises(SurfaceInvariantError):
        surface.to_bytes()
