"""Immutable update-policy metadata for inactive free-layout rendering."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from nextuxlinq.widget_models import (
    CANONICAL_WIDGET_SETTINGS_MODELS,
    ClockSettings,
    WidgetSettings,
)


class UpdatePolicy(StrEnum):
    """Stable settings-dependent frame update policies."""

    STATIC = "static"
    CONTINUOUS = "continuous"
    HALF_SECOND = "half_second"
    SECOND_BOUNDARY = "second_boundary"
    MINUTE_BOUNDARY = "minute_boundary"
    DAY_BOUNDARY = "day_boundary"


UPDATE_POLICY_FREQUENCY: Mapping[UpdatePolicy, int] = MappingProxyType(
    {
        UpdatePolicy.CONTINUOUS: 0,
        UpdatePolicy.HALF_SECOND: 1,
        UpdatePolicy.SECOND_BOUNDARY: 2,
        UpdatePolicy.MINUTE_BOUNDARY: 3,
        UpdatePolicy.DAY_BOUNDARY: 4,
        UpdatePolicy.STATIC: 5,
    }
)

ANIMATED_UPDATE_POLICIES = frozenset(
    {
        UpdatePolicy.CONTINUOUS,
        UpdatePolicy.HALF_SECOND,
        UpdatePolicy.SECOND_BOUNDARY,
    }
)


@dataclass(frozen=True, slots=True)
class UpdatePolicyResolver:
    """One immutable exact-settings policy rule."""

    settings_model: type[WidgetSettings]
    fixed_policy: UpdatePolicy | None
    clock_dependent: bool = False

    def __post_init__(self) -> None:
        if self.settings_model not in set(
            CANONICAL_WIDGET_SETTINGS_MODELS.values()
        ):
            raise TypeError("settings_model must be canonical.")
        if not isinstance(self.clock_dependent, bool):
            raise TypeError("clock_dependent must be a boolean.")
        if self.clock_dependent:
            if (
                self.settings_model is not ClockSettings
                or self.fixed_policy is not None
            ):
                raise TypeError(
                    "clock-dependent policy requires ClockSettings."
                )
        elif not isinstance(self.fixed_policy, UpdatePolicy):
            raise TypeError("fixed_policy must be an UpdatePolicy.")

    @classmethod
    def fixed(
        cls,
        settings_model: type[WidgetSettings],
        policy: UpdatePolicy,
    ) -> UpdatePolicyResolver:
        return cls(settings_model, policy)

    @classmethod
    def clock(cls) -> UpdatePolicyResolver:
        return cls(ClockSettings, None, clock_dependent=True)

    def resolve(self, settings: WidgetSettings) -> UpdatePolicy:
        if type(settings) is not self.settings_model:
            raise TypeError(
                "update policy requires exact canonical settings."
            )
        if self.clock_dependent:
            assert type(settings) is ClockSettings
            if settings.blink_separator:
                return UpdatePolicy.HALF_SECOND
            if settings.show_seconds:
                return UpdatePolicy.SECOND_BOUNDARY
            return UpdatePolicy.MINUTE_BOUNDARY
        assert self.fixed_policy is not None
        return self.fixed_policy


def aggregate_update_policies(
    policies: Iterable[UpdatePolicy],
) -> UpdatePolicy:
    """Return the explicitly highest-frequency supplied policy."""
    aggregate = UpdatePolicy.STATIC
    for policy in policies:
        if not isinstance(policy, UpdatePolicy):
            raise TypeError("policies must contain UpdatePolicy values.")
        if (
            UPDATE_POLICY_FREQUENCY[policy]
            < UPDATE_POLICY_FREQUENCY[aggregate]
        ):
            aggregate = policy
    return aggregate


def policy_has_animated_content(policy: UpdatePolicy) -> bool:
    """Return the stable compatibility meaning of animated content."""
    if not isinstance(policy, UpdatePolicy):
        raise TypeError("policy must be an UpdatePolicy.")
    return policy in ANIMATED_UPDATE_POLICIES
