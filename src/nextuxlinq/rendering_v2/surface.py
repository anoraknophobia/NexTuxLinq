"""Strict region-local RGB888 drawing surfaces for schema-v2 rendering."""

from __future__ import annotations

import re
from dataclasses import dataclass

from nextuxlinq.creator import MATRIX_HEIGHT, MATRIX_WIDTH, RGB_CHANNELS

_CANONICAL_COLOR = re.compile(r"^#[0-9A-F]{6}$")


class SurfaceValidationError(ValueError):
    """A drawing value violates the RegionSurface contract."""


class SurfaceTypeError(TypeError):
    """A drawing value has an unsupported type."""


class SurfaceInvariantError(RuntimeError):
    """The private surface buffer no longer has its required size."""


def _strict_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SurfaceTypeError(f"{name} must be an integer.")
    return value


@dataclass(frozen=True, slots=True)
class RgbColor:
    """One immutable, strictly validated RGB888 pixel."""

    red: int
    green: int
    blue: int

    def __post_init__(self) -> None:
        for name, component in (
            ("red", self.red),
            ("green", self.green),
            ("blue", self.blue),
        ):
            value = _strict_integer(component, name)
            if not 0 <= value <= 255:
                raise SurfaceValidationError(
                    f"{name} must be between 0 and 255."
                )

    @classmethod
    def from_schema_color(cls, value: object) -> RgbColor:
        """Parse one canonical schema color in uppercase ``#RRGGBB`` form."""
        if not isinstance(value, str):
            raise SurfaceTypeError("schema color must be a string.")
        if not _CANONICAL_COLOR.fullmatch(value):
            raise SurfaceValidationError(
                "schema color must use canonical #RRGGBB form."
            )
        return cls(
            red=int(value[1:3], 16),
            green=int(value[3:5], 16),
            blue=int(value[5:7], 16),
        )

    def to_bytes(self) -> bytes:
        """Return the immutable three-byte RGB888 representation."""
        return bytes((self.red, self.green, self.blue))


BLACK = RgbColor(0, 0, 0)


class RegionSurface:
    """An isolated local drawing surface with deterministic clipping."""

    __slots__ = ("_data", "_foreground_mask", "_height", "_width")

    def __init__(
        self,
        width: int,
        height: int,
        initial_color: RgbColor | None = None,
    ) -> None:
        normalized_width = _strict_integer(width, "surface width")
        normalized_height = _strict_integer(height, "surface height")
        if not 1 <= normalized_width <= MATRIX_WIDTH:
            raise SurfaceValidationError(
                f"surface width must be between 1 and {MATRIX_WIDTH}."
            )
        if not 1 <= normalized_height <= MATRIX_HEIGHT:
            raise SurfaceValidationError(
                f"surface height must be between 1 and {MATRIX_HEIGHT}."
            )
        self._width = normalized_width
        self._height = normalized_height
        self._data = bytearray(self.byte_length)
        self._foreground_mask = bytearray(self.pixel_count)
        self.fill(BLACK if initial_color is None else initial_color)

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def byte_length(self) -> int:
        return self._width * self._height * RGB_CHANNELS

    @property
    def pixel_count(self) -> int:
        return self._width * self._height

    @staticmethod
    def _require_color(color: object) -> RgbColor:
        if not isinstance(color, RgbColor):
            raise SurfaceTypeError("color must be an RgbColor.")
        return color

    def _verify_storage(self) -> None:
        if len(self._data) != self.byte_length:
            raise SurfaceInvariantError(
                "RegionSurface storage has an invalid byte length."
            )
        if len(self._foreground_mask) != self.pixel_count:
            raise SurfaceInvariantError(
                "RegionSurface foreground mask has an invalid byte length."
            )

    def fill(self, color: RgbColor, *, effect_eligible: bool = False) -> None:
        """Fill the complete local surface with one opaque color."""
        if not isinstance(effect_eligible, bool):
            raise SurfaceTypeError("effect_eligible must be a boolean.")
        pixel = self._require_color(color).to_bytes()
        self._data[:] = pixel * (self._width * self._height)
        self._foreground_mask[:] = bytes((int(effect_eligible),)) * self.pixel_count
        self._verify_storage()

    def set_pixel(
        self,
        x: int,
        y: int,
        color: RgbColor,
        *,
        effect_eligible: bool = True,
    ) -> bool:
        """Write one semantic foreground pixel, returning false if clipped."""
        normalized_x = _strict_integer(x, "pixel x")
        normalized_y = _strict_integer(y, "pixel y")
        if not isinstance(effect_eligible, bool):
            raise SurfaceTypeError("effect_eligible must be a boolean.")
        pixel = self._require_color(color).to_bytes()
        if not (
            0 <= normalized_x < self._width
            and 0 <= normalized_y < self._height
        ):
            return False
        offset = (
            (normalized_y * self._width + normalized_x) * RGB_CHANNELS
        )
        self._data[offset : offset + RGB_CHANNELS] = pixel
        mask_offset = normalized_y * self._width + normalized_x
        self._foreground_mask[mask_offset] = int(effect_eligible)
        self._verify_storage()
        return True

    def fill_rect(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        color: RgbColor,
        *,
        effect_eligible: bool = False,
    ) -> int:
        """Fill the clipped intersection of a local rectangle.

        The returned integer is the number of pixels written.
        """
        normalized_x = _strict_integer(x, "rectangle x")
        normalized_y = _strict_integer(y, "rectangle y")
        normalized_width = _strict_integer(width, "rectangle width")
        normalized_height = _strict_integer(height, "rectangle height")
        if not isinstance(effect_eligible, bool):
            raise SurfaceTypeError("effect_eligible must be a boolean.")
        pixel = self._require_color(color).to_bytes()
        if normalized_width < 0 or normalized_height < 0:
            raise SurfaceValidationError(
                "rectangle width and height must not be negative."
            )
        if normalized_width == 0 or normalized_height == 0:
            return 0

        start_x = max(0, normalized_x)
        start_y = max(0, normalized_y)
        end_x = min(self._width, normalized_x + normalized_width)
        end_y = min(self._height, normalized_y + normalized_height)
        if start_x >= end_x or start_y >= end_y:
            return 0

        row = pixel * (end_x - start_x)
        mask_row = bytes((int(effect_eligible),)) * (end_x - start_x)
        for row_y in range(start_y, end_y):
            offset = (row_y * self._width + start_x) * RGB_CHANNELS
            self._data[offset : offset + len(row)] = row
            mask_offset = row_y * self._width + start_x
            self._foreground_mask[
                mask_offset : mask_offset + len(mask_row)
            ] = mask_row
        self._verify_storage()
        return (end_x - start_x) * (end_y - start_y)

    def _write_rgb_row(self, x: int, y: int, payload: bytes) -> None:
        """Copy one exact in-bounds RGB row segment for the compositor."""
        normalized_x = _strict_integer(x, "row x")
        normalized_y = _strict_integer(y, "row y")
        if not isinstance(payload, bytes):
            raise SurfaceTypeError("row payload must be bytes.")
        if len(payload) % RGB_CHANNELS:
            raise SurfaceValidationError(
                "row payload must contain complete RGB888 pixels."
            )
        pixel_width = len(payload) // RGB_CHANNELS
        if (
            normalized_x < 0
            or normalized_y < 0
            or normalized_y >= self._height
            or normalized_x + pixel_width > self._width
        ):
            raise SurfaceValidationError(
                "row payload must fit completely inside the surface."
            )
        offset = (
            (normalized_y * self._width + normalized_x) * RGB_CHANNELS
        )
        self._data[offset : offset + len(payload)] = payload
        self._verify_storage()

    def _write_mask_row(self, x: int, y: int, payload: bytes) -> None:
        """Copy one exact in-bounds mask row segment for the compositor."""
        normalized_x = _strict_integer(x, "mask row x")
        normalized_y = _strict_integer(y, "mask row y")
        if not isinstance(payload, bytes):
            raise SurfaceTypeError("mask row payload must be bytes.")
        if any(value not in (0, 1) for value in payload):
            raise SurfaceValidationError("mask row values must be zero or one.")
        if (
            normalized_x < 0
            or normalized_y < 0
            or normalized_y >= self._height
            or normalized_x + len(payload) > self._width
        ):
            raise SurfaceValidationError(
                "mask row payload must fit completely inside the surface."
            )
        offset = normalized_y * self._width + normalized_x
        self._foreground_mask[offset : offset + len(payload)] = payload
        self._verify_storage()

    def to_bytes(self) -> bytes:
        """Return an immutable copy of the local RGB888 framebuffer."""
        self._verify_storage()
        return bytes(self._data)

    def foreground_mask(self) -> bytes:
        """Return an immutable copy of semantic effect eligibility."""
        self._verify_storage()
        return bytes(self._foreground_mask)
