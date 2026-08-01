"""Immutable checked-in native pixel icons.

The bitmap rows are deliberately isolated here so physical visual review can
adjust an icon without changing widget or compositor architecture.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from nextuxlinq.widget_models import BuiltInIconId


@dataclass(frozen=True, slots=True)
class BuiltInIconDefinition:
    icon_id: BuiltInIconId
    display_label: str
    rows: tuple[str, ...]
    fixed_color: str
    fixed_color_label: str
    effect_eligible: bool

    @property
    def width(self) -> int:
        return len(self.rows[0])

    @property
    def height(self) -> int:
        return len(self.rows)


# Simplified native pixel adaptation of the CachyOS mark.  This is an exact
# 15x10 fixture: 1 means CachyOS Turquoise and 0 means transparent/background.
CACHYOS_15X10_BITMAP = (
    "000111111111000",
    "001110000011100",
    "011100000001110",
    "111000111000111",
    "110001111100011",
    "110001100000000",
    "111000111100000",
    "011100001110110",
    "001110000011100",
    "000111111111000",
)


def _validated_catalog() -> Mapping[BuiltInIconId, BuiltInIconDefinition]:
    definition = BuiltInIconDefinition(
        icon_id=BuiltInIconId.CACHYOS,
        display_label="CachyOS Logo",
        rows=CACHYOS_15X10_BITMAP,
        fixed_color="#1DC7B5",
        fixed_color_label="CachyOS Turquoise",
        effect_eligible=False,
    )
    if definition.width != 15 or definition.height != 10:
        raise RuntimeError("The CachyOS native icon must be exactly 15x10.")
    if any(set(row) - {"0", "1"} or len(row) != 15 for row in definition.rows):
        raise RuntimeError("The CachyOS native icon bitmap is invalid.")
    return MappingProxyType({definition.icon_id: definition})


BUILT_IN_ICON_CATALOG = _validated_catalog()


def resolve_built_in_icon(icon_id: BuiltInIconId) -> BuiltInIconDefinition:
    """Resolve a strictly validated canonical built-in icon identifier."""
    if not isinstance(icon_id, BuiltInIconId):
        raise TypeError("icon_id must be a BuiltInIconId.")
    return BUILT_IN_ICON_CATALOG[icon_id]

