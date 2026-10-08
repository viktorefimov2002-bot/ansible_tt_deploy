"""Worker-owned alert transitions; PostgreSQL owns incidents and debounce state."""

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from apps.monitoring.query import FRESH_SECONDS, MetricsUnavailable, number
from apps.monitoring.service import fresh
from apps.persistence.database import transaction
from apps.persistence.models import AuditEvent, OperationalAlert, Server

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Rule:
    open_seconds: int
    recover_seconds: int = 60
    high: float | None = None
    low: float | None = None


RULES = {
    "NODE_OFFLINE": Rule(60),
    "SERVICE_DOWN": Rule(60),
    "DISK_HIGH": Rule(300, high=90, low=85),
    "MEMORY_HIGH": Rule(300, high=90, low=80),
}


def values_by_server(rows):
    values, duplicates = {}, set()
    for row in rows:
        key = (row["metric"].get("server_id"), row["metric"].get("metric"))
        if key in values:
            duplicates.add(key)
        values[key] = number(row["value"][1])
    for key in duplicates:
        values[key] = None  # Ambiguous observations are never chosen arbitrarily.
    return values


def observation(kind, values, collector_time, now):
    """Return a condition (bad/good/unknown) and the original source clock."""
    observed = values.get("observed")
    scrape = values.get("scrape")
    if observed is None or observed > now or scrape not in (0, 1):
        return None, None
    reachable = fresh(observed, now) and scrape == 1
    if kind == "NODE_OFFLINE":
        # Staleness is meaningful only while the collector itself is fresh/up.
        # Recovery must progress on distinct successful node samples, not on
        # collector polls that keep returning the same recent node sample.
        return not reachable, observed if reachable else collector_time
    if not reachable:
        return None, None
    if kind == "SERVICE_DOWN":
        probe, active, process = (values.get(key) for key in ("probe", "active", "process"))
        if probe == 0:
            return True, observed  # Explicitly failed helper: service health degraded.
        if probe != 1 or active not in (0, 1) or process not in (0, 1):
            return None, None
        return active != 1 or process != 1, observed
    metric = values.get("disk_percent" if kind == "DISK_HIGH" else "memory_percent")
    if metric is None or not 0 <= metric <= 100:
        return None, None
    rule = RULES[kind]
    # The band preserves an open incident but breaks a pending transition.
    return True if metric >= rule.high else False if metric <= rule.low else None, observed


def advance(state, bad, observed, evaluated, rule):
    """Mutate a locked cursor; return at most one open/recovery transition."""
    if state.last_evaluated_at is not None and evaluated <= state.last_evaluated_at:
        return None
    previous_evaluation = state.last_evaluated_at
    state.last_evaluated_at = evaluated
    if bad is None or observed is None:
        state.pending_state = state.pending_since = None
        return None
    source = datetime.fromtimestamp(observed, UTC)
    if state.last_observed_at is not None and source <= state.last_observed_at:
        return None  # Polling the same scrape never advances debounce.
    gap = (
        state.last_observed_at is None
        or (source - state.last_observed_at).total_seconds() > FRESH_SECONDS
        or previous_evaluation is None
        or (evaluated - previous_evaluation).total_seconds() > FRESH_SECONDS
    )
    state.last_observed_at = source
    active = state.incident_id is not None
    if bad == active:
        state.pending_state = state.pending_since = None
        return None
    if gap or state.pending_state != bad:
        state.pending_state, state.pending_since = bad, source
    duration = rule.open_seconds if bad else rule.recover_seconds
    if (source - state.pending_since).total_seconds() < duration:
        return None
    incident = state.incident_id or uuid4()
    state.incident_id = incident if bad else None
    state.opened_at = evaluated if bad else None
    state.pending_state = state.pending_since = None
    return ("open" if bad else "recovered"), incident


class OperationalAlertService:
    def __init__(self, engine, metrics):
        self.engine, self.metrics = engine, metrics
        self.next_poll = 0.0

    async def tick(self):
        # Process-local throttling is only an optimization; all business state
        # and duplicate protection stay in PostgreSQL.
        if time.monotonic() < self.next_poll:
            return
        self.next_poll = time.monotonic() + 30
        try:
            await self.reconcile()
        except SQLAlchemyError:
            logger.warning("alert_persistence_unavailable")

    async def reconcile(self, *, now=None):
        now = now or datetime.now(UTC)
        try:
            rows = await self.metrics.alerts(now.timestamp())
        except MetricsUnavailable:
            rows = []
        values = values_by_server(rows)
        collector_time = values.get((None, "collector_observed"))
        collector_ok = values.get((None, "collector")) == 1 and fresh(
            collector_time, now.timestamp()
        )
        async with transaction(self.engine) as db:
            # Match the collector's management capability without loading keys.
            nodes = await db.execute(
                select(
                    Server.id,
                    Server.enabled,
                    Server.lifecycle_state,
                    Server.config_state,
                    (Server.ssh_user == "ttcp").label("managed"),
                    Server.ssh_host_key.is_not(None).label("pinned"),
                    Server.ssh_private_ciphertext.is_not(None).label("identity"),
                )
                .order_by(Server.id)
                .with_for_update()
            )
            for node in nodes:
                await db.execute(
                    insert(OperationalAlert)
                    .values([{"server_id": node.id, "type": kind} for kind in RULES])
                    .on_conflict_do_nothing()
                )
                states = await db.scalars(
                    select(OperationalAlert)
                    .where(OperationalAlert.server_id == node.id)
                    .order_by(OperationalAlert.type)
                    .with_for_update()
                )
                metrics = {
                    key: value
                    for (identity, key), value in values.items()
                    if identity == str(node.id)
                }
                for state in states:
                    usable = collector_ok and node.enabled and node.managed and node.pinned
                    usable = usable and node.identity
                    if state.type == "SERVICE_DOWN":
                        usable = (
                            usable
                            and node.lifecycle_state
                            not in (
                                "uninstalled",
                                "deploy_pending",
                                "update_pending",
                                "restart_pending",
                                "uninstall_pending",
                            )
                            and node.config_state != "apply_pending"
                        )
                    bad, observed = (
                        observation(state.type, metrics, collector_time, now.timestamp())
                        if usable
                        else (None, None)
                    )
                    transition = advance(state, bad, observed, now, RULES[state.type])
                    if transition:
                        result, incident = transition
                        db.add(
                            AuditEvent(
                                actor_type="system",
                                action="alert." + state.type.lower(),
                                target_type="server",
                                target_id=node.id,
                                request_id=str(incident),
                                result=result,
                                created_at=now,
                            )
                        )
