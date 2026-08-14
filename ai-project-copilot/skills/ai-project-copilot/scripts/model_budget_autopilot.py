#!/usr/bin/env python3
"""Route model requests through a user-controlled cost portfolio.

Model Budget Autopilot is an application-owned, provider-neutral control plane.
It does not call a model or alter provider quotas.  It atomically records an
immutable routing decision, reserves estimated cost, settles real response
usage, and recommends at most one quality-driven upgrade.

Money is stored as integer nano-USD (10^-9 USD).  Prices are explicit reviewed
configuration rather than a hard-coded, time-sensitive model catalog.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import random
import sqlite3
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from decimal import Decimal, DecimalException, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TypeVar


MAX_JSON_BYTES = 10 * 1024 * 1024
SQLITE_MAX_INT = 2**63 - 1
DEFAULT_RESERVATION_TTL_SECONDS = 3600
MAX_RESERVATION_TTL_SECONDS = 30 * 24 * 60 * 60
WINDOWS = ("daily", "monthly", "lifetime")
TASK_CLASSES = (
    "routine", "coding", "analysis", "security", "release", "migration",
    "deployment", "permissions", "final-gate",
)
DEFAULT_PROTECTED_TASKS = (
    "security", "release", "migration", "deployment", "permissions", "final-gate",
)
RESPONSE_STATUSES = ("completed", "incomplete", "failed")
QUALITY_GATES = ("pass", "fail")
AUTOPILOT_STATES = ("normal", "fallback")
POLICY_VERSION = "model-budget-autopilot-v2"
SCHEMA_VERSION = 2
SQLITE_TRANSACTION_RETRY_MAX_SECONDS = 60.0
SQLITE_BUSY_RETRY_BASE_SECONDS = 0.01
SQLITE_BUSY_RETRY_MAX_SECONDS = 0.25
_T = TypeVar("_T")

SCHEMA_COLUMN_TYPES = {
    "budget_users": {
        "user_hash": "TEXT",
        "period_budget_nano_usd": "INTEGER",
        "preferred_max_percent": "INTEGER",
        "restore_percent": "INTEGER",
        "startup_allowance_percent": "INTEGER",
        "models_json": "TEXT",
        "prices_json": "TEXT",
        "protected_tasks_json": "TEXT",
        "window": "TEXT",
        "state": "TEXT",
        "state_cycle": "TEXT",
        "config_version": "INTEGER",
        "updated_at": "TEXT",
    },
    "route_decisions": {
        "user_hash": "TEXT",
        "request_hash": "TEXT",
        "logical_hash": "TEXT",
        "request_payload_sha256": "TEXT",
        "parent_request_hash": "TEXT",
        "attempt_number": "INTEGER",
        "cycle": "TEXT",
        "requested_model": "TEXT",
        "selected_model": "TEXT",
        "task_class": "TEXT",
        "protected_task": "INTEGER",
        "action": "TEXT",
        "reason": "TEXT",
        "projected_input_tokens": "INTEGER",
        "projected_cached_tokens": "INTEGER",
        "projected_cache_write_tokens": "INTEGER",
        "projected_output_tokens": "INTEGER",
        "projected_extra_cost_nano_usd": "INTEGER",
        "conservative_input_projection": "INTEGER",
        "projected_cost_nano_usd": "INTEGER",
        "requested_model_cost_nano_usd": "TEXT",
        "accounted_before_nano_usd": "TEXT",
        "preferred_accounted_before_nano_usd": "TEXT",
        "preferred_cap_nano_usd": "INTEGER",
        "period_budget_nano_usd": "INTEGER",
        "state_before": "TEXT",
        "state_after": "TEXT",
        "models_snapshot_json": "TEXT",
        "prices_snapshot_json": "TEXT",
        "config_version": "INTEGER",
        "policy_version": "TEXT",
        "fingerprint": "TEXT",
        "created_at": "TEXT",
        "expires_at": "INTEGER",
        "user_notice": "INTEGER",
    },
    "reservations": {
        "user_hash": "TEXT",
        "request_hash": "TEXT",
        "cycle": "TEXT",
        "model": "TEXT",
        "projected_cost_nano_usd": "INTEGER",
        "status": "TEXT",
        "expires_at": "INTEGER",
        "updated_at": "TEXT",
    },
    "usage_events": {
        "user_hash": "TEXT",
        "request_hash": "TEXT",
        "provider_request_hash": "TEXT",
        "logical_hash": "TEXT",
        "cycle": "TEXT",
        "model": "TEXT",
        "response_status": "TEXT",
        "input_tokens": "INTEGER",
        "cached_tokens": "INTEGER",
        "cache_write_tokens": "INTEGER",
        "reasoning_tokens": "INTEGER",
        "output_tokens": "INTEGER",
        "total_tokens": "INTEGER",
        "estimated_cost_nano_usd": "TEXT",
        "actual_cost_nano_usd": "TEXT",
        "reservation_variance_nano_usd": "TEXT",
        "estimated_savings_nano_usd": "TEXT",
        "over_period_budget": "INTEGER",
        "late_settlement": "INTEGER",
        "fingerprint": "TEXT",
        "recorded_at": "TEXT",
    },
    "quality_events": {
        "user_hash": "TEXT",
        "request_hash": "TEXT",
        "gate": "TEXT",
        "effective_quality": "TEXT",
        "reason": "TEXT",
        "upgrade_recommended": "INTEGER",
        "next_model": "TEXT",
        "fingerprint": "TEXT",
        "recorded_at": "TEXT",
    },
}

SCHEMA_UNIQUE_KEYS = {
    "budget_users": {("user_hash",)},
    "route_decisions": {
        ("user_hash", "request_hash"),
        ("user_hash", "logical_hash", "attempt_number"),
    },
    "reservations": {("user_hash", "request_hash")},
    "usage_events": {
        ("user_hash", "request_hash"),
        ("user_hash", "provider_request_hash"),
    },
    "quality_events": {("user_hash", "request_hash")},
}

SCHEMA_NULLABLE_COLUMNS = {
    "budget_users": set(),
    "route_decisions": {
        "parent_request_hash",
        "selected_model",
        "projected_cached_tokens",
        "projected_cache_write_tokens",
        "projected_cost_nano_usd",
        "expires_at",
    },
    "reservations": set(),
    "usage_events": set(),
    "quality_events": {"next_model"},
}

SCHEMA_PRIMARY_KEYS = {
    "budget_users": ("user_hash",),
    "route_decisions": ("user_hash", "request_hash"),
    "reservations": ("user_hash", "request_hash"),
    "usage_events": ("user_hash", "request_hash"),
    "quality_events": ("user_hash", "request_hash"),
}

SCHEMA_FOREIGN_KEYS = {
    "budget_users": set(),
    "route_decisions": set(),
    "reservations": {
        ("route_decisions", "user_hash", "user_hash"),
        ("route_decisions", "request_hash", "request_hash"),
    },
    "usage_events": {
        ("route_decisions", "user_hash", "user_hash"),
        ("route_decisions", "request_hash", "request_hash"),
    },
    "quality_events": {
        ("route_decisions", "user_hash", "user_hash"),
        ("route_decisions", "request_hash", "request_hash"),
    },
}

SCHEMA_CHECK_FRAGMENTS = {
    "budget_users": {
        "check (period_budget_nano_usd > 0)",
        "check (preferred_max_percent between 1 and 99)",
        "check (restore_percent between 0 and 98)",
        "check (startup_allowance_percent between 0 and 99)",
        "check (window in ('daily', 'monthly', 'lifetime'))",
        "check (state in ('normal', 'fallback'))",
        "check (config_version > 0)",
    },
    "route_decisions": {"check (attempt_number in (1, 2))"},
    "reservations": {
        "check (projected_cost_nano_usd >= 0)",
        "check (status in ('active', 'settled', 'released', 'expired'))",
    },
    "usage_events": {
        "check (response_status in ('completed', 'incomplete', 'failed'))",
    },
    "quality_events": {
        "check (gate in ('pass', 'fail'))",
        "check (effective_quality in ('pass', 'fail'))",
    },
}


@dataclass(frozen=True)
class PriceCard:
    model: str
    input_nano_usd_per_token: int
    cached_input_nano_usd_per_token: int
    cache_write_nano_usd_per_token: int
    output_nano_usd_per_token: int

    def estimate(
        self,
        input_tokens: int,
        output_tokens: int,
        *,
        cached_tokens: int = 0,
        cache_write_tokens: int = 0,
        extra_cost_nano_usd: int = 0,
    ) -> int:
        return _sqlite_int(
            self.estimate_unbounded(
                input_tokens,
                output_tokens,
                cached_tokens=cached_tokens,
                cache_write_tokens=cache_write_tokens,
                extra_cost_nano_usd=extra_cost_nano_usd,
            ),
            "estimated cost",
        )

    def estimate_unbounded(
        self,
        input_tokens: int,
        output_tokens: int,
        *,
        cached_tokens: int = 0,
        cache_write_tokens: int = 0,
        extra_cost_nano_usd: int = 0,
    ) -> int:
        """Return an exact Python integer before any SQLite storage limit."""
        input_tokens = _nonnegative_int(input_tokens, "input_tokens")
        output_tokens = _nonnegative_int(output_tokens, "output_tokens")
        cached_tokens = _nonnegative_int(cached_tokens, "cached_tokens")
        cache_write_tokens = _nonnegative_int(cache_write_tokens, "cache_write_tokens")
        extra = _nonnegative_int(extra_cost_nano_usd, "extra_cost_nano_usd")
        if cached_tokens + cache_write_tokens > input_tokens:
            raise ValueError("cached_tokens + cache_write_tokens cannot exceed input_tokens")
        ordinary = input_tokens - cached_tokens - cache_write_tokens
        total = (
            ordinary * self.input_nano_usd_per_token
            + cached_tokens * self.cached_input_nano_usd_per_token
            + cache_write_tokens * self.cache_write_nano_usd_per_token
            + output_tokens * self.output_nano_usd_per_token
            + extra
        )
        return total


@dataclass(frozen=True)
class BudgetConfig:
    period_budget_nano_usd: int
    preferred_max_percent: int
    restore_percent: int
    startup_allowance_percent: int
    preferred_cap_nano_usd: int
    startup_allowance_nano_usd: int
    models: list[str]
    prices: list[PriceCard]
    protected_tasks: list[str]
    window: str
    cycle: str
    state: str
    config_version: int


@dataclass(frozen=True)
class BudgetStatus:
    period_budget_nano_usd: int
    period_budget_usd: str
    preferred_cap_nano_usd: int
    committed_nano_usd: int
    reserved_nano_usd: int
    accounted_nano_usd: int
    preferred_committed_nano_usd: int
    preferred_reserved_nano_usd: int
    preferred_accounted_nano_usd: int
    actual_preferred_share_percent: float | None
    remaining_period_nano_usd: int
    remaining_preferred_nano_usd: int
    over_period_budget: bool
    over_preferred_allocation: bool
    state: str
    window: str
    cycle: str
    config_version: int


@dataclass(frozen=True)
class RouteDecision:
    request_hash: str
    logical_request_hash: str
    request_payload_sha256: str
    attempt_number: int
    action: str
    requested_model: str
    selected_model: str | None
    task_class: str
    protected_task: bool
    reason: str
    projected_input_tokens: int
    projected_cached_tokens: int | None
    projected_cache_write_tokens: int | None
    projected_output_tokens: int
    projected_extra_cost_nano_usd: int
    conservative_input_projection: bool
    projected_cost_nano_usd: int | None
    projected_cost_usd: str | None
    requested_model_cost_nano_usd: int
    accounted_before_nano_usd: int
    preferred_accounted_before_nano_usd: int
    preferred_cap_nano_usd: int
    period_budget_nano_usd: int
    state_before: str
    state_after: str
    cycle: str
    config_version: int
    reservation_created: bool
    reservation_status: str | None
    reservation_expires_at: str | None
    user_notice_required: bool
    execution_authorized: bool


@dataclass(frozen=True)
class UsageRecord:
    recorded: bool
    request_hash: str
    model: str
    response_status: str
    input_tokens: int
    cached_tokens: int
    cache_write_tokens: int
    reasoning_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost_nano_usd: int
    actual_cost_nano_usd: int
    actual_cost_usd: str
    reservation_variance_nano_usd: int
    reservation_overrun: bool
    late_settlement: bool
    estimated_savings_vs_requested_nano_usd: int
    over_period_budget: bool
    cycle: str


@dataclass(frozen=True)
class QualityDecision:
    recorded: bool
    request_hash: str
    effective_quality: str
    response_status: str
    gate: str
    reason: str
    final_model: str | None
    upgrade_recommended: bool
    next_model: str | None
    automatic_upgrade_authorized: bool


@dataclass(frozen=True)
class FinalResult:
    logical_request_hash: str
    final_status: str
    final_model: str | None
    attempts: list[dict[str, Any]]
    total_input_tokens: int
    total_cached_tokens: int
    total_cache_write_tokens: int
    total_output_tokens: int
    total_reasoning_tokens: int
    total_tokens: int
    total_cost_nano_usd: int
    total_cost_usd: str
    estimated_cost_savings_nano_usd: int
    token_savings: None


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    text = value.strip()
    if len(text) > 256:
        raise ValueError(f"{label} must be at most 256 characters")
    return text


def _nonnegative_int(value: object, label: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > SQLITE_MAX_INT
    ):
        raise ValueError(f"{label} must be an integer between 0 and {SQLITE_MAX_INT}")
    return value


def _positive_int(value: object, label: str) -> int:
    result = _nonnegative_int(value, label)
    if result == 0:
        raise ValueError(f"{label} must be a positive integer")
    return result


def _sqlite_int(value: int, label: str) -> int:
    if value < 0 or value > SQLITE_MAX_INT:
        raise ValueError(f"{label} exceeds the SQLite integer range")
    return value


def _payload_sha256(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("request_payload_sha256 must be a 64-character hexadecimal SHA-256")
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise ValueError("request_payload_sha256 must be a 64-character hexadecimal SHA-256")
    return normalized


def _percent(value: object, label: str, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer percentage")
    minimum = 0 if allow_zero else 1
    if not minimum <= value <= 99:
        raise ValueError(f"{label} must be between {minimum} and 99")
    return value


def _utc(value: dt.datetime | None = None) -> dt.datetime:
    current = value or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return current.astimezone(dt.timezone.utc)


def _epoch_seconds_floor(value: dt.datetime) -> int:
    """Convert UTC time to a whole epoch second without float rounding."""
    current = _utc(value)
    epoch = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
    delta = current - epoch
    return delta.days * 86_400 + delta.seconds


def _epoch_seconds_ceiling(value: dt.datetime) -> int:
    """Persist a deadline that is never earlier than the requested instant."""
    current = _utc(value)
    whole = _epoch_seconds_floor(current)
    return whole + int(current.microsecond != 0)


def _datetime_from_epoch_seconds(value: int) -> dt.datetime:
    epoch = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
    return epoch + dt.timedelta(seconds=value)


def _hash_identifier(value: str, namespace: str) -> str:
    return hashlib.sha256(f"aipc:{namespace}:\0{value}".encode("utf-8")).hexdigest()


def _fingerprint(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _usd(nano_usd: int) -> str:
    sign = "-" if nano_usd < 0 else ""
    whole, fractional = divmod(abs(nano_usd), 1_000_000_000)
    return f"{sign}{whole}.{fractional:09d}"


def _parse_usd_to_nano(value: str, label: str) -> int:
    try:
        amount = Decimal(value)
        if not amount.is_finite() or amount <= 0:
            raise ValueError(f"{label} must be a positive finite decimal USD amount")
        nano = amount * Decimal(1_000_000_000)
    except (DecimalException, InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} must be a decimal USD amount") from exc
    if nano != nano.to_integral_value():
        raise ValueError(f"{label} must have at most 9 decimal places")
    return _positive_int(int(nano), label)


def _parse_rate(value: str, label: str) -> int:
    """Convert USD per one million tokens to integer nano-USD per token."""
    try:
        rate = Decimal(value)
        if not rate.is_finite() or rate < 0:
            raise ValueError(f"{label} must be a non-negative finite decimal rate")
        per_token_nano = rate * Decimal(1000)
    except (DecimalException, InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} must be a decimal USD-per-million-token rate") from exc
    if per_token_nano != per_token_nano.to_integral_value():
        raise ValueError(f"{label} must resolve to a non-negative integer nano-USD per token")
    return _nonnegative_int(int(per_token_nano), label)


def _models(values: Sequence[str]) -> list[str]:
    if isinstance(values, (str, bytes)):
        raise ValueError("model ladder must be a sequence of model names")
    result = [_identifier(value, "model") for value in values]
    if len(result) < 2:
        raise ValueError("model ladder must contain at least two reviewed models")
    if len(set(result)) != len(result):
        raise ValueError("model ladder cannot contain duplicates")
    return result


def _price_cards(models: Sequence[str], values: Sequence[PriceCard]) -> list[PriceCard]:
    cards: list[PriceCard] = []
    seen: set[str] = set()
    for card in values:
        if not isinstance(card, PriceCard):
            raise ValueError("prices must contain PriceCard values")
        model = _identifier(card.model, "price model")
        if model in seen:
            raise ValueError(f"duplicate price card: {model}")
        for field in (
            card.input_nano_usd_per_token,
            card.cached_input_nano_usd_per_token,
            card.cache_write_nano_usd_per_token,
            card.output_nano_usd_per_token,
        ):
            _nonnegative_int(field, f"price for {model}")
        seen.add(model)
        cards.append(card)
    if seen != set(models):
        missing = sorted(set(models) - seen)
        extra = sorted(seen - set(models))
        raise ValueError(f"price cards must exactly match model ladder; missing={missing}, extra={extra}")
    by_name = {card.model: card for card in cards}
    return [by_name[model] for model in models]


def _protected_tasks(values: Sequence[str]) -> list[str]:
    if isinstance(values, (str, bytes)):
        raise ValueError("protected tasks must be a sequence of task classes")
    result = [_identifier(value, "protected task class") for value in values]
    invalid = sorted(set(result) - set(TASK_CLASSES))
    if invalid:
        raise ValueError(f"unknown protected task class(es): {', '.join(invalid)}")
    return sorted(set(result))


def cycle_key(window: str, now: dt.datetime | None = None) -> str:
    if window not in WINDOWS:
        raise ValueError(f"window must be one of: {', '.join(WINDOWS)}")
    current = _utc(now)
    if window == "daily":
        return current.strftime("%Y-%m-%d")
    if window == "monthly":
        return current.strftime("%Y-%m")
    return "lifetime"


def _cap(budget: int, percent: int) -> int:
    return max(1, budget * percent // 100)


def _cards_json(cards: Sequence[PriceCard]) -> str:
    return json.dumps([asdict(card) for card in cards], sort_keys=True, separators=(",", ":"))


def _cards_from_json(value: str) -> list[PriceCard]:
    try:
        raw = json.loads(value)
        return [PriceCard(**item) for item in raw]
    except (json.JSONDecodeError, TypeError, KeyError) as exc:
        raise ValueError("stored price cards are invalid") from exc


def _schema_unique_keys(
    connection: sqlite3.Connection, table: str
) -> set[tuple[str, ...]]:
    keys: set[tuple[str, ...]] = set()
    for row in connection.execute(f'PRAGMA index_list("{table}")').fetchall():
        if not bool(row["unique"]) or bool(row["partial"]):
            continue
        columns = tuple(
            str(item["name"])
            for item in connection.execute(
                "SELECT name FROM pragma_index_info(?) ORDER BY seqno",
                (str(row["name"]),),
            ).fetchall()
        )
        keys.add(columns)
    return keys


def _normalized_schema_sql(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def _validate_schema_contract(connection: sqlite3.Connection) -> None:
    issues: list[str] = []
    objects = connection.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE type IN ('table', 'view')"
    ).fetchall()
    by_normalized_name: dict[str, list[sqlite3.Row]] = {}
    for row in objects:
        by_normalized_name.setdefault(str(row["name"]).casefold(), []).append(row)
    for table, expected_columns in SCHEMA_COLUMN_TYPES.items():
        matching_objects = by_normalized_name.get(table.casefold(), [])
        if (
            len(matching_objects) != 1
            or str(matching_objects[0]["type"]) != "table"
            or str(matching_objects[0]["name"]) != table
        ):
            issues.append(f"{table} object")
            continue
        rows = connection.execute(f'PRAGMA table_xinfo("{table}")').fetchall()
        actual_columns = {
            str(row["name"]): str(row["type"]).upper() for row in rows
        }
        if actual_columns != expected_columns:
            issues.append(f"{table} columns/types")
            continue
        actual_not_null = {
            str(row["name"]) for row in rows if bool(row["notnull"])
        }
        expected_not_null = set(expected_columns) - SCHEMA_NULLABLE_COLUMNS[table]
        if actual_not_null != expected_not_null:
            issues.append(f"{table} nullability")
        primary_key = tuple(
            str(row["name"])
            for row in sorted(rows, key=lambda item: int(item["pk"]))
            if int(row["pk"]) > 0
        )
        if primary_key != SCHEMA_PRIMARY_KEYS[table]:
            issues.append(f"{table} primary key")
        if _schema_unique_keys(connection, table) != SCHEMA_UNIQUE_KEYS[table]:
            issues.append(f"{table} unique keys")
        foreign_keys = {
            (str(row["table"]), str(row["from"]), str(row["to"]))
            for row in connection.execute(
                f'PRAGMA foreign_key_list("{table}")'
            ).fetchall()
        }
        if foreign_keys != SCHEMA_FOREIGN_KEYS[table]:
            issues.append(f"{table} foreign keys")
        schema_sql = _normalized_schema_sql(matching_objects[0]["sql"])
        if not all(
            fragment in schema_sql for fragment in SCHEMA_CHECK_FRAGMENTS[table]
        ):
            issues.append(f"{table} check constraints")
    placeholders = ",".join("?" for _ in SCHEMA_COLUMN_TYPES)
    triggers = connection.execute(
        f"SELECT name FROM sqlite_master "
        f"WHERE type='trigger' AND lower(tbl_name) IN ({placeholders})",
        tuple(table.casefold() for table in SCHEMA_COLUMN_TYPES),
    ).fetchall()
    if triggers:
        issues.append("unexpected triggers")
    if issues:
        raise ValueError(
            "incompatible Model Budget Autopilot database schema; create a new "
            f"state database ({', '.join(issues)})"
        )


def _preflight_schema(connection: sqlite3.Connection) -> None:
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version > SCHEMA_VERSION:
        raise ValueError(
            f"database schema version {version} is newer than supported version {SCHEMA_VERSION}"
        )
    existing_objects = {
        str(row[0]).casefold()
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        ).fetchall()
    }
    expected_names = {table.casefold() for table in SCHEMA_COLUMN_TYPES}
    if version == SCHEMA_VERSION or existing_objects.intersection(expected_names):
        _validate_schema_contract(connection)


def _ensure_schema_version(connection: sqlite3.Connection) -> None:
    _validate_schema_contract(connection)
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version > SCHEMA_VERSION:
        raise ValueError(
            f"database schema version {version} is newer than supported version {SCHEMA_VERSION}"
        )
    if version < SCHEMA_VERSION:
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def _sql_statements(script: str) -> list[str]:
    statements: list[str] = []
    pending = ""
    for line in script.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            statement = pending.strip()
            if statement:
                statements.append(statement)
            pending = ""
    if pending.strip():
        raise ValueError("internal SQLite schema script is incomplete")
    return statements


def _harden_database_files(path: Path) -> None:
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        if candidate.exists():
            try:
                os.chmod(candidate, 0o600)
            except OSError:
                pass


def _is_transient_sqlite_lock(error: sqlite3.OperationalError) -> bool:
    code = getattr(error, "sqlite_errorcode", None)
    if isinstance(code, int):
        return (code & 0xFF) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
    message = str(error).casefold()
    return any(
        marker in message
        for marker in (
            "database is locked",
            "database table is locked",
            "database schema is locked",
            "database is busy",
        )
    )


def _with_sqlite_lock_retry(
    operation: Callable[[], _T],
    rollback: Callable[[], None],
) -> _T:
    """Run one pre-provider SQLite operation within a bounded lock-wait budget."""
    deadline = time.monotonic() + SQLITE_TRANSACTION_RETRY_MAX_SECONDS
    delay = SQLITE_BUSY_RETRY_BASE_SECONDS
    while True:
        try:
            return operation()
        except sqlite3.OperationalError as exc:
            if not _is_transient_sqlite_lock(exc):
                raise
            rollback()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(remaining, random.uniform(delay / 2, delay)))
            delay = min(SQLITE_BUSY_RETRY_MAX_SECONDS, delay * 2)


def _begin_immediate(connection: sqlite3.Connection) -> None:
    """Acquire the SQLite writer lock with bounded retry under transient contention."""
    _with_sqlite_lock_retry(
        lambda: connection.execute("BEGIN IMMEDIATE"),
        connection.rollback,
    )
    _preflight_schema(connection)
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version != SCHEMA_VERSION:
        raise ValueError(
            f"database schema version changed to {version}; expected {SCHEMA_VERSION}"
        )


def _require_wal(connection: sqlite3.Connection) -> None:
    journal_mode = str(
        connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    ).casefold()
    if journal_mode != "wal":
        raise ValueError(f"SQLite WAL mode is required; got {journal_mode}")


def _connect(path: Path) -> sqlite3.Connection:
    path = path.expanduser()
    if path.is_symlink():
        raise ValueError(f"refusing symlink database: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    if existed and not path.is_file():
        raise ValueError(f"database path must be a regular file: {path}")
    if not existed:
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError:
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"database path must be a regular non-symlink file: {path}")
        else:
            os.close(descriptor)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    connection = sqlite3.connect(path, timeout=0.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=0")
    connection.execute("PRAGMA foreign_keys=ON")

    def initialize_schema() -> None:
        schema_sql = """
        CREATE TABLE IF NOT EXISTS budget_users (
          user_hash TEXT PRIMARY KEY NOT NULL,
          period_budget_nano_usd INTEGER NOT NULL CHECK (period_budget_nano_usd > 0),
          preferred_max_percent INTEGER NOT NULL CHECK (preferred_max_percent BETWEEN 1 AND 99),
          restore_percent INTEGER NOT NULL CHECK (restore_percent BETWEEN 0 AND 98),
          startup_allowance_percent INTEGER NOT NULL CHECK (startup_allowance_percent BETWEEN 0 AND 99),
          models_json TEXT NOT NULL,
          prices_json TEXT NOT NULL,
          protected_tasks_json TEXT NOT NULL,
          window TEXT NOT NULL CHECK (window IN ('daily', 'monthly', 'lifetime')),
          state TEXT NOT NULL CHECK (state IN ('normal', 'fallback')),
          state_cycle TEXT NOT NULL,
          config_version INTEGER NOT NULL CHECK (config_version > 0),
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS route_decisions (
          user_hash TEXT NOT NULL,
          request_hash TEXT NOT NULL,
          logical_hash TEXT NOT NULL,
          request_payload_sha256 TEXT NOT NULL,
          parent_request_hash TEXT,
          attempt_number INTEGER NOT NULL CHECK (attempt_number IN (1, 2)),
          cycle TEXT NOT NULL,
          requested_model TEXT NOT NULL,
          selected_model TEXT,
          task_class TEXT NOT NULL,
          protected_task INTEGER NOT NULL,
          action TEXT NOT NULL,
          reason TEXT NOT NULL,
          projected_input_tokens INTEGER NOT NULL,
          projected_cached_tokens INTEGER,
          projected_cache_write_tokens INTEGER,
          projected_output_tokens INTEGER NOT NULL,
          projected_extra_cost_nano_usd INTEGER NOT NULL,
          conservative_input_projection INTEGER NOT NULL,
          projected_cost_nano_usd INTEGER,
          requested_model_cost_nano_usd TEXT NOT NULL,
          accounted_before_nano_usd TEXT NOT NULL,
          preferred_accounted_before_nano_usd TEXT NOT NULL,
          preferred_cap_nano_usd INTEGER NOT NULL,
          period_budget_nano_usd INTEGER NOT NULL,
          state_before TEXT NOT NULL,
          state_after TEXT NOT NULL,
          models_snapshot_json TEXT NOT NULL,
          prices_snapshot_json TEXT NOT NULL,
          config_version INTEGER NOT NULL,
          policy_version TEXT NOT NULL,
          fingerprint TEXT NOT NULL,
          created_at TEXT NOT NULL,
          expires_at INTEGER,
          user_notice INTEGER NOT NULL,
          PRIMARY KEY (user_hash, request_hash),
          UNIQUE (user_hash, logical_hash, attempt_number)
        );
        CREATE INDEX IF NOT EXISTS route_cycle_idx ON route_decisions (user_hash, cycle);
        CREATE INDEX IF NOT EXISTS route_logical_idx ON route_decisions (user_hash, logical_hash, attempt_number);

        CREATE TABLE IF NOT EXISTS reservations (
          user_hash TEXT NOT NULL,
          request_hash TEXT NOT NULL,
          cycle TEXT NOT NULL,
          model TEXT NOT NULL,
          projected_cost_nano_usd INTEGER NOT NULL CHECK (projected_cost_nano_usd >= 0),
          status TEXT NOT NULL CHECK (status IN ('active', 'settled', 'released', 'expired')),
          expires_at INTEGER NOT NULL,
          updated_at TEXT NOT NULL,
          PRIMARY KEY (user_hash, request_hash),
          FOREIGN KEY (user_hash, request_hash)
            REFERENCES route_decisions (user_hash, request_hash)
        );
        CREATE INDEX IF NOT EXISTS reservation_cycle_idx
          ON reservations (user_hash, cycle, status);

        CREATE TABLE IF NOT EXISTS usage_events (
          user_hash TEXT NOT NULL,
          request_hash TEXT NOT NULL,
          provider_request_hash TEXT NOT NULL,
          logical_hash TEXT NOT NULL,
          cycle TEXT NOT NULL,
          model TEXT NOT NULL,
          response_status TEXT NOT NULL CHECK (response_status IN ('completed', 'incomplete', 'failed')),
          input_tokens INTEGER NOT NULL,
          cached_tokens INTEGER NOT NULL,
          cache_write_tokens INTEGER NOT NULL,
          reasoning_tokens INTEGER NOT NULL,
          output_tokens INTEGER NOT NULL,
          total_tokens INTEGER NOT NULL,
          estimated_cost_nano_usd TEXT NOT NULL,
          actual_cost_nano_usd TEXT NOT NULL,
          reservation_variance_nano_usd TEXT NOT NULL,
          estimated_savings_nano_usd TEXT NOT NULL,
          over_period_budget INTEGER NOT NULL,
          late_settlement INTEGER NOT NULL,
          fingerprint TEXT NOT NULL,
          recorded_at TEXT NOT NULL,
          PRIMARY KEY (user_hash, request_hash),
          UNIQUE (user_hash, provider_request_hash),
          FOREIGN KEY (user_hash, request_hash)
            REFERENCES route_decisions (user_hash, request_hash)
        );
        CREATE INDEX IF NOT EXISTS usage_cycle_idx ON usage_events (user_hash, cycle);

        CREATE TABLE IF NOT EXISTS quality_events (
          user_hash TEXT NOT NULL,
          request_hash TEXT NOT NULL,
          gate TEXT NOT NULL CHECK (gate IN ('pass', 'fail')),
          effective_quality TEXT NOT NULL CHECK (effective_quality IN ('pass', 'fail')),
          reason TEXT NOT NULL,
          upgrade_recommended INTEGER NOT NULL,
          next_model TEXT,
          fingerprint TEXT NOT NULL,
          recorded_at TEXT NOT NULL,
          PRIMARY KEY (user_hash, request_hash),
          FOREIGN KEY (user_hash, request_hash)
            REFERENCES route_decisions (user_hash, request_hash)
        );
        """
        _require_wal(connection)
        connection.execute("BEGIN IMMEDIATE")
        _preflight_schema(connection)
        locked_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if locked_version == SCHEMA_VERSION:
            connection.commit()
            return
        for statement in _sql_statements(schema_sql):
            connection.execute(statement)
        _ensure_schema_version(connection)
        connection.commit()

    def prepare_database() -> None:
        _preflight_schema(connection)
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version == SCHEMA_VERSION:
            _require_wal(connection)
        else:
            initialize_schema()

    try:
        _with_sqlite_lock_retry(prepare_database, connection.rollback)
    except Exception:
        connection.rollback()
        connection.close()
        raise
    _harden_database_files(path)
    connection.execute("PRAGMA busy_timeout=0")
    return connection


def _expire_reservations(connection: sqlite3.Connection, now: dt.datetime) -> int:
    cursor = connection.execute(
        """
        UPDATE reservations SET status='expired', updated_at=?
        WHERE status='active' AND expires_at <= ?
        """,
        (now.isoformat(), _epoch_seconds_floor(now)),
    )
    return cursor.rowcount


def _load_config(connection: sqlite3.Connection, user_hash: str, now: dt.datetime) -> BudgetConfig:
    row = connection.execute("SELECT * FROM budget_users WHERE user_hash=?", (user_hash,)).fetchone()
    if row is None:
        raise ValueError("user model budget is not configured")
    try:
        models = _models(json.loads(row["models_json"]))
        cards = _price_cards(models, _cards_from_json(row["prices_json"]))
        protected = _protected_tasks(json.loads(row["protected_tasks_json"]))
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("stored model budget configuration is invalid") from exc
    budget = int(row["period_budget_nano_usd"])
    maximum = int(row["preferred_max_percent"])
    startup = int(row["startup_allowance_percent"])
    window = str(row["window"])
    cycle = cycle_key(window, now)
    state = str(row["state"]) if str(row["state_cycle"]) == cycle else "normal"
    return BudgetConfig(
        budget,
        maximum,
        int(row["restore_percent"]),
        startup,
        _cap(budget, maximum),
        budget * startup // 100,
        models,
        cards,
        protected,
        window,
        cycle,
        state,
        int(row["config_version"]),
    )


def configure_user(
    db: Path,
    user_key: str,
    period_budget_nano_usd: int,
    preferred_max_percent: int,
    restore_percent: int,
    startup_allowance_percent: int,
    models: Sequence[str],
    prices: Sequence[PriceCard],
    *,
    protected_tasks: Sequence[str] = DEFAULT_PROTECTED_TASKS,
    window: str = "monthly",
    now: dt.datetime | None = None,
) -> BudgetConfig:
    user_hash = _hash_identifier(_identifier(user_key, "user_key"), "user")
    budget = _positive_int(period_budget_nano_usd, "period_budget_nano_usd")
    maximum = _percent(preferred_max_percent, "preferred_max_percent")
    restore = _percent(restore_percent, "restore_percent", allow_zero=True)
    startup = _percent(startup_allowance_percent, "startup_allowance_percent", allow_zero=True)
    if restore >= maximum:
        raise ValueError("restore_percent must be lower than preferred_max_percent")
    if startup > maximum:
        raise ValueError("startup_allowance_percent cannot exceed preferred_max_percent")
    ladder = _models(models)
    cards = _price_cards(ladder, prices)
    protected = _protected_tasks(protected_tasks)
    current = _utc(now)
    cycle = cycle_key(window, current)
    connection = _connect(db)
    try:
        _begin_immediate(connection)
        _expire_reservations(connection, current)
        if connection.execute(
            "SELECT 1 FROM reservations WHERE user_hash=? "
            "AND status NOT IN ('settled', 'released', 'expired') LIMIT 1",
            (user_hash,),
        ).fetchone():
            raise ValueError(
                "cannot reconfigure a user while reservations are active or nonterminal"
            )
        existing = connection.execute(
            "SELECT config_version FROM budget_users WHERE user_hash=?", (user_hash,)
        ).fetchone()
        version = (int(existing[0]) + 1) if existing else 1
        connection.execute(
            """
            INSERT INTO budget_users
              (user_hash, period_budget_nano_usd, preferred_max_percent, restore_percent,
               startup_allowance_percent, models_json, prices_json, protected_tasks_json,
               window, state, state_cycle, config_version, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'normal', ?, ?, ?)
            ON CONFLICT(user_hash) DO UPDATE SET
              period_budget_nano_usd=excluded.period_budget_nano_usd,
              preferred_max_percent=excluded.preferred_max_percent,
              restore_percent=excluded.restore_percent,
              startup_allowance_percent=excluded.startup_allowance_percent,
              models_json=excluded.models_json,
              prices_json=excluded.prices_json,
              protected_tasks_json=excluded.protected_tasks_json,
              window=excluded.window,
              state='normal',
              state_cycle=excluded.state_cycle,
              config_version=excluded.config_version,
              updated_at=excluded.updated_at
            """,
            (
                user_hash, budget, maximum, restore, startup,
                json.dumps(ladder, separators=(",", ":")), _cards_json(cards),
                json.dumps(protected, separators=(",", ":")), window, cycle, version,
                current.isoformat(),
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return BudgetConfig(
        budget, maximum, restore, startup, _cap(budget, maximum),
        budget * startup // 100, ladder, cards, protected, window, cycle, "normal", version,
    )


def _status(connection: sqlite3.Connection, config: BudgetConfig, user_hash: str) -> BudgetStatus:
    preferred = config.models[0]
    usage = connection.execute(
        """
        SELECT model, actual_cost_nano_usd
        FROM usage_events WHERE user_hash=? AND cycle=?
        """,
        (user_hash, config.cycle),
    ).fetchall()
    reservations = connection.execute(
        """
        SELECT model, projected_cost_nano_usd
        FROM reservations WHERE user_hash=? AND cycle=?
          AND status NOT IN ('settled', 'released', 'expired')
        """,
        (user_hash, config.cycle),
    ).fetchall()
    committed = sum(int(row["actual_cost_nano_usd"]) for row in usage)
    preferred_committed = sum(
        int(row["actual_cost_nano_usd"]) for row in usage if str(row["model"]) == preferred
    )
    reserved = sum(int(row["projected_cost_nano_usd"]) for row in reservations)
    preferred_reserved = sum(
        int(row["projected_cost_nano_usd"])
        for row in reservations if str(row["model"]) == preferred
    )
    accounted = committed + reserved
    preferred_accounted = preferred_committed + preferred_reserved
    share = None if accounted == 0 else round(preferred_accounted / accounted * 100, 4)
    return BudgetStatus(
        config.period_budget_nano_usd,
        _usd(config.period_budget_nano_usd),
        config.preferred_cap_nano_usd,
        committed,
        reserved,
        accounted,
        preferred_committed,
        preferred_reserved,
        preferred_accounted,
        share,
        max(0, config.period_budget_nano_usd - accounted),
        max(0, config.preferred_cap_nano_usd - preferred_accounted),
        accounted > config.period_budget_nano_usd,
        preferred_accounted > config.preferred_cap_nano_usd,
        config.state,
        config.window,
        config.cycle,
        config.config_version,
    )


def get_status(db: Path, user_key: str, *, now: dt.datetime | None = None) -> BudgetStatus:
    user_hash = _hash_identifier(_identifier(user_key, "user_key"), "user")
    explicit_now = _utc(now) if now is not None else None
    connection = _connect(db)
    try:
        _begin_immediate(connection)
        current = explicit_now or _utc()
        _expire_reservations(connection, current)
        result = _status(connection, _load_config(connection, user_hash, current), user_hash)
        connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _price_map(cards: Sequence[PriceCard]) -> dict[str, PriceCard]:
    return {card.model: card for card in cards}


def _projected_cost(
    card: PriceCard,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int | None,
    cache_write_tokens: int | None,
    extra_cost_nano_usd: int,
) -> int:
    """Return an exact Python integer so an unusable candidate can be skipped."""
    if cached_tokens is None or cache_write_tokens is None:
        input_rate = max(
            card.input_nano_usd_per_token,
            card.cached_input_nano_usd_per_token,
            card.cache_write_nano_usd_per_token,
        )
        return (
            input_tokens * input_rate
            + output_tokens * card.output_nano_usd_per_token
            + extra_cost_nano_usd
        )
    return card.estimate_unbounded(
        input_tokens,
        output_tokens,
        cached_tokens=cached_tokens,
        cache_write_tokens=cache_write_tokens,
        extra_cost_nano_usd=extra_cost_nano_usd,
    )


def _at_or_below_share(preferred: int, total: int, percent: int) -> bool:
    return total == 0 or preferred * 100 <= total * percent


def _reservation_state(
    connection: sqlite3.Connection, user_hash: str, request_hash: str
) -> tuple[str | None, int | None]:
    row = connection.execute(
        "SELECT status, expires_at FROM reservations WHERE user_hash=? AND request_hash=?",
        (user_hash, request_hash),
    ).fetchone()
    if row is None:
        return None, None
    return str(row["status"]), int(row["expires_at"])


def _decision_from_row(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    reservation_created: bool,
) -> RouteDecision:
    reservation_status, reservation_expiry = _reservation_state(
        connection, str(row["user_hash"]), str(row["request_hash"])
    )
    expiry = (
        _datetime_from_epoch_seconds(reservation_expiry).isoformat()
        if reservation_expiry is not None else None
    )
    selected_cost = row["projected_cost_nano_usd"]
    stored_selected_model = (
        str(row["selected_model"]) if row["selected_model"] is not None else None
    )
    execution_authorized = (
        stored_selected_model is not None
        and reservation_status == "active"
        and reservation_created
    )
    return RouteDecision(
        str(row["request_hash"]), str(row["logical_hash"]),
        str(row["request_payload_sha256"]), int(row["attempt_number"]),
        str(row["action"]), str(row["requested_model"]),
        stored_selected_model if execution_authorized else None,
        str(row["task_class"]), bool(row["protected_task"]), str(row["reason"]),
        int(row["projected_input_tokens"]),
        int(row["projected_cached_tokens"]) if row["projected_cached_tokens"] is not None else None,
        int(row["projected_cache_write_tokens"])
        if row["projected_cache_write_tokens"] is not None else None,
        int(row["projected_output_tokens"]), int(row["projected_extra_cost_nano_usd"]),
        bool(row["conservative_input_projection"]),
        int(selected_cost) if selected_cost is not None else None,
        _usd(int(selected_cost)) if selected_cost is not None else None,
        int(row["requested_model_cost_nano_usd"]), int(row["accounted_before_nano_usd"]),
        int(row["preferred_accounted_before_nano_usd"]), int(row["preferred_cap_nano_usd"]),
        int(row["period_budget_nano_usd"]), str(row["state_before"]), str(row["state_after"]),
        str(row["cycle"]), int(row["config_version"]), reservation_created,
        reservation_status, expiry, bool(row["user_notice"]), execution_authorized,
    )


def route_request(
    db: Path,
    user_key: str,
    request_id: str,
    requested_model: str,
    projected_input_tokens: int,
    projected_output_tokens: int,
    *,
    request_payload_sha256: str,
    projected_cached_tokens: int | None = None,
    projected_cache_write_tokens: int | None = None,
    projected_extra_cost_nano_usd: int = 0,
    task_class: str = "routine",
    logical_request_id: str | None = None,
    parent_request_id: str | None = None,
    reservation_ttl_seconds: int = DEFAULT_RESERVATION_TTL_SECONDS,
    now: dt.datetime | None = None,
) -> RouteDecision:
    """Atomically select a model and reserve its conservative projected cost."""
    user_hash = _hash_identifier(_identifier(user_key, "user_key"), "user")
    request_id = _identifier(request_id, "request_id")
    request_hash = _hash_identifier(request_id, "request")
    requested = _identifier(requested_model, "requested_model")
    payload_hash = _payload_sha256(request_payload_sha256)
    input_tokens = _nonnegative_int(projected_input_tokens, "projected_input_tokens")
    output_tokens = _nonnegative_int(projected_output_tokens, "projected_output_tokens")
    extra_cost = _nonnegative_int(
        projected_extra_cost_nano_usd, "projected_extra_cost_nano_usd"
    )
    if (projected_cached_tokens is None) != (projected_cache_write_tokens is None):
        raise ValueError(
            "projected_cached_tokens and projected_cache_write_tokens must be supplied together"
        )
    cached_tokens = (
        _nonnegative_int(projected_cached_tokens, "projected_cached_tokens")
        if projected_cached_tokens is not None else None
    )
    cache_write_tokens = (
        _nonnegative_int(projected_cache_write_tokens, "projected_cache_write_tokens")
        if projected_cache_write_tokens is not None else None
    )
    if (
        cached_tokens is not None
        and cache_write_tokens is not None
        and cached_tokens + cache_write_tokens > input_tokens
    ):
        raise ValueError(
            "projected cached + cache-write tokens cannot exceed projected input tokens"
        )
    projected_total_tokens = input_tokens + output_tokens
    if projected_total_tokens == 0:
        raise ValueError("projected request must contain at least one token")
    if projected_total_tokens > SQLITE_MAX_INT:
        raise ValueError(
            "projected input + output tokens exceed the SQLite integer range"
        )
    if task_class not in TASK_CLASSES:
        raise ValueError(f"task_class must be one of: {', '.join(TASK_CLASSES)}")
    ttl = _positive_int(reservation_ttl_seconds, "reservation_ttl_seconds")
    if ttl > MAX_RESERVATION_TTL_SECONDS:
        raise ValueError(
            f"reservation_ttl_seconds cannot exceed {MAX_RESERVATION_TTL_SECONDS}"
        )
    explicit_now = _utc(now) if now is not None else None
    supplied_logical_hash = (
        _hash_identifier(_identifier(logical_request_id, "logical_request_id"), "logical")
        if logical_request_id is not None else None
    )
    parent_hash = (
        _hash_identifier(_identifier(parent_request_id, "parent_request_id"), "request")
        if parent_request_id is not None else None
    )
    connection = _connect(db)
    try:
        _begin_immediate(connection)
        current = explicit_now or _utc()
        _expire_reservations(connection, current)

        parent = None
        if parent_hash is not None:
            parent = connection.execute(
                "SELECT * FROM route_decisions WHERE user_hash=? AND request_hash=?",
                (user_hash, parent_hash),
            ).fetchone()
            if parent is None:
                raise ValueError("parent request decision does not exist")
            logical_hash = str(parent["logical_hash"])
            if supplied_logical_hash is not None and supplied_logical_hash != logical_hash:
                raise ValueError("logical_request_id does not match the parent request")
            attempt_number = int(parent["attempt_number"]) + 1
        else:
            logical_hash = supplied_logical_hash or _hash_identifier(request_id, "logical")
            attempt_number = 1

        fingerprint = _fingerprint([
            logical_hash, parent_hash, payload_hash, requested, input_tokens, cached_tokens,
            cache_write_tokens, output_tokens, extra_cost, task_class, ttl,
        ])
        existing = connection.execute(
            "SELECT * FROM route_decisions WHERE user_hash=? AND request_hash=?",
            (user_hash, request_hash),
        ).fetchone()
        if existing is not None:
            if str(existing["fingerprint"]) != fingerprint:
                raise ValueError("request_id already has a different immutable routing decision")
            result = _decision_from_row(connection, existing, reservation_created=False)
            connection.commit()
            return result

        config = _load_config(connection, user_hash, current)
        if parent is not None and (
            int(parent["config_version"]) != config.config_version
            or str(parent["policy_version"]) != POLICY_VERSION
        ):
            raise ValueError("parent quality authorization is stale under the current policy")
        if requested not in config.models:
            raise ValueError("requested model is not in the configured reviewed ladder")
        if attempt_number not in (1, 2):
            raise ValueError("automatic quality upgrade is limited to one additional attempt")
        conflicting_attempt = connection.execute(
            """
            SELECT request_hash FROM route_decisions
            WHERE user_hash=? AND logical_hash=? AND attempt_number=?
            """,
            (user_hash, logical_hash, attempt_number),
        ).fetchone()
        if conflicting_attempt is not None:
            raise ValueError(
                f"logical request already has an immutable decision for attempt {attempt_number}"
            )

        quality_upgrade = parent is not None
        if quality_upgrade:
            if int(parent["attempt_number"]) != 1:
                raise ValueError("automatic quality upgrade is limited to one additional attempt")
            quality = connection.execute(
                "SELECT * FROM quality_events WHERE user_hash=? AND request_hash=?",
                (user_hash, parent_hash),
            ).fetchone()
            if quality is None or not bool(quality["upgrade_recommended"]):
                raise ValueError("parent quality decision does not authorize an upgrade")
            if str(quality["next_model"]) != requested:
                raise ValueError("requested upgrade model does not match the quality decision")

        status = _status(connection, config, user_hash)
        price_by_model = _price_map(config.prices)
        requested_cost = _projected_cost(
            price_by_model[requested], input_tokens, output_tokens, cached_tokens,
            cache_write_tokens, extra_cost,
        )
        state_before = config.state
        state_after = state_before
        protected = task_class in config.protected_tasks
        preferred = config.models[0]

        if state_before == "fallback":
            can_restore = (
                status.preferred_accounted_nano_usd < config.preferred_cap_nano_usd
                and _at_or_below_share(
                    status.preferred_accounted_nano_usd,
                    status.accounted_nano_usd,
                    config.restore_percent,
                )
            )
            if can_restore:
                state_after = "normal"

        soft_downgrade = False
        soft_reasons: list[str] = []
        if requested == preferred and not protected and not quality_upgrade:
            prospective_preferred = status.preferred_accounted_nano_usd + requested_cost
            prospective_total = status.accounted_nano_usd + requested_cost
            exceeds_allocation = prospective_preferred > config.preferred_cap_nano_usd
            exceeds_ratio = (
                status.accounted_nano_usd > 0
                and prospective_preferred > config.startup_allowance_nano_usd
                and prospective_preferred * 100 > prospective_total * config.preferred_max_percent
            )
            if state_after == "fallback":
                soft_downgrade = True
                soft_reasons.append("hysteresis keeps ordinary work on fallback until the restore threshold")
            elif exceeds_allocation or exceeds_ratio:
                soft_downgrade = True
                state_after = "fallback"
                if exceeds_allocation:
                    soft_reasons.append("preferred model fixed period allocation would be exceeded")
                if exceeds_ratio:
                    soft_reasons.append("preferred model projected share exceeds the user limit after startup allowance")

        requested_index = config.models.index(requested)
        if protected or quality_upgrade:
            candidates = [requested]
        elif soft_downgrade:
            candidates = config.models[requested_index + 1:]
        else:
            candidates = config.models[requested_index:]

        selected: str | None = None
        selected_cost: int | None = None
        for candidate in candidates:
            candidate_cost = _projected_cost(
                price_by_model[candidate], input_tokens, output_tokens, cached_tokens,
                cache_write_tokens, extra_cost,
            )
            if candidate_cost > SQLITE_MAX_INT:
                continue
            if candidate != requested and candidate_cost > requested_cost:
                continue
            if status.accounted_nano_usd + candidate_cost <= config.period_budget_nano_usd:
                selected = candidate
                selected_cost = candidate_cost
                break

        if selected is None:
            action = "block"
            reason = (
                "no non-more-expensive reviewed fallback fits the remaining hard admission budget"
            )
            notice = True
        elif quality_upgrade:
            action = "quality-upgrade"
            reason = "one quality-gated upgrade was authorized; the period admission cap still applies"
            notice = True
        elif protected:
            action = "protected-allow"
            reason = "protected task keeps the requested model; soft allocation is bypassed but the period admission cap is enforced"
            notice = True
        elif selected != requested:
            action = "downgrade"
            if soft_reasons:
                reason = "; ".join(soft_reasons) + "; selected the first reviewed fallback that fits"
            else:
                reason = "requested model did not fit the period admission cap; selected a reviewed non-more-expensive fallback"
            notice = True
        else:
            action = "allow"
            if requested == preferred:
                reason = "preferred request remains inside startup/share/allocation and period-admission controls"
            else:
                reason = "user-requested non-preferred model fits the period admission cap"
            notice = False

        expires_at = current + dt.timedelta(seconds=ttl) if selected is not None else None
        expires_epoch = _epoch_seconds_ceiling(expires_at) if expires_at is not None else None
        models_snapshot = json.dumps(config.models, separators=(",", ":"))
        prices_snapshot = _cards_json(config.prices)
        connection.execute(
            """
            INSERT OR ABORT INTO route_decisions
              (user_hash, request_hash, logical_hash, parent_request_hash, attempt_number, cycle,
               request_payload_sha256, requested_model, selected_model, task_class,
               protected_task, action, reason, projected_input_tokens,
               projected_cached_tokens, projected_cache_write_tokens,
               projected_output_tokens, projected_extra_cost_nano_usd,
               conservative_input_projection, projected_cost_nano_usd,
               requested_model_cost_nano_usd, accounted_before_nano_usd,
               preferred_accounted_before_nano_usd, preferred_cap_nano_usd,
               period_budget_nano_usd, state_before, state_after, models_snapshot_json,
               prices_snapshot_json, config_version, policy_version, fingerprint, created_at,
               expires_at, user_notice)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_hash, request_hash, logical_hash, parent_hash, attempt_number, config.cycle,
                payload_hash, requested, selected, task_class, int(protected), action, reason,
                input_tokens, cached_tokens, cache_write_tokens, output_tokens, extra_cost,
                int(cached_tokens is None), selected_cost, str(requested_cost),
                str(status.accounted_nano_usd),
                str(status.preferred_accounted_nano_usd), config.preferred_cap_nano_usd,
                config.period_budget_nano_usd, state_before, state_after, models_snapshot,
                prices_snapshot, config.config_version, POLICY_VERSION, fingerprint,
                current.isoformat(), expires_epoch,
                int(notice),
            ),
        )
        if selected is not None and selected_cost is not None and expires_at is not None:
            connection.execute(
                """
                INSERT OR ABORT INTO reservations
                  (user_hash, request_hash, cycle, model, projected_cost_nano_usd,
                   status, expires_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    user_hash, request_hash, config.cycle, selected, selected_cost,
                    expires_epoch, current.isoformat(),
                ),
            )
        connection.execute(
            """
            UPDATE budget_users SET state=?, state_cycle=?, updated_at=?
            WHERE user_hash=? AND config_version=?
            """,
            (state_after, config.cycle, current.isoformat(), user_hash, config.config_version),
        )
        row = connection.execute(
            "SELECT * FROM route_decisions WHERE user_hash=? AND request_hash=?",
            (user_hash, request_hash),
        ).fetchone()
        result = _decision_from_row(connection, row, reservation_created=selected is not None)
        connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _usage(payload: Mapping[str, Any]) -> dict[str, int | str | None]:
    if not isinstance(payload, Mapping):
        raise ValueError("response payload must be a JSON object")
    provider_request_id = _identifier(
        payload.get("id", payload.get("provider_request_id")), "provider response id"
    )
    provider_model = _identifier(payload.get("model"), "provider response model")
    if "actual_cost_nano_usd" in payload:
        raise ValueError(
            "actual_cost_nano_usd is not accepted from a response payload; "
            "settlement prices trusted usage with the route's immutable price snapshot"
        )
    raw = payload.get("usage", payload)
    if not isinstance(raw, Mapping):
        raise ValueError("usage must be a JSON object")
    status = payload.get("status", raw.get("status"))
    if status not in RESPONSE_STATUSES:
        raise ValueError(f"response status must be one of: {', '.join(RESPONSE_STATUSES)}")

    def number(name: str, default: int | None = None) -> int:
        value = raw.get(name, default)
        if value is None:
            raise ValueError(f"{name} is required")
        return _nonnegative_int(value, name)

    input_tokens = number("input_tokens")
    output_tokens = number("output_tokens")
    details = raw.get("input_tokens_details", {})
    if details is None:
        details = {}
    if not isinstance(details, Mapping):
        raise ValueError("input_tokens_details must be an object")
    cached = _nonnegative_int(
        details.get("cached_tokens", raw.get("cached_tokens", 0)), "cached_tokens"
    )
    cache_write = _nonnegative_int(
        details.get("cache_write_tokens", raw.get("cache_write_tokens", 0)),
        "cache_write_tokens",
    )
    if cached + cache_write > input_tokens:
        raise ValueError("cached_tokens + cache_write_tokens cannot exceed input_tokens")
    output_details = raw.get("output_tokens_details", {})
    if output_details is None:
        output_details = {}
    if not isinstance(output_details, Mapping):
        raise ValueError("output_tokens_details must be an object")
    reasoning = _nonnegative_int(
        output_details.get("reasoning_tokens", raw.get("reasoning_tokens", 0)),
        "reasoning_tokens",
    )
    if reasoning > output_tokens:
        raise ValueError("reasoning_tokens cannot exceed output_tokens")
    total = number("total_tokens", input_tokens + output_tokens)
    if total < input_tokens + output_tokens:
        raise ValueError("total_tokens cannot be smaller than input_tokens + output_tokens")
    extra = _nonnegative_int(payload.get("extra_cost_nano_usd", 0), "extra_cost_nano_usd")
    return {
        "provider_request_id": provider_request_id,
        "provider_model": provider_model,
        "status": str(status), "input_tokens": input_tokens, "output_tokens": output_tokens,
        "cached_tokens": cached, "cache_write_tokens": cache_write,
        "reasoning_tokens": reasoning,
        "total_tokens": total, "extra_cost_nano_usd": extra,
    }


def settle_usage(
    db: Path,
    user_key: str,
    request_id: str,
    model: str,
    response_payload: Mapping[str, Any],
    *,
    now: dt.datetime | None = None,
) -> UsageRecord:
    """Replace any active reservation with actual usage, even after expiry/release."""
    user_hash = _hash_identifier(_identifier(user_key, "user_key"), "user")
    request_hash = _hash_identifier(_identifier(request_id, "request_id"), "request")
    model = _identifier(model, "model")
    usage = _usage(response_payload)
    if str(usage["provider_model"]) != model:
        raise ValueError("provider response model does not match the selected model")
    provider_request_hash = _hash_identifier(str(usage["provider_request_id"]), "provider-request")
    fingerprint_usage = dict(usage)
    fingerprint_usage["provider_request_id"] = provider_request_hash
    fingerprint = _fingerprint([model, fingerprint_usage])
    current = _utc(now)
    connection = _connect(db)
    try:
        _begin_immediate(connection)
        _expire_reservations(connection, current)
        decision = connection.execute(
            "SELECT * FROM route_decisions WHERE user_hash=? AND request_hash=?",
            (user_hash, request_hash),
        ).fetchone()
        if decision is None:
            raise ValueError("usage requires an existing immutable route decision")
        if decision["selected_model"] is None:
            raise ValueError("cannot settle usage for a blocked route decision")
        if str(decision["selected_model"]) != model:
            raise ValueError("settled model does not match the selected model")

        existing = connection.execute(
            "SELECT * FROM usage_events WHERE user_hash=? AND request_hash=?",
            (user_hash, request_hash),
        ).fetchone()
        if existing is not None:
            if str(existing["fingerprint"]) != fingerprint:
                raise ValueError("request_id is already settled with different usage")
            result = _usage_record_from_rows(existing, decision, recorded=False)
            connection.commit()
            return result

        cards = _cards_from_json(str(decision["prices_snapshot_json"]))
        prices = _price_map(cards)
        selected_price = prices[model]
        requested_price = prices[str(decision["requested_model"])]
        estimated = selected_price.estimate_unbounded(
            int(usage["input_tokens"]), int(usage["output_tokens"]),
            cached_tokens=int(usage["cached_tokens"]),
            cache_write_tokens=int(usage["cache_write_tokens"]),
            extra_cost_nano_usd=int(usage["extra_cost_nano_usd"]),
        )
        actual = estimated
        counterfactual = requested_price.estimate_unbounded(
            int(usage["input_tokens"]), int(usage["output_tokens"]),
            cached_tokens=int(usage["cached_tokens"]),
            cache_write_tokens=int(usage["cache_write_tokens"]),
            extra_cost_nano_usd=int(usage["extra_cost_nano_usd"]),
        )
        projected = int(decision["projected_cost_nano_usd"])
        variance = actual - projected
        reservation = connection.execute(
            "SELECT status FROM reservations WHERE user_hash=? AND request_hash=?",
            (user_hash, request_hash),
        ).fetchone()
        late = reservation is None or str(reservation["status"]) != "active"
        try:
            connection.execute(
                """
                INSERT OR ABORT INTO usage_events
                  (user_hash, request_hash, provider_request_hash, logical_hash, cycle, model,
                   response_status, input_tokens, cached_tokens, cache_write_tokens,
                   reasoning_tokens, output_tokens, total_tokens, estimated_cost_nano_usd,
                   actual_cost_nano_usd, reservation_variance_nano_usd,
                   estimated_savings_nano_usd, over_period_budget, late_settlement,
                   fingerprint, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (
                    user_hash, request_hash, provider_request_hash,
                    str(decision["logical_hash"]), str(decision["cycle"]), model,
                    usage["status"], usage["input_tokens"], usage["cached_tokens"],
                    usage["cache_write_tokens"], usage["reasoning_tokens"],
                    usage["output_tokens"], usage["total_tokens"], str(estimated), str(actual),
                    str(variance), str(counterfactual - actual), int(late), fingerprint,
                    current.isoformat(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            duplicate = connection.execute(
                """
                SELECT request_hash FROM usage_events
                WHERE user_hash=? AND provider_request_hash=?
                """,
                (user_hash, provider_request_hash),
            ).fetchone()
            if duplicate is not None:
                raise ValueError(
                    "provider response id is already settled under a different request_id"
                ) from exc
            raise
        connection.execute(
            """
            UPDATE reservations SET status='settled', updated_at=?
            WHERE user_hash=? AND request_hash=?
            """,
            (current.isoformat(), user_hash, request_hash),
        )
        settled_rows = connection.execute(
            """
            SELECT actual_cost_nano_usd FROM usage_events
            WHERE user_hash=? AND cycle=?
            """,
            (user_hash, str(decision["cycle"])),
        ).fetchall()
        active_rows = connection.execute(
            """
            SELECT projected_cost_nano_usd FROM reservations
            WHERE user_hash=? AND cycle=?
              AND status NOT IN ('settled', 'released', 'expired')
            """,
            (user_hash, str(decision["cycle"])),
        ).fetchall()
        cycle_accounted = sum(int(row[0]) for row in settled_rows) + sum(
            int(row[0]) for row in active_rows
        )
        connection.execute(
            """
            UPDATE usage_events SET over_period_budget=?
            WHERE user_hash=? AND request_hash=?
            """,
            (
                int(cycle_accounted > int(decision["period_budget_nano_usd"])),
                user_hash, request_hash,
            ),
        )
        row = connection.execute(
            "SELECT * FROM usage_events WHERE user_hash=? AND request_hash=?",
            (user_hash, request_hash),
        ).fetchone()
        result = _usage_record_from_rows(row, decision, recorded=True)
        connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _usage_record_from_rows(
    usage: sqlite3.Row, decision: sqlite3.Row, *, recorded: bool
) -> UsageRecord:
    actual = int(usage["actual_cost_nano_usd"])
    return UsageRecord(
        recorded, str(usage["request_hash"]), str(usage["model"]),
        str(usage["response_status"]), int(usage["input_tokens"]),
        int(usage["cached_tokens"]), int(usage["cache_write_tokens"]),
        int(usage["reasoning_tokens"]),
        int(usage["output_tokens"]), int(usage["total_tokens"]),
        int(usage["estimated_cost_nano_usd"]), actual, _usd(actual),
        int(usage["reservation_variance_nano_usd"]),
        int(usage["reservation_variance_nano_usd"]) > 0,
        bool(usage["late_settlement"]), int(usage["estimated_savings_nano_usd"]),
        bool(usage["over_period_budget"]), str(usage["cycle"]),
    )


def assess_quality(
    db: Path,
    user_key: str,
    request_id: str,
    gate: str,
    reason: str,
    *,
    now: dt.datetime | None = None,
) -> QualityDecision:
    user_hash = _hash_identifier(_identifier(user_key, "user_key"), "user")
    request_hash = _hash_identifier(_identifier(request_id, "request_id"), "request")
    if gate not in QUALITY_GATES:
        raise ValueError(f"gate must be one of: {', '.join(QUALITY_GATES)}")
    reason = _identifier(reason, "quality reason")
    fingerprint = _fingerprint([gate, reason])
    current = _utc(now)
    connection = _connect(db)
    try:
        _begin_immediate(connection)
        decision = connection.execute(
            "SELECT * FROM route_decisions WHERE user_hash=? AND request_hash=?",
            (user_hash, request_hash),
        ).fetchone()
        usage = connection.execute(
            "SELECT * FROM usage_events WHERE user_hash=? AND request_hash=?",
            (user_hash, request_hash),
        ).fetchone()
        if decision is None or usage is None:
            raise ValueError("quality assessment requires a settled model attempt")
        existing = connection.execute(
            "SELECT * FROM quality_events WHERE user_hash=? AND request_hash=?",
            (user_hash, request_hash),
        ).fetchone()
        if existing is not None:
            if str(existing["fingerprint"]) != fingerprint:
                raise ValueError("request_id already has a different immutable quality decision")
            current_config = _load_config(connection, user_hash, current)
            authorization_current = (
                int(decision["config_version"]) == current_config.config_version
                and str(decision["policy_version"]) == POLICY_VERSION
                and existing["next_model"] is not None
                and str(existing["next_model"]) in current_config.models
            )
            result = _quality_from_rows(
                existing, decision, usage, recorded=False,
                authorization_current=authorization_current,
            )
            connection.commit()
            return result

        effective = "pass" if gate == "pass" and usage["response_status"] == "completed" else "fail"
        models = _models(json.loads(str(decision["models_snapshot_json"])))
        selected = str(decision["selected_model"])
        index = models.index(selected)
        candidate = models[index - 1] if index > 0 else None
        current_config = _load_config(connection, user_hash, current)
        upgrade = (
            effective == "fail"
            and int(decision["attempt_number"]) == 1
            and str(decision["action"]) == "downgrade"
            and candidate is not None
            and int(decision["config_version"]) == current_config.config_version
            and str(decision["policy_version"]) == POLICY_VERSION
            and candidate in current_config.models
        )
        next_model = candidate if upgrade else None
        connection.execute(
            """
            INSERT OR ABORT INTO quality_events
              (user_hash, request_hash, gate, effective_quality, reason,
               upgrade_recommended, next_model, fingerprint, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_hash, request_hash, gate, effective, reason, int(upgrade),
                next_model, fingerprint, current.isoformat(),
            ),
        )
        row = connection.execute(
            "SELECT * FROM quality_events WHERE user_hash=? AND request_hash=?",
            (user_hash, request_hash),
        ).fetchone()
        result = _quality_from_rows(row, decision, usage, recorded=True)
        connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _quality_from_rows(
    quality: sqlite3.Row,
    decision: sqlite3.Row,
    usage: sqlite3.Row,
    *,
    recorded: bool,
    authorization_current: bool = True,
) -> QualityDecision:
    passed = str(quality["effective_quality"]) == "pass"
    upgrade = bool(quality["upgrade_recommended"]) and authorization_current
    next_model = (
        str(quality["next_model"])
        if upgrade and quality["next_model"] is not None else None
    )
    return QualityDecision(
        recorded, str(quality["request_hash"]), str(quality["effective_quality"]),
        str(usage["response_status"]), str(quality["gate"]), str(quality["reason"]),
        str(decision["selected_model"]) if passed else None,
        upgrade, next_model, upgrade,
    )


def release_reservation(db: Path, user_key: str, request_id: str, *, now: dt.datetime | None = None) -> bool:
    user_hash = _hash_identifier(_identifier(user_key, "user_key"), "user")
    request_hash = _hash_identifier(_identifier(request_id, "request_id"), "request")
    current = _utc(now)
    connection = _connect(db)
    try:
        _begin_immediate(connection)
        _expire_reservations(connection, current)
        cursor = connection.execute(
            """
            UPDATE reservations SET status='released', updated_at=?
            WHERE user_hash=? AND request_hash=? AND status='active'
            """,
            (current.isoformat(), user_hash, request_hash),
        )
        connection.commit()
        return cursor.rowcount == 1
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def renew_reservation(
    db: Path,
    user_key: str,
    request_id: str,
    reservation_ttl_seconds: int = DEFAULT_RESERVATION_TTL_SECONDS,
    *,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Renew an active provider-call lease before its current expiry."""
    user_hash = _hash_identifier(_identifier(user_key, "user_key"), "user")
    request_hash = _hash_identifier(_identifier(request_id, "request_id"), "request")
    ttl = _positive_int(reservation_ttl_seconds, "reservation_ttl_seconds")
    if ttl > MAX_RESERVATION_TTL_SECONDS:
        raise ValueError(
            f"reservation_ttl_seconds cannot exceed {MAX_RESERVATION_TTL_SECONDS}"
        )
    explicit_now = _utc(now) if now is not None else None
    connection = _connect(db)
    try:
        _begin_immediate(connection)
        current = explicit_now or _utc()
        _expire_reservations(connection, current)
        row = connection.execute(
            """
            SELECT expires_at FROM reservations
            WHERE user_hash=? AND request_hash=? AND status='active'
            """,
            (user_hash, request_hash),
        ).fetchone()
        if row is None:
            raise ValueError("only an active, unexpired reservation can be renewed")
        requested_expiry = _epoch_seconds_ceiling(
            current + dt.timedelta(seconds=ttl)
        )
        expires_epoch = max(int(row["expires_at"]), requested_expiry)
        connection.execute(
            """
            UPDATE reservations SET expires_at=?, updated_at=?
            WHERE user_hash=? AND request_hash=? AND status='active'
            """,
            (
                expires_epoch, current.isoformat(), user_hash, request_hash,
            ),
        )
        stored_expiry = _datetime_from_epoch_seconds(expires_epoch)
        connection.commit()
        return {
            "renewed": True,
            "request_hash": request_hash,
            "reservation_expires_at": stored_expiry.isoformat(),
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_final_result(
    db: Path,
    user_key: str,
    logical_request_id: str,
    *,
    now: dt.datetime | None = None,
) -> FinalResult:
    user_hash = _hash_identifier(_identifier(user_key, "user_key"), "user")
    logical_hash = _hash_identifier(_identifier(logical_request_id, "logical_request_id"), "logical")
    current = _utc(now)
    connection = _connect(db)
    try:
        _begin_immediate(connection)
        _expire_reservations(connection, current)
        current_config = _load_config(connection, user_hash, current)
        rows = connection.execute(
            """
            SELECT d.*, r.status AS reservation_status,
                   u.response_status, u.input_tokens, u.cached_tokens,
                   u.cache_write_tokens, u.output_tokens, u.reasoning_tokens, u.total_tokens,
                   u.actual_cost_nano_usd, u.estimated_savings_nano_usd,
                   q.effective_quality, q.upgrade_recommended, q.next_model
            FROM route_decisions d
            LEFT JOIN reservations r
              ON r.user_hash=d.user_hash AND r.request_hash=d.request_hash
            LEFT JOIN usage_events u
              ON u.user_hash=d.user_hash AND u.request_hash=d.request_hash
            LEFT JOIN quality_events q
              ON q.user_hash=d.user_hash AND q.request_hash=d.request_hash
            WHERE d.user_hash=? AND d.logical_hash=?
            ORDER BY d.attempt_number
            """,
            (user_hash, logical_hash),
        ).fetchall()
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    if not rows:
        raise ValueError("logical request has no route decisions")
    attempts: list[dict[str, Any]] = []
    total_input = total_cached = total_cache_write = 0
    total_output = total_reasoning = total_tokens = total_cost = 0
    first_counterfactual_cost: int | None = None
    for row in rows:
        upgrade_is_current = (
            bool(row["upgrade_recommended"])
            and int(row["config_version"]) == current_config.config_version
            and str(row["policy_version"]) == POLICY_VERSION
            and row["next_model"] is not None
            and str(row["next_model"]) in current_config.models
        )
        attempt = {
            "attempt_number": int(row["attempt_number"]),
            "request_hash": str(row["request_hash"]),
            "action": str(row["action"]),
            "requested_model": str(row["requested_model"]),
            "selected_model": str(row["selected_model"]) if row["selected_model"] is not None else None,
            "reservation_status": str(row["reservation_status"]) if row["reservation_status"] is not None else None,
            "response_status": str(row["response_status"]) if row["response_status"] is not None else None,
            "quality": str(row["effective_quality"]) if row["effective_quality"] is not None else None,
            "upgrade_recommended": upgrade_is_current,
            "next_model": str(row["next_model"]) if upgrade_is_current else None,
            "input_tokens": int(row["input_tokens"] or 0),
            "cached_tokens": int(row["cached_tokens"] or 0),
            "cache_write_tokens": int(row["cache_write_tokens"] or 0),
            "output_tokens": int(row["output_tokens"] or 0),
            "reasoning_tokens": int(row["reasoning_tokens"] or 0),
            "total_tokens": int(row["total_tokens"] or 0),
            "actual_cost_nano_usd": int(row["actual_cost_nano_usd"] or 0),
        }
        attempts.append(attempt)
        total_input += attempt["input_tokens"]
        total_cached += attempt["cached_tokens"]
        total_cache_write += attempt["cache_write_tokens"]
        total_output += attempt["output_tokens"]
        total_reasoning += attempt["reasoning_tokens"]
        total_tokens += attempt["total_tokens"]
        total_cost += attempt["actual_cost_nano_usd"]
        if first_counterfactual_cost is None and row["actual_cost_nano_usd"] is not None:
            first_counterfactual_cost = (
                int(row["actual_cost_nano_usd"])
                + int(row["estimated_savings_nano_usd"] or 0)
            )
    latest = attempts[-1]
    if latest["action"] == "block":
        final_status, final_model = "blocked", None
    elif latest["quality"] == "pass":
        final_status, final_model = "success", latest["selected_model"]
    elif latest["upgrade_recommended"]:
        final_status, final_model = "needs-upgrade", None
    elif latest["quality"] == "fail":
        final_status, final_model = "needs-user-review", None
    elif latest["response_status"] is not None:
        final_status, final_model = "awaiting-quality", None
    elif latest["reservation_status"] == "active":
        final_status, final_model = "in-progress", None
    else:
        final_status, final_model = "not-completed", None
    return FinalResult(
        logical_hash, final_status, final_model, attempts, total_input, total_cached,
        total_cache_write, total_output, total_reasoning, total_tokens, total_cost,
        _usd(total_cost),
        (first_counterfactual_cost - total_cost) if first_counterfactual_cost is not None else 0,
        None,
    )


def _demo_cards() -> list[PriceCard]:
    return [
        PriceCard("quality-model", 10, 1, 12, 60),
        PriceCard("balanced-model", 4, 1, 5, 24),
        PriceCard("economy-model", 1, 0, 2, 6),
    ]


def _demo_payload_hash(name: str) -> str:
    return hashlib.sha256(f"model-budget-demo:{name}".encode("utf-8")).hexdigest()


def simulate_final_model() -> dict[str, Any]:
    """Run a deterministic fallback -> incomplete -> one-upgrade scenario."""
    now = dt.datetime(2026, 8, 13, 12, 0, tzinfo=dt.timezone.utc)
    user = "simulation-user"
    with tempfile.TemporaryDirectory() as temp:
        db = Path(temp) / "autopilot.sqlite3"
        configure_user(
            db, user, 100_000, 40, 30, 5,
            ["quality-model", "balanced-model", "economy-model"], _demo_cards(), now=now,
        )

        seed = route_request(
            db, user, "seed-quality", "quality-model", 100, 50,
            request_payload_sha256=_demo_payload_hash("seed-quality"), now=now,
        )
        settle_usage(db, user, "seed-quality", seed.selected_model or "", {
            "id": "provider-seed-quality", "model": seed.selected_model,
            "status": "completed",
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }, now=now)
        for number in (1, 2):
            request_id = f"seed-economy-{number}"
            seed_low = route_request(
                db, user, request_id, "economy-model", 2000, 500,
                request_payload_sha256=_demo_payload_hash(request_id), now=now,
            )
            settle_usage(db, user, request_id, seed_low.selected_model or "", {
                "id": f"provider-{request_id}", "model": seed_low.selected_model,
                "status": "completed",
                "usage": {"input_tokens": 2000, "output_tokens": 500},
            }, now=now)

        first = route_request(
            db, user, "answer-attempt-1", "quality-model", 100, 100,
            request_payload_sha256=_demo_payload_hash("answer-attempt-1"),
            logical_request_id="answer", task_class="coding", now=now,
        )
        first_usage = settle_usage(db, user, "answer-attempt-1", first.selected_model or "", {
            "id": "provider-answer-attempt-1", "model": first.selected_model,
            "status": "incomplete",
            "usage": {"input_tokens": 100, "output_tokens": 100, "total_tokens": 200},
        }, now=now)
        first_quality = assess_quality(
            db, user, "answer-attempt-1", "pass",
            "schema passed, but incomplete response cannot pass the quality gate", now=now,
        )
        second = route_request(
            db, user, "answer-attempt-2", first_quality.next_model or "",
            100, 100, request_payload_sha256=_demo_payload_hash("answer-attempt-2"),
            logical_request_id="answer", parent_request_id="answer-attempt-1",
            task_class="coding", now=now,
        )
        second_usage = settle_usage(db, user, "answer-attempt-2", second.selected_model or "", {
            "id": "provider-answer-attempt-2", "model": second.selected_model,
            "status": "completed",
            "usage": {"input_tokens": 100, "output_tokens": 90, "total_tokens": 190},
        }, now=now)
        second_quality = assess_quality(
            db, user, "answer-attempt-2", "pass", "tests and output contract passed", now=now,
        )
        final = get_final_result(db, user, "answer")
        return {
            "scenario": "fallback-incomplete-single-upgrade",
            "first_route": asdict(first),
            "first_usage": asdict(first_usage),
            "first_quality": asdict(first_quality),
            "second_route": asdict(second),
            "second_usage": asdict(second_usage),
            "second_quality": asdict(second_quality),
            "final_result": asdict(final),
        }


def _parse_price_spec(value: str) -> PriceCard:
    parts = value.rsplit(":", 4)
    if len(parts) != 5:
        raise argparse.ArgumentTypeError(
            "price must be MODEL:INPUT:CACHED:CACHE_WRITE:OUTPUT in USD per 1M tokens"
        )
    try:
        return PriceCard(
            _identifier(parts[0], "model"),
            _parse_rate(parts[1], "input price"),
            _parse_rate(parts[2], "cached input price"),
            _parse_rate(parts[3], "cache-write price"),
            _parse_rate(parts[4], "output price"),
        )
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _read_json(path: Path) -> Mapping[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"JSON input must be a regular non-symlink file: {path}")
    if path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError(f"JSON input exceeds {MAX_JSON_BYTES} bytes: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"cannot read valid JSON from {path}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ValueError("JSON input must contain an object")
    return value


def _json_wire_value(value: object, *, key: str | None = None) -> object:
    if isinstance(value, Mapping):
        return {
            str(item_key): _json_wire_value(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_wire_value(item) for item in value]
    if (
        key is not None
        and "nano_usd" in key
        and isinstance(value, int)
        and not isinstance(value, bool)
    ):
        return str(value)
    return value


def _emit(value: object, output_format: str) -> None:
    data = asdict(value) if hasattr(value, "__dataclass_fields__") else value
    if output_format == "json":
        print(json.dumps(_json_wire_value(data), ensure_ascii=False, indent=2))
        return
    print("# Model Budget Autopilot\n")
    if isinstance(data, Mapping):
        for key, item in data.items():
            print(f"- {key.replace('_', ' ').title()}: `{item}`")
    else:
        print(data)


def _base(parser: argparse.ArgumentParser, *, user: bool = True) -> None:
    parser.add_argument("--db", type=Path, default=Path(".aipc/model-budget.sqlite3"))
    if user:
        parser.add_argument("--user-key", required=True, help="Opaque app user key; only its hash is stored.")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    configure = commands.add_parser("configure", help="Configure a cost portfolio and reviewed model ladder.")
    _base(configure)
    configure.add_argument("--period-budget-usd", required=True)
    configure.add_argument("--preferred-share", type=int, required=True)
    configure.add_argument("--restore-share", type=int, required=True)
    configure.add_argument("--startup-allowance", type=int, default=3)
    configure.add_argument("--window", choices=WINDOWS, default="monthly")
    configure.add_argument("--model", action="append", required=True, help="Repeat highest to lowest.")
    configure.add_argument(
        "--price", type=_parse_price_spec, action="append", required=True,
        help="MODEL:INPUT:CACHED:CACHE_WRITE:OUTPUT, rates in USD per 1M tokens.",
    )
    configure.add_argument("--protected-task", action="append", choices=TASK_CLASSES)

    status = commands.add_parser("status", help="Show settled and active-reservation cost.")
    _base(status)

    route = commands.add_parser("route", help="Persist a route decision and reserve projected cost.")
    _base(route)
    route.add_argument("--request-id", required=True)
    route.add_argument("--logical-request-id")
    route.add_argument("--parent-request-id")
    route.add_argument("--requested-model", required=True)
    route.add_argument(
        "--request-payload-sha256", required=True,
        help="SHA-256 of the exact final provider request payload.",
    )
    route.add_argument("--projected-input-tokens", type=int, required=True)
    route.add_argument("--projected-cached-tokens", type=int)
    route.add_argument("--projected-cache-write-tokens", type=int)
    route.add_argument("--projected-output-tokens", type=int, required=True)
    route.add_argument("--projected-extra-cost-nano-usd", type=int, default=0)
    route.add_argument("--task-class", choices=TASK_CLASSES, default="routine")
    route.add_argument("--reservation-ttl-seconds", type=int, default=DEFAULT_RESERVATION_TTL_SECONDS)

    settle = commands.add_parser("settle", help="Replace a reservation with actual response usage.")
    _base(settle)
    settle.add_argument("--request-id", required=True)
    settle.add_argument("--model", required=True)
    settle.add_argument("--response", type=Path, required=True)

    quality = commands.add_parser("quality", help="Record an immutable external quality-gate result.")
    _base(quality)
    quality.add_argument("--request-id", required=True)
    quality.add_argument("--gate", choices=QUALITY_GATES, required=True)
    quality.add_argument("--reason", required=True)

    release = commands.add_parser("release", help="Release an active reservation after cancellation.")
    _base(release)
    release.add_argument("--request-id", required=True)

    renew = commands.add_parser("renew", help="Renew an active provider-call reservation lease.")
    _base(renew)
    renew.add_argument("--request-id", required=True)
    renew.add_argument(
        "--reservation-ttl-seconds", type=int, default=DEFAULT_RESERVATION_TTL_SECONDS
    )

    final = commands.add_parser(
        "final", help="Summarize all attempts and the final quality-gated selected model."
    )
    _base(final)
    final.add_argument("--logical-request-id", required=True)

    simulate = commands.add_parser("simulate", help="Run the deterministic fallback/upgrade proof scenario.")
    _base(simulate, user=False)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        if args.command == "configure":
            result = configure_user(
                args.db, args.user_key, _parse_usd_to_nano(args.period_budget_usd, "period budget"),
                args.preferred_share, args.restore_share, args.startup_allowance,
                args.model, args.price,
                protected_tasks=args.protected_task or DEFAULT_PROTECTED_TASKS,
                window=args.window,
            )
        elif args.command == "status":
            result = get_status(args.db, args.user_key)
        elif args.command == "route":
            result = route_request(
                args.db, args.user_key, args.request_id, args.requested_model,
                args.projected_input_tokens, args.projected_output_tokens,
                request_payload_sha256=args.request_payload_sha256,
                projected_cached_tokens=args.projected_cached_tokens,
                projected_cache_write_tokens=args.projected_cache_write_tokens,
                projected_extra_cost_nano_usd=args.projected_extra_cost_nano_usd,
                task_class=args.task_class, logical_request_id=args.logical_request_id,
                parent_request_id=args.parent_request_id,
                reservation_ttl_seconds=args.reservation_ttl_seconds,
            )
        elif args.command == "settle":
            result = settle_usage(
                args.db, args.user_key, args.request_id, args.model, _read_json(args.response)
            )
        elif args.command == "quality":
            result = assess_quality(
                args.db, args.user_key, args.request_id, args.gate, args.reason
            )
        elif args.command == "release":
            result = {"released": release_reservation(args.db, args.user_key, args.request_id)}
        elif args.command == "renew":
            result = renew_reservation(
                args.db, args.user_key, args.request_id, args.reservation_ttl_seconds
            )
        elif args.command == "final":
            result = get_final_result(args.db, args.user_key, args.logical_request_id)
        else:
            result = simulate_final_model()
        _emit(result, args.format)
        if isinstance(result, RouteDecision) and result.action == "block":
            return 3
        if isinstance(result, RouteDecision) and not result.execution_authorized:
            return 4
        return 0
    except (ValueError, sqlite3.Error, OSError, OverflowError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
