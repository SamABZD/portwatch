"""Deterministic event-time visit and candidate-waiting inference; no external inputs."""

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import groupby

RULE_VERSION = "visits-v1"


class InferenceError(ValueError):
    """Actionable, non-secret inference failure."""


def identity(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def utc(value: datetime) -> None:
    if value.utcoffset() != timedelta(0):
        raise InferenceError("observation and cutoff timestamps must explicitly use UTC")


@dataclass(frozen=True)
class VisitRules:
    waiting_speed_knots: float = 1.0
    waiting_min_seconds: float = 1800
    max_gap_seconds: float = 900
    persistence_seconds: float = 120
    min_consistent_observations: int = 2

    def __post_init__(self):
        values = (
            self.waiting_speed_knots,
            self.waiting_min_seconds,
            self.max_gap_seconds,
            self.persistence_seconds,
        )
        if any(not math.isfinite(v) for v in values) or self.waiting_speed_knots < 0:
            raise InferenceError("rules require finite, nonnegative speed and positive time limits")
        if any(v <= 0 for v in values[1:]) or self.min_consistent_observations < 2:
            raise InferenceError("persistence requires at least two distinct observation times")

    @property
    def document(self):
        return {
            "rule_version": RULE_VERSION,
            "waiting_speed_knots": float(self.waiting_speed_knots),
            "waiting_min_seconds": float(self.waiting_min_seconds),
            "max_gap_seconds": float(self.max_gap_seconds),
            "persistence_seconds": float(self.persistence_seconds),
            "min_consistent_observations": self.min_consistent_observations,
        }

    @property
    def rule_id(self):
        return identity(self.document)


@dataclass(frozen=True)
class TrackPoint:
    position_id: int
    observation_id: str
    at: datetime
    speed_knots: float | None
    harbor: bool
    terminal: bool
    candidate_zones: tuple[str, ...] = ()
    quality_flags: tuple[str, ...] = ()

    def __post_init__(self):
        utc(self.at)
        if not self.observation_id:
            raise InferenceError("observation identity is required")
        if self.speed_knots is not None and (
            not math.isfinite(self.speed_knots) or self.speed_knots < 0
        ):
            raise InferenceError("speed must be nonnegative and finite, or unknown")


@dataclass(frozen=True)
class Evidence:
    points: tuple[TrackPoint, ...]
    at: datetime
    state: str
    port_inside: bool | None
    waiting: bool | None
    candidate_zones: tuple[str, ...]
    gap_before_seconds: float
    interval_state_before: str
    flags: tuple[str, ...]
    segment: int

    @property
    def representative(self):
        return self.points[0]


def evidence(points: list[TrackPoint], rules: VisitRules) -> list[Evidence]:
    result = []
    segment = 0
    for at, group in groupby(
        sorted(points, key=lambda p: (p.at, p.observation_id)), key=lambda p: p.at
    ):
        same_time = tuple(group)
        kinds = {(point.terminal, point.harbor) for point in same_time}
        previous = result[-1] if result else None
        gap = (at - previous.at).total_seconds() if previous else 0
        flags = {flag for point in same_time for flag in point.quality_flags}
        if len(same_time) > 1:
            flags.add("multiple_records_same_time")
        common = set(same_time[0].candidate_zones)
        for point in same_time[1:]:
            common.intersection_update(point.candidate_zones)
        if len(kinds) > 1:
            inside, waiting, state = None, None, "unknown"
            flags.add("conflicting_timestamp_memberships")
        else:
            terminal, harbor = next(iter(kinds))
            inside = terminal or harbor
            if terminal or not common:
                waiting = False
            elif any(point.speed_knots is None for point in same_time):
                waiting = None
                flags.add("waiting_speed_unknown")
            else:
                waiting = all(point.speed_knots <= rules.waiting_speed_knots for point in same_time)
            state = (
                "terminal"
                if terminal
                else "candidate_waiting"
                if waiting is True
                else "harbor"
                if harbor
                else "outside"
            )
        if previous and (
            gap > rules.max_gap_seconds or state == "unknown" or previous.state == "unknown"
        ):
            segment += 1
        if gap > rules.max_gap_seconds:
            flags.add("reporting_gap")
        interval = (
            state
            if previous
            and gap <= rules.max_gap_seconds
            and previous.state == state
            and state != "unknown"
            else "unknown"
        )
        result.append(
            Evidence(
                same_time,
                at,
                state,
                inside,
                waiting,
                tuple(sorted(common)),
                gap,
                interval,
                tuple(sorted(flags)),
                segment,
            )
        )
    return result


@dataclass(frozen=True)
class VisitEpisode:
    visit_id: str
    segment: int
    first_position_id: int
    last_position_id: int
    observed_start: datetime
    observed_end: datetime
    arrival_lower: datetime | None
    arrival_upper: datetime | None
    arrival_confirmed_at: datetime
    departure_lower: datetime | None
    departure_upper: datetime | None
    departure_confirmed_at: datetime | None
    known_at: datetime
    completed: bool
    completeness_reasons: tuple[str, ...]
    gap_indicators: tuple[str, ...]
    boundary_jitter_count: int
    observation_count: int
    position_count: int
    supported_inside_seconds: float
    observed_span_seconds: float
    duration_min_seconds: float | None
    duration_max_seconds: float | None
    duration_semantics: str


def _visit(
    partition_id,
    bins,
    arrival_lower,
    confirmed_at,
    left_reason,
    departure,
    known_at,
    right_reason,
    jitter_count,
):
    inside = [b for b in bins if b.port_inside is True]
    first, last = inside[0], inside[-1]
    departure_lower = last.at if departure else None
    departure_upper = departure[0].at if departure else None
    completed = arrival_lower is not None and departure is not None
    reasons = set()
    if arrival_lower is None:
        reasons.update(("arrival_unknown", left_reason))
    if departure is None:
        reasons.update(("departure_unknown", right_reason))
    reasons.discard(None)
    gap_flags = tuple(
        sorted(
            reasons
            & {"reporting_gap", "trailing_reporting_gap", "conflicting_timestamp_memberships"}
        )
    )
    supported = sum(
        (b.at - a.at).total_seconds()
        for a, b in zip(bins, bins[1:], strict=False)
        if a.port_inside is True and b.port_inside is True
    )
    return VisitEpisode(
        identity([partition_id, "visit", first.representative.observation_id]),
        first.segment,
        first.representative.position_id,
        last.representative.position_id,
        first.at,
        last.at,
        arrival_lower,
        first.at if arrival_lower is not None else None,
        confirmed_at,
        departure_lower,
        departure_upper,
        departure[-1].at if departure else None,
        known_at,
        completed,
        tuple(sorted(reasons)),
        gap_flags,
        jitter_count,
        len(bins),
        sum(len(b.points) for b in bins),
        supported,
        (last.at - first.at).total_seconds(),
        (departure_lower - first.at).total_seconds() if completed else None,
        (departure_upper - arrival_lower).total_seconds() if completed else None,
        "completed_transition_interval" if completed else "partial_observation_span",
    )


def infer_visits(states, rules, partition_id, cutoff):
    visits = []
    entry, exit_pending, active = [], [], []
    last_outside = arrival_lower = confirmed_at = None
    segment_left = "slice_start"
    left_reason = None
    jitter = 0
    previous = None
    for current in states:
        broken = previous and (current.segment != previous.segment)
        if broken:
            reason = (
                "reporting_gap"
                if current.gap_before_seconds > rules.max_gap_seconds
                else "conflicting_timestamp_memberships"
            )
            if active:
                visits.append(
                    _visit(
                        partition_id,
                        active,
                        arrival_lower,
                        confirmed_at,
                        left_reason,
                        None,
                        current.at,
                        reason,
                        jitter,
                    )
                )
            entry, exit_pending, active = [], [], []
            last_outside = None
            segment_left = reason
        previous = current
        if current.port_inside is None:
            continue
        if not active:
            if current.port_inside:
                entry.append(current)
                if (
                    len(entry) >= rules.min_consistent_observations
                    and (current.at - entry[0].at).total_seconds() >= rules.persistence_seconds
                ):
                    active = entry
                    entry = []
                    arrival_lower = last_outside.at if last_outside else None
                    confirmed_at, left_reason = current.at, segment_left
                    jitter = 0
            else:
                entry = []
                last_outside = current
            continue
        if current.port_inside:
            jitter += len(exit_pending)
            active.extend(exit_pending)
            exit_pending = []
            active.append(current)
        else:
            exit_pending.append(current)
            if (
                len(exit_pending) >= rules.min_consistent_observations
                and (current.at - exit_pending[0].at).total_seconds() >= rules.persistence_seconds
            ):
                visits.append(
                    _visit(
                        partition_id,
                        active,
                        arrival_lower,
                        confirmed_at,
                        left_reason,
                        exit_pending,
                        current.at,
                        None,
                        jitter,
                    )
                )
                active, exit_pending = [], []
                last_outside = current
    if active:
        reason = (
            "trailing_reporting_gap"
            if (cutoff - states[-1].at).total_seconds() > rules.max_gap_seconds
            else "insufficient_exit_persistence"
            if exit_pending
            else "slice_end"
        )
        visits.append(
            _visit(
                partition_id,
                active,
                arrival_lower,
                confirmed_at,
                left_reason,
                None,
                cutoff,
                reason,
                jitter,
            )
        )
    return visits


@dataclass(frozen=True)
class WaitingEpisode:
    episode_id: str
    segment: int
    first_position_id: int
    last_position_id: int
    observed_start: datetime
    observed_end: datetime
    start_lower: datetime | None
    start_upper: datetime | None
    end_lower: datetime | None
    end_upper: datetime | None
    qualification_known_at: datetime
    known_at: datetime
    completed: bool
    completeness_reasons: tuple[str, ...]
    gap_indicators: tuple[str, ...]
    observation_count: int
    position_count: int
    candidate_zone_ids: tuple[str, ...]
    candidate_duration_seconds: float
    associated_visit_id: str | None = None
    association_known_at: datetime | None = None


def _waiting(
    partition_id, bins, rules, start_lower, left_reason, end_upper, known_at, right_reason
):
    first, last = bins[0], bins[-1]
    duration = (last.at - first.at).total_seconds()
    if duration < rules.waiting_min_seconds:
        return None
    common = set(first.candidate_zones)
    for b in bins[1:]:
        common.intersection_update(b.candidate_zones)
    reasons = set()
    if start_lower is None:
        reasons.update(("waiting_start_unknown", left_reason))
    if end_upper is None:
        reasons.update(("waiting_end_unknown", right_reason))
    reasons.discard(None)
    qualification = next(
        b.at for b in bins if (b.at - first.at).total_seconds() >= rules.waiting_min_seconds
    )
    return WaitingEpisode(
        identity([partition_id, "waiting", first.representative.observation_id]),
        first.segment,
        first.representative.position_id,
        last.representative.position_id,
        first.at,
        last.at,
        start_lower,
        first.at if start_lower is not None else None,
        last.at if end_upper is not None else None,
        end_upper,
        qualification,
        known_at,
        start_lower is not None and end_upper is not None,
        tuple(sorted(reasons)),
        tuple(
            sorted(
                reasons
                & {"reporting_gap", "trailing_reporting_gap", "conflicting_timestamp_memberships"}
            )
        ),
        len(bins),
        sum(len(b.points) for b in bins),
        tuple(sorted(common)),
        duration,
    )


def infer_waiting(states, rules, partition_id, cutoff, visits):
    from dataclasses import replace

    episodes, run = [], []
    common = set()
    start_lower = previous = None
    left_reason = "slice_start"
    for current in states:
        broken = previous and current.segment != previous.segment
        changed_zone = (
            run and current.waiting is True and not common.intersection(current.candidate_zones)
        )
        if run and (broken or changed_zone or current.waiting is not True):
            reason = (
                "reporting_gap"
                if broken and current.gap_before_seconds > rules.max_gap_seconds
                else "conflicting_timestamp_memberships"
                if broken
                else "candidate_zone_change"
                if changed_zone
                else "unknown_waiting_state"
                if current.waiting is None
                else None
            )
            end_upper = (
                current.at if not broken and not changed_zone and current.waiting is False else None
            )
            episode = _waiting(
                partition_id, run, rules, start_lower, left_reason, end_upper, current.at, reason
            )
            if episode:
                episodes.append(episode)
            run = []
            common = set()
        if current.waiting is True:
            if not run:
                common = set(current.candidate_zones)
                start_lower = (
                    previous.at if previous and not broken and previous.waiting is False else None
                )
                left_reason = (
                    "reporting_gap"
                    if broken and current.gap_before_seconds > rules.max_gap_seconds
                    else "unknown_waiting_state"
                    if previous
                    else "slice_start"
                )
            run.append(current)
            common.intersection_update(current.candidate_zones)
        previous = current
    if run:
        reason = (
            "trailing_reporting_gap"
            if (cutoff - states[-1].at).total_seconds() > rules.max_gap_seconds
            else "slice_end"
        )
        episode = _waiting(partition_id, run, rules, start_lower, left_reason, None, cutoff, reason)
        if episode:
            episodes.append(episode)
    associated = []
    for episode in episodes:
        candidates = [
            visit
            for visit in visits
            if visit.segment == episode.segment
            and (
                (
                    visit.observed_start <= episode.observed_end
                    and visit.observed_end >= episode.observed_start
                )
                or (
                    0
                    < (visit.observed_start - episode.observed_end).total_seconds()
                    <= rules.max_gap_seconds
                )
            )
        ]
        if candidates:
            chosen = min(candidates, key=lambda v: (v.observed_start, v.visit_id))
            association_at = max(episode.qualification_known_at, chosen.arrival_confirmed_at)
            episode = replace(
                episode,
                associated_visit_id=chosen.visit_id,
                association_known_at=association_at,
                known_at=max(episode.known_at, association_at),
            )
        associated.append(episode)
    return associated


@dataclass(frozen=True)
class TrackInference:
    states: tuple[Evidence, ...]
    visits: tuple[VisitEpisode, ...]
    waiting: tuple[WaitingEpisode, ...]
    current_state: str
    result_as_of: datetime


def infer_track(
    points: list[TrackPoint],
    rules: VisitRules,
    partition_id: str,
    as_of_utc: datetime | None = None,
) -> TrackInference:
    """Prefix inference excludes future event times; stored full-history output is retrospective."""
    if as_of_utc is None:
        if not points:
            raise InferenceError("a nonempty track or explicit cutoff is required")
        as_of_utc = max(point.at for point in points)
    utc(as_of_utc)
    states = evidence([point for point in points if point.at <= as_of_utc], rules)
    visits = infer_visits(states, rules, partition_id, as_of_utc)
    waiting = infer_waiting(states, rules, partition_id, as_of_utc, visits)
    current = (
        states[-1].state
        if states and (as_of_utc - states[-1].at).total_seconds() <= rules.max_gap_seconds
        else "unknown"
    )
    return TrackInference(tuple(states), tuple(visits), tuple(waiting), current, as_of_utc)
