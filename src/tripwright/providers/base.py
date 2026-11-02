"""Shared contract for external data providers.

Every provider result is a `Sourced` value: the payload plus where it came
from, when it was fetched, and until when it may be treated as current.
Checks use `valid_until` to decide whether a fact must be re-queried before a
plan can be marked verified. A provider never returns an empty success when
the upstream failed: it raises `ProviderUnavailable` with the reason, so a
caller can say "could not verify" instead of "verified".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Generic, Protocol, TypeVar

T = TypeVar("T")


class ProviderUnavailable(Exception):
    """The upstream could not answer. `reason` is shown to the user verbatim."""

    def __init__(self, provider: str, reason: str, *, retryable: bool = True):
        super().__init__(f"{provider}: {reason}")
        self.provider = provider
        self.reason = reason
        self.retryable = retryable


@dataclass(frozen=True)
class Sourced(Generic[T]):
    value: T
    source: str  # e.g. "transitous", "open-meteo"; shown as attribution
    fetched_at: datetime  # timezone-aware
    valid_until: datetime | None  # None: no known expiry
    stale: bool = False  # served from an old cache because the upstream failed
    notes: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)  # what the upstream answered about the question itself (A8)


@dataclass(frozen=True)
class Place:
    name: str
    lat: float
    lon: float
    id: str | None = None  # provider-specific stop or place id
    modes: tuple[str, ...] = ()  # transport modes that stop here, as the provider names them ("REGIONAL_RAIL", "BUS")
    areas: tuple[str, ...] = ()  # the place's municipality, district and state names, smallest last


@dataclass(frozen=True)
class Leg:
    mode: str  # "walk", "rail", "bus", "tram", "ferry", ...
    origin: str
    destination: str
    depart: datetime
    arrive: datetime
    line: str | None = None
    platform: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Itinerary:
    legs: list[Leg]

    @property
    def depart(self) -> datetime:
        return self.legs[0].depart

    @property
    def arrive(self) -> datetime:
        return self.legs[-1].arrive


class TransitProvider(Protocol):
    async def stops(self, text: str, *, limit: int = 8) -> Sourced[list[Place]]: ...

    async def plan(
        self,
        origin: Place | str,
        destination: Place | str,
        when: datetime,
        *,
        arrive_by: bool = False,
        limit: int = 5,
    ) -> Sourced[list[Itinerary]]: ...

    async def walk_minutes(self, origin: Place, destinations: list[Place]) -> Sourced[list[int | None]]: ...


class WeatherProvider(Protocol):
    async def day(self, place: Place, date: str) -> Sourced[dict[str, Any]]: ...


class GeocodeProvider(Protocol):
    async def search(self, text: str, *, limit: int = 5) -> Sourced[list[Place]]: ...
