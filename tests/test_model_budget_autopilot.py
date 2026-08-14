from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
import sqlite3
import stat
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "ai-project-copilot" / "scripts" / "model_budget_autopilot.py"


def load_module():
    spec = importlib.util.spec_from_file_location("aipc_model_budget_autopilot", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ModelBudgetAutopilotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_module()

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "state" / "autopilot.sqlite3"
        self.now = dt.datetime(2026, 8, 13, 12, 0, tzinfo=dt.timezone.utc)
        self.user = "private-user-42"

    def cards(self, rate: int = 1):
        return [
            self.mod.PriceCard("quality", rate, rate, rate, rate),
            self.mod.PriceCard("balanced", rate, rate, rate, rate),
            self.mod.PriceCard("economy", rate, rate, rate, rate),
        ]

    def configure(
        self,
        *,
        budget: int = 1000,
        maximum: int = 40,
        restore: int = 30,
        startup: int = 10,
        cards=None,
        window: str = "monthly",
    ):
        return self.mod.configure_user(
            self.db, self.user, budget, maximum, restore, startup,
            ["quality", "balanced", "economy"], cards or self.cards(),
            window=window, now=self.now,
        )

    def route(
        self,
        request_id: str,
        model: str,
        tokens: int,
        *,
        output: int = 0,
        task_class: str = "routine",
        logical: str | None = None,
        parent: str | None = None,
        ttl: int = 3600,
        payload_hash: str | None = None,
        projected_cached: int | None = None,
        projected_cache_write: int | None = None,
        projected_extra_cost: int = 0,
        now: dt.datetime | None = None,
    ):
        payload_hash = payload_hash or hashlib.sha256(
            f"test-payload:{request_id}".encode("utf-8")
        ).hexdigest()
        return self.mod.route_request(
            self.db, self.user, request_id, model, tokens, output,
            request_payload_sha256=payload_hash,
            projected_cached_tokens=projected_cached,
            projected_cache_write_tokens=projected_cache_write,
            projected_extra_cost_nano_usd=projected_extra_cost,
            task_class=task_class, logical_request_id=logical,
            parent_request_id=parent, reservation_ttl_seconds=ttl,
            now=now or self.now,
        )

    def settle(
        self,
        request_id: str,
        model: str,
        input_tokens: int,
        *,
        output_tokens: int = 0,
        status: str = "completed",
        cached_tokens: int = 0,
        cache_write_tokens: int = 0,
        reasoning_tokens: int = 0,
        provider_id: str | None = None,
        now: dt.datetime | None = None,
    ):
        payload = {
            "id": provider_id or f"provider-{request_id}",
            "model": model,
            "status": status,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "input_tokens_details": {
                    "cached_tokens": cached_tokens,
                    "cache_write_tokens": cache_write_tokens,
                },
                "output_tokens_details": {"reasoning_tokens": reasoning_tokens},
            },
        }
        return self.mod.settle_usage(
            self.db, self.user, request_id, model, payload, now=now or self.now
        )

    def test_configuration_validates_percentages_ladder_and_prices(self) -> None:
        for maximum, restore, startup in ((0, 0, 0), (100, 30, 3), (40, 40, 3), (40, 30, 41)):
            with self.subTest(values=(maximum, restore, startup)), self.assertRaises(ValueError):
                self.mod.configure_user(
                    self.db, self.user, 1000, maximum, restore, startup,
                    ["quality", "balanced"], self.cards()[:2], now=self.now,
                )
        with self.assertRaisesRegex(ValueError, "exactly match"):
            self.mod.configure_user(
                self.db, self.user, 1000, 40, 30, 3,
                ["quality", "balanced"], [self.cards()[0]], now=self.now,
            )
        config = self.configure()
        self.assertEqual(400, config.preferred_cap_nano_usd)
        self.assertEqual(100, config.startup_allowance_nano_usd)

    def test_decimal_price_parser_is_exact(self) -> None:
        card = self.mod._parse_price_spec("gpt:5.00:0.50:6.25:30.00")
        self.assertEqual(5000, card.input_nano_usd_per_token)
        self.assertEqual(500, card.cached_input_nano_usd_per_token)
        self.assertEqual(6250, card.cache_write_nano_usd_per_token)
        self.assertEqual(30000, card.output_nano_usd_per_token)
        with self.assertRaises(Exception):
            self.mod._parse_price_spec("gpt:0.0001:0:0:1")

    def test_cold_start_uses_bounded_startup_allowance(self) -> None:
        self.configure(startup=10)
        first = self.route("first", "quality", 100)
        self.assertEqual("allow", first.action)
        self.assertEqual("quality", first.selected_model)
        self.settle("first", "quality", 100)
        second = self.route("second", "quality", 1)
        self.assertEqual("downgrade", second.action)
        self.assertEqual("balanced", second.selected_model)

    def test_first_preferred_request_within_fixed_cap_is_not_cold_start_downgraded(self) -> None:
        self.configure(startup=3)
        first = self.route("large-first", "quality", 300)
        self.assertEqual("allow", first.action)
        self.assertEqual("quality", first.selected_model)

    def test_fixed_allocation_boundary_is_exact(self) -> None:
        self.configure(startup=40)
        first = self.route("first", "quality", 399)
        self.settle("first", first.selected_model, 399)
        boundary = self.route("boundary", "quality", 1)
        self.assertEqual("allow", boundary.action)
        self.settle("boundary", boundary.selected_model, 1)
        over = self.route("over", "quality", 1)
        self.assertEqual("downgrade", over.action)

    def test_hysteresis_restores_only_below_restore_line(self) -> None:
        self.configure(startup=10)
        first = self.route("q1", "quality", 100)
        self.settle("q1", first.selected_model, 100)
        fallback = self.route("q2", "quality", 1)
        self.assertEqual("fallback", fallback.state_after)
        self.settle("q2", fallback.selected_model, 1)

        low = self.route("low", "economy", 300)
        self.assertEqual("economy", low.selected_model)
        self.settle("low", "economy", 300)
        restored = self.route("q3", "quality", 10)
        self.assertEqual("normal", restored.state_after)
        self.assertEqual("quality", restored.selected_model)

    def test_user_requested_lower_model_is_not_secretly_upgraded_or_redowngraded(self) -> None:
        self.configure()
        low = self.route("low", "balanced", 50)
        self.assertEqual("allow", low.action)
        self.assertEqual("balanced", low.selected_model)

    def test_protected_task_bypasses_soft_control_but_not_hard_budget(self) -> None:
        self.configure(budget=500, startup=10)
        seed = self.route("seed", "quality", 50)
        self.settle("seed", seed.selected_model, 50)
        protected = self.route("protected", "quality", 100, task_class="security")
        self.assertEqual("protected-allow", protected.action)
        self.assertEqual("quality", protected.selected_model)
        self.settle("protected", "quality", 100)
        blocked = self.route("too-big", "quality", 400, task_class="release")
        self.assertEqual("block", blocked.action)
        self.assertIsNone(blocked.selected_model)

    def test_unknown_task_class_cannot_claim_protection(self) -> None:
        self.configure()
        with self.assertRaises(ValueError):
            self.route("x", "quality", 10, task_class="super-important")

    def test_route_rejects_projected_total_tokens_that_cannot_be_settled(self) -> None:
        self.configure()
        with self.assertRaisesRegex(ValueError, r"input \+ output tokens exceed"):
            self.route(
                "unsettleable-total", "economy", self.mod.SQLITE_MAX_INT,
                output=self.mod.SQLITE_MAX_INT,
            )
        with contextlib.closing(sqlite3.connect(self.db)) as connection, connection:
            self.assertEqual(
                0, connection.execute("SELECT COUNT(*) FROM route_decisions").fetchone()[0]
            )

    def test_hard_budget_can_select_a_cheaper_fallback(self) -> None:
        cards = [
            self.mod.PriceCard("quality", 10, 10, 10, 10),
            self.mod.PriceCard("balanced", 2, 2, 2, 2),
            self.mod.PriceCard("economy", 1, 1, 1, 1),
        ]
        self.configure(budget=100, maximum=99, restore=90, startup=99, cards=cards)
        decision = self.route("hard-fallback", "quality", 20)
        self.assertEqual("downgrade", decision.action)
        self.assertEqual("balanced", decision.selected_model)
        self.assertEqual(40, decision.projected_cost_nano_usd)

    def test_router_never_downgrades_to_a_more_expensive_candidate(self) -> None:
        cards = [
            self.mod.PriceCard("quality", 1, 1, 1, 1),
            self.mod.PriceCard("balanced", 2, 2, 2, 2),
            self.mod.PriceCard("economy", 3, 3, 3, 3),
        ]
        self.configure(startup=10, cards=cards)
        first = self.route("cheap-quality", "quality", 100)
        self.settle("cheap-quality", first.selected_model, 100)
        decision = self.route("must-not-cost-more", "quality", 1)
        self.assertEqual("block", decision.action)
        self.assertIsNone(decision.selected_model)
        self.assertIn("non-more-expensive", decision.reason)

    def test_overflowing_fallback_candidate_is_skipped(self) -> None:
        maximum = self.mod.SQLITE_MAX_INT
        cards = [
            self.mod.PriceCard("quality", 1, 1, 1, 1),
            self.mod.PriceCard("balanced", maximum, maximum, maximum, maximum),
            self.mod.PriceCard("economy", 1, 1, 1, 1),
        ]
        self.configure(
            budget=100, maximum=1, restore=0, startup=0, cards=cards
        )
        decision = self.route("overflowing-middle", "quality", 2)
        self.assertEqual("downgrade", decision.action)
        self.assertEqual("economy", decision.selected_model)
        self.assertEqual(2, decision.projected_cost_nano_usd)

    def test_overflowing_requested_projection_can_use_a_safe_fallback(self) -> None:
        maximum = self.mod.SQLITE_MAX_INT
        cards = [
            self.mod.PriceCard("quality", maximum, maximum, maximum, maximum),
            self.mod.PriceCard("economy", 1, 1, 1, 1),
        ]
        self.mod.configure_user(
            self.db, self.user, 100, 1, 0, 0,
            ["quality", "economy"], cards, now=self.now,
        )
        decision = self.mod.route_request(
            self.db, self.user, "overflowing-requested", "quality", 2, 0,
            request_payload_sha256="e" * 64, now=self.now,
        )
        self.assertEqual("downgrade", decision.action)
        self.assertEqual("economy", decision.selected_model)
        self.assertEqual(maximum * 2, decision.requested_model_cost_nano_usd)
        self.assertEqual(2, decision.projected_cost_nano_usd)

    def test_unknown_cache_mix_uses_the_highest_input_rate_for_reservation(self) -> None:
        cards = [
            self.mod.PriceCard("quality", 10, 1, 20, 0),
            self.mod.PriceCard("balanced", 10, 1, 20, 0),
            self.mod.PriceCard("economy", 10, 1, 20, 0),
        ]
        self.configure(budget=2500, cards=cards)
        conservative = self.route("unknown-cache", "balanced", 100)
        self.assertTrue(conservative.conservative_input_projection)
        self.assertEqual(2000, conservative.projected_cost_nano_usd)
        self.mod.release_reservation(self.db, self.user, "unknown-cache", now=self.now)
        exact = self.route(
            "known-cache", "balanced", 100,
            projected_cached=0, projected_cache_write=0, projected_extra_cost=123,
        )
        self.assertFalse(exact.conservative_input_projection)
        self.assertEqual(1123, exact.projected_cost_nano_usd)

    def test_block_decision_remains_idempotent_after_budget_changes(self) -> None:
        self.configure(budget=100)
        first = self.route("blocked", "economy", 101)
        self.assertEqual("block", first.action)
        self.configure(budget=1000)
        replay = self.route("blocked", "economy", 101)
        self.assertEqual("block", replay.action)
        self.assertEqual(first.reason, replay.reason)
        self.assertFalse(replay.reservation_created)

    def test_route_idempotency_replays_and_conflicts(self) -> None:
        self.configure()
        first = self.route("idem", "balanced", 10)
        replay = self.route("idem", "balanced", 10)
        self.assertTrue(first.reservation_created)
        self.assertFalse(replay.reservation_created)
        self.assertTrue(first.execution_authorized)
        self.assertFalse(replay.execution_authorized)
        self.assertIsNone(replay.selected_model)
        self.assertEqual(first.request_hash, replay.request_hash)
        with self.assertRaisesRegex(ValueError, "different immutable"):
            self.route("idem", "balanced", 11)
        with self.assertRaisesRegex(ValueError, "different immutable"):
            self.route("idem", "balanced", 10, payload_hash="f" * 64)

    def test_ttl_releases_capacity_but_preserves_request_identity(self) -> None:
        self.configure()
        first = self.route("ttl", "balanced", 100, ttl=60)
        self.assertEqual("active", first.reservation_status)
        later = self.now + dt.timedelta(seconds=60)
        status = self.mod.get_status(self.db, self.user, now=later)
        self.assertEqual(0, status.reserved_nano_usd)
        replay = self.route("ttl", "balanced", 100, ttl=60, now=later)
        self.assertEqual("expired", replay.reservation_status)
        self.assertFalse(replay.reservation_created)
        self.assertFalse(replay.execution_authorized)
        self.assertIsNone(replay.selected_model)
        with self.assertRaises(ValueError):
            self.route("ttl", "balanced", 101, ttl=60, now=later)

    def test_closed_route_replay_has_no_executable_model_and_cli_is_nonzero(self) -> None:
        self.configure()
        payload_hash = hashlib.sha256(b"test-payload:closed-cli").hexdigest()
        decision = self.route(
            "closed-cli", "balanced", 10, payload_hash=payload_hash
        )
        self.assertTrue(decision.execution_authorized)
        self.assertTrue(
            self.mod.release_reservation(
                self.db, self.user, "closed-cli", now=self.now
            )
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = self.mod.main([
                "route", "--db", str(self.db), "--user-key", self.user,
                "--request-id", "closed-cli", "--requested-model", "balanced",
                "--request-payload-sha256", payload_hash,
                "--projected-input-tokens", "10",
                "--projected-output-tokens", "0",
                "--reservation-ttl-seconds", "3600",
            ])
        replay = json.loads(output.getvalue())
        self.assertEqual(4, code)
        self.assertEqual("released", replay["reservation_status"])
        self.assertFalse(replay["execution_authorized"])
        self.assertIsNone(replay["selected_model"])

    def test_expired_replay_cannot_authorize_spend_after_capacity_is_reused(self) -> None:
        self.configure(budget=10, maximum=99, restore=90, startup=99)
        first = self.route("expired-first", "economy", 10, ttl=1)
        self.assertTrue(first.execution_authorized)
        later = self.now + dt.timedelta(seconds=1)
        replacement = self.route(
            "replacement", "economy", 10, now=later
        )
        self.assertTrue(replacement.execution_authorized)
        replay = self.route(
            "expired-first", "economy", 10, ttl=1, now=later
        )
        self.assertEqual("expired", replay.reservation_status)
        self.assertFalse(replay.execution_authorized)
        self.assertIsNone(replay.selected_model)
        self.assertEqual(
            10, self.mod.get_status(self.db, self.user, now=later).accounted_nano_usd
        )

    def test_final_result_sweeps_expired_reservations_without_status_call(self) -> None:
        self.configure()
        self.route("final-expiry", "balanced", 10, logical="final-expiry", ttl=1)
        result = self.mod.get_final_result(
            self.db, self.user, "final-expiry",
            now=self.now + dt.timedelta(seconds=1),
        )
        self.assertEqual("not-completed", result.final_status)
        self.assertEqual("expired", result.attempts[0]["reservation_status"])

    def test_active_reservation_lease_can_be_renewed_before_expiry(self) -> None:
        self.configure()
        self.route("lease", "balanced", 10, ttl=60)
        renewed = self.mod.renew_reservation(
            self.db, self.user, "lease", 120,
            now=self.now + dt.timedelta(seconds=30),
        )
        self.assertTrue(renewed["renewed"])
        still_active = self.route(
            "lease", "balanced", 10, ttl=60,
            now=self.now + dt.timedelta(seconds=61),
        )
        self.assertEqual("active", still_active.reservation_status)
        self.assertEqual(
            renewed["reservation_expires_at"], still_active.reservation_expires_at
        )

    def test_renew_returns_the_exact_persisted_second(self) -> None:
        self.configure()
        self.route("microsecond-lease", "balanced", 10, ttl=60)
        renewed = self.mod.renew_reservation(
            self.db, self.user, "microsecond-lease", 120,
            now=self.now.replace(microsecond=900_000),
        )
        replay = self.route("microsecond-lease", "balanced", 10, ttl=60, now=self.now)
        self.assertEqual(renewed["reservation_expires_at"], replay.reservation_expires_at)
        self.assertNotIn(".900000", renewed["reservation_expires_at"])

    def test_microsecond_route_ttl_never_expires_early(self) -> None:
        self.configure()
        start = self.now.replace(microsecond=900_000)
        decision = self.route(
            "short-microsecond-lease", "balanced", 10, ttl=1, now=start
        )
        before_declared_ttl = start + dt.timedelta(milliseconds=100)
        status = self.mod.get_status(self.db, self.user, now=before_declared_ttl)
        replay = self.route(
            "short-microsecond-lease", "balanced", 10, ttl=1,
            now=before_declared_ttl,
        )
        self.assertEqual(10, status.reserved_nano_usd)
        self.assertEqual("active", replay.reservation_status)
        persisted_expiry = dt.datetime.fromisoformat(decision.reservation_expires_at)
        self.assertGreaterEqual(persisted_expiry - start, dt.timedelta(seconds=1))
        expired = self.mod.get_status(self.db, self.user, now=persisted_expiry)
        self.assertEqual(0, expired.reserved_nano_usd)

    def test_microsecond_renewal_ttl_never_expires_early(self) -> None:
        self.configure()
        start = self.now.replace(microsecond=900_000)
        initial = self.route(
            "short-renewed-lease", "balanced", 10, ttl=60, now=start
        )
        renewed = self.mod.renew_reservation(
            self.db, self.user, "short-renewed-lease", 1, now=start
        )
        before_declared_ttl = start + dt.timedelta(milliseconds=100)
        status = self.mod.get_status(self.db, self.user, now=before_declared_ttl)
        replay = self.route(
            "short-renewed-lease", "balanced", 10, ttl=60,
            now=before_declared_ttl,
        )
        self.assertEqual(10, status.reserved_nano_usd)
        self.assertEqual("active", replay.reservation_status)
        persisted_expiry = dt.datetime.fromisoformat(renewed["reservation_expires_at"])
        self.assertGreaterEqual(persisted_expiry - start, dt.timedelta(seconds=1))
        self.assertEqual(initial.reservation_expires_at, renewed["reservation_expires_at"])

    def test_released_fallback_does_not_stick_when_accounted_returns_to_zero(self) -> None:
        self.configure(startup=3)
        first = self.route("fallback-then-cancel", "quality", 500)
        self.assertEqual("downgrade", first.action)
        self.assertEqual("fallback", first.state_after)
        self.assertTrue(
            self.mod.release_reservation(
                self.db, self.user, "fallback-then-cancel", now=self.now
            )
        )
        next_route = self.route("fresh-after-cancel", "quality", 100)
        self.assertEqual("normal", next_route.state_after)
        self.assertEqual("quality", next_route.selected_model)

    def test_actual_overrun_is_settled_and_creates_visible_debt(self) -> None:
        self.configure(budget=100, maximum=99, restore=90, startup=99)
        decision = self.route("overrun", "economy", 90)
        self.assertEqual("economy", decision.selected_model)
        record = self.settle("overrun", "economy", 150)
        self.assertTrue(record.reservation_overrun)
        self.assertEqual(60, record.reservation_variance_nano_usd)
        self.assertTrue(record.over_period_budget)
        status = self.mod.get_status(self.db, self.user, now=self.now)
        self.assertEqual(150, status.committed_nano_usd)
        self.assertTrue(status.over_period_budget)
        self.assertEqual("block", self.route("after-debt", "economy", 1).action)

    def test_multiple_maximum_usage_events_do_not_overflow_sql_aggregation(self) -> None:
        maximum = self.mod.SQLITE_MAX_INT
        self.configure(budget=maximum, maximum=99, restore=90, startup=99)
        self.route("huge-a", "economy", 1)
        self.route("huge-b", "economy", 1)
        first = self.settle("huge-a", "economy", maximum)
        second = self.settle("huge-b", "economy", maximum)
        self.assertTrue(first.over_period_budget)
        self.assertTrue(second.over_period_budget)
        status = self.mod.get_status(self.db, self.user, now=self.now)
        self.assertEqual(maximum * 2, status.committed_nano_usd)
        self.assertTrue(status.over_period_budget)

    def test_counterfactual_overflow_cannot_block_real_usage_settlement(self) -> None:
        maximum = self.mod.SQLITE_MAX_INT
        cards = [
            self.mod.PriceCard("quality", maximum, maximum, maximum, maximum),
            self.mod.PriceCard("economy", 1, 1, 1, 1),
        ]
        self.mod.configure_user(
            self.db, self.user, 100, 1, 0, 0,
            ["quality", "economy"], cards, now=self.now,
        )
        decision = self.mod.route_request(
            self.db, self.user, "counterfactual-overflow", "quality", 1, 0,
            request_payload_sha256="c" * 64, now=self.now,
        )
        self.assertEqual("economy", decision.selected_model)
        record = self.settle(
            "counterfactual-overflow", "economy", 2, now=self.now
        )
        self.assertEqual(2, record.actual_cost_nano_usd)
        self.assertEqual(maximum * 2 - 2, record.estimated_savings_vs_requested_nano_usd)
        self.assertEqual(2, self.mod.get_status(self.db, self.user, now=self.now).committed_nano_usd)
        with contextlib.closing(sqlite3.connect(self.db)) as connection, connection:
            self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0])
            stored = connection.execute(
                "SELECT typeof(estimated_savings_nano_usd), estimated_savings_nano_usd "
                "FROM usage_events"
            ).fetchone()
        self.assertEqual(("text", str(maximum * 2 - 2)), stored)

    def test_actual_cost_beyond_sqlite_integer_range_is_still_settled(self) -> None:
        maximum = self.mod.SQLITE_MAX_INT
        cards = [
            self.mod.PriceCard("quality", maximum, maximum, maximum, maximum),
            self.mod.PriceCard("economy", maximum, maximum, maximum, maximum),
        ]
        self.mod.configure_user(
            self.db, self.user, maximum, 99, 90, 99,
            ["quality", "economy"], cards, now=self.now,
        )
        decision = self.mod.route_request(
            self.db, self.user, "unbounded-actual", "economy", 1, 0,
            request_payload_sha256="d" * 64, now=self.now,
        )
        self.assertEqual(maximum, decision.projected_cost_nano_usd)
        record = self.settle("unbounded-actual", "economy", 2, now=self.now)
        self.assertEqual(maximum * 2, record.actual_cost_nano_usd)
        self.assertEqual(
            maximum * 2,
            self.mod.get_status(self.db, self.user, now=self.now).committed_nano_usd,
        )
        self.assertTrue(record.over_period_budget)
        self.assertEqual("18446744073.709551614", record.actual_cost_usd)
        with contextlib.closing(sqlite3.connect(self.db)) as connection, connection:
            stored = connection.execute(
                "SELECT typeof(actual_cost_nano_usd), actual_cost_nano_usd "
                "FROM usage_events"
            ).fetchone()
        self.assertEqual(("text", str(maximum * 2)), stored)

    def test_settlement_is_idempotent_and_conflicting_usage_fails(self) -> None:
        self.configure()
        self.route("settle", "balanced", 20)
        first = self.settle("settle", "balanced", 20)
        replay = self.settle("settle", "balanced", 20)
        self.assertTrue(first.recorded)
        self.assertFalse(replay.recorded)
        with self.assertRaisesRegex(ValueError, "different usage"):
            self.settle("settle", "balanced", 21)

    def test_response_cannot_override_snapshot_pricing(self) -> None:
        self.configure()
        self.route("untrusted-cost", "balanced", 10)
        with self.assertRaisesRegex(ValueError, "not accepted"):
            self.mod.settle_usage(
                self.db, self.user, "untrusted-cost", "balanced",
                {
                    "id": "provider-untrusted-cost",
                    "model": "balanced",
                    "status": "completed",
                    "actual_cost_nano_usd": 0,
                    "usage": {"input_tokens": 10, "output_tokens": 0},
                },
                now=self.now,
            )

    def test_response_requires_explicit_status_and_selected_model(self) -> None:
        self.configure()
        self.route("response-contract", "balanced", 10)
        base = {
            "id": "provider-response-contract",
            "model": "balanced",
            "status": "completed",
            "usage": {"input_tokens": 10, "output_tokens": 0},
        }
        missing_status = dict(base)
        missing_status.pop("status")
        with self.assertRaisesRegex(ValueError, "response status"):
            self.mod.settle_usage(
                self.db, self.user, "response-contract", "balanced", missing_status,
                now=self.now,
            )
        wrong_model = dict(base)
        wrong_model["model"] = "economy"
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.mod.settle_usage(
                self.db, self.user, "response-contract", "balanced", wrong_model,
                now=self.now,
            )

    def test_provider_response_id_cannot_be_double_settled(self) -> None:
        self.configure()
        self.route("internal-a", "balanced", 10)
        self.route("internal-b", "balanced", 10)
        self.settle("internal-a", "balanced", 10, provider_id="same-provider-response")
        with self.assertRaisesRegex(ValueError, "already settled"):
            self.settle("internal-b", "balanced", 10, provider_id="same-provider-response")

    def test_usage_insert_aborts_even_if_schema_default_is_replace(self) -> None:
        self.configure(budget=100)
        with contextlib.closing(sqlite3.connect(self.db)) as connection, connection:
            sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='usage_events'"
            ).fetchone()[0]
            replaced = sql.replace(
                "UNIQUE (user_hash, provider_request_hash)",
                "UNIQUE (user_hash, provider_request_hash) ON CONFLICT REPLACE",
            )
            self.assertNotEqual(sql, replaced)
            schema_version = connection.execute("PRAGMA schema_version").fetchone()[0]
            connection.execute("PRAGMA writable_schema=ON")
            connection.execute(
                "UPDATE sqlite_master SET sql=? WHERE type='table' AND name='usage_events'",
                (replaced,),
            )
            connection.execute("PRAGMA writable_schema=OFF")
            connection.execute(f"PRAGMA schema_version={schema_version + 1}")

        verified = self.mod._connect(self.db)
        verified.close()
        first = self.route("replace-one", "economy", 10)
        second = self.route("replace-two", "economy", 10)
        self.settle(
            "replace-one", first.selected_model, 10,
            provider_id="replace-provider-response",
        )
        with self.assertRaisesRegex(ValueError, "already settled"):
            self.settle(
                "replace-two", second.selected_model, 10,
                provider_id="replace-provider-response",
            )
        with contextlib.closing(sqlite3.connect(self.db)) as connection, connection:
            self.assertEqual(
                1, connection.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0]
            )
            second_status = connection.execute(
                "SELECT status FROM reservations WHERE request_hash=?",
                (second.request_hash,),
            ).fetchone()[0]
        self.assertEqual("active", second_status)

    def test_late_usage_after_release_is_not_lost(self) -> None:
        self.configure()
        self.route("late", "balanced", 20)
        self.assertTrue(self.mod.release_reservation(self.db, self.user, "late", now=self.now))
        record = self.settle("late", "balanced", 20)
        self.assertTrue(record.late_settlement)
        self.assertEqual(20, self.mod.get_status(self.db, self.user, now=self.now).committed_nano_usd)

    def test_price_snapshot_survives_reconfiguration(self) -> None:
        self.configure(cards=self.cards(rate=2))
        self.route("snapshot", "balanced", 10)
        self.mod.release_reservation(self.db, self.user, "snapshot", now=self.now)
        self.configure(cards=self.cards(rate=50))
        record = self.settle("snapshot", "balanced", 10)
        self.assertEqual(20, record.actual_cost_nano_usd)

    def test_cached_and_cache_write_tokens_use_distinct_prices(self) -> None:
        cards = [
            self.mod.PriceCard("quality", 10, 1, 12, 20),
            self.mod.PriceCard("balanced", 10, 1, 12, 20),
            self.mod.PriceCard("economy", 10, 1, 12, 20),
        ]
        self.configure(budget=10_000, cards=cards)
        self.route("cache", "balanced", 100, output=10)
        record = self.settle(
            "cache", "balanced", 100, output_tokens=10,
            cached_tokens=30, cache_write_tokens=20,
        )
        self.assertEqual(50 * 10 + 30 * 1 + 20 * 12 + 10 * 20, record.actual_cost_nano_usd)

    def test_incomplete_response_cannot_pass_quality_and_authorizes_one_upgrade(self) -> None:
        self.configure(startup=10)
        seed = self.route("seed", "quality", 100)
        self.settle("seed", seed.selected_model, 100)
        first = self.route("a1", "quality", 1, logical="logical")
        self.assertEqual("balanced", first.selected_model)
        self.settle("a1", "balanced", 1, status="incomplete")
        quality = self.mod.assess_quality(
            self.db, self.user, "a1", "pass", "schema alone is insufficient", now=self.now
        )
        self.assertEqual("fail", quality.effective_quality)
        self.assertTrue(quality.upgrade_recommended)
        self.assertEqual("quality", quality.next_model)

        second = self.route("a2", "quality", 1, logical="logical", parent="a1")
        self.assertEqual("quality-upgrade", second.action)
        self.settle("a2", "quality", 1, status="incomplete")
        second_quality = self.mod.assess_quality(
            self.db, self.user, "a2", "fail", "still incomplete", now=self.now
        )
        self.assertFalse(second_quality.upgrade_recommended)
        with self.assertRaisesRegex(ValueError, "limited to one"):
            self.route("a3", "quality", 1, logical="logical", parent="a2")

    def test_reconfiguration_invalidates_pending_quality_upgrade(self) -> None:
        self.configure(startup=10)
        seed = self.route("pending-seed", "quality", 100)
        self.settle("pending-seed", seed.selected_model, 100)
        fallback = self.route("pending-a1", "quality", 1, logical="pending-logical")
        self.settle("pending-a1", fallback.selected_model, 1, status="failed")
        quality = self.mod.assess_quality(
            self.db, self.user, "pending-a1", "fail", "verification failed", now=self.now
        )
        self.assertTrue(quality.upgrade_recommended)
        reconfigured = self.configure(budget=2000)
        self.assertEqual(2, reconfigured.config_version)

        replay = self.mod.assess_quality(
            self.db, self.user, "pending-a1", "fail", "verification failed", now=self.now
        )
        self.assertFalse(replay.upgrade_recommended)
        self.assertFalse(replay.automatic_upgrade_authorized)
        self.assertIsNone(replay.next_model)
        with self.assertRaisesRegex(ValueError, "stale"):
            self.route(
                "pending-a2", "quality", 1,
                logical="pending-logical", parent="pending-a1",
            )
        final = self.mod.get_final_result(
            self.db, self.user, "pending-logical", now=self.now
        )
        self.assertEqual("needs-user-review", final.final_status)
        self.assertFalse(final.attempts[-1]["upgrade_recommended"])

    def test_stale_policy_upgrade_does_not_permanently_block_reconfiguration(self) -> None:
        self.configure(startup=10)
        seed = self.route("legacy-seed", "quality", 100)
        self.settle("legacy-seed", seed.selected_model, 100)
        fallback = self.route(
            "legacy-attempt", "quality", 1, logical="legacy-logical"
        )
        self.settle("legacy-attempt", fallback.selected_model, 1, status="failed")
        quality = self.mod.assess_quality(
            self.db, self.user, "legacy-attempt", "fail", "legacy failure", now=self.now
        )
        self.assertTrue(quality.upgrade_recommended)
        with contextlib.closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute(
                "UPDATE route_decisions SET policy_version=? WHERE request_hash=?",
                ("model-budget-autopilot-v1", fallback.request_hash),
            )
        reconfigured = self.configure(budget=2000)
        self.assertEqual(2, reconfigured.config_version)
        with self.assertRaisesRegex(ValueError, "stale"):
            self.route(
                "legacy-upgrade", "quality", 1,
                logical="legacy-logical", parent="legacy-attempt",
            )

    def test_reconfiguration_invalidates_late_upgrade_from_old_ladder(self) -> None:
        old_cards = [
            self.mod.PriceCard("old-quality", 10, 10, 10, 10),
            self.mod.PriceCard("old-fallback", 1, 1, 1, 1),
        ]
        self.mod.configure_user(
            self.db, self.user, 1000, 40, 30, 10,
            ["old-quality", "old-fallback"], old_cards, now=self.now,
        )
        seed = self.mod.route_request(
            self.db, self.user, "old-seed", "old-quality", 10, 0,
            request_payload_sha256="1" * 64, now=self.now,
        )
        self.settle("old-seed", seed.selected_model, 10, now=self.now)
        fallback = self.mod.route_request(
            self.db, self.user, "old-attempt", "old-quality", 1, 0,
            request_payload_sha256="2" * 64, logical_request_id="old-logical",
            reservation_ttl_seconds=1, now=self.now,
        )
        self.assertEqual("old-fallback", fallback.selected_model)
        later = self.now + dt.timedelta(seconds=1)
        self.mod.get_status(self.db, self.user, now=later)

        new_cards = [
            self.mod.PriceCard("new-quality", 5, 5, 5, 5),
            self.mod.PriceCard("new-fallback", 1, 1, 1, 1),
        ]
        self.mod.configure_user(
            self.db, self.user, 2000, 40, 30, 10,
            ["new-quality", "new-fallback"], new_cards, now=later,
        )
        self.settle(
            "old-attempt", "old-fallback", 1, status="failed", now=later
        )
        quality = self.mod.assess_quality(
            self.db, self.user, "old-attempt", "fail", "late failure", now=later
        )
        self.assertFalse(quality.upgrade_recommended)
        self.assertIsNone(quality.next_model)
        self.assertEqual(
            "needs-user-review",
            self.mod.get_final_result(
                self.db, self.user, "old-logical", now=later
            ).final_status,
        )
        reconfigured = self.mod.configure_user(
            self.db, self.user, 3000, 40, 30, 10,
            ["new-quality", "new-fallback"], new_cards, now=later,
        )
        self.assertEqual(3, reconfigured.config_version)

    def test_failed_user_requested_lower_model_does_not_auto_upgrade(self) -> None:
        self.configure()
        route = self.route("direct-lower", "balanced", 10)
        self.assertEqual("allow", route.action)
        self.settle("direct-lower", "balanced", 10, status="failed")
        quality = self.mod.assess_quality(
            self.db, self.user, "direct-lower", "fail", "task failed", now=self.now
        )
        self.assertFalse(quality.upgrade_recommended)
        self.assertFalse(quality.automatic_upgrade_authorized)
        self.assertIsNone(quality.next_model)

    def test_quality_event_is_idempotent_but_conflicts_fail(self) -> None:
        self.configure()
        route = self.route("quality", "balanced", 10)
        self.settle("quality", route.selected_model, 10)
        first = self.mod.assess_quality(
            self.db, self.user, "quality", "pass", "tests passed", now=self.now
        )
        replay = self.mod.assess_quality(
            self.db, self.user, "quality", "pass", "tests passed", now=self.now
        )
        self.assertTrue(first.recorded)
        self.assertFalse(replay.recorded)
        with self.assertRaisesRegex(ValueError, "different immutable"):
            self.mod.assess_quality(
                self.db, self.user, "quality", "fail", "tests failed", now=self.now
            )

    def test_final_result_counts_all_attempts_and_does_not_invent_token_savings(self) -> None:
        proof = self.mod.simulate_final_model()["final_result"]
        self.assertEqual("success", proof["final_status"])
        self.assertEqual("quality-model", proof["final_model"])
        self.assertEqual(2, len(proof["attempts"]))
        self.assertEqual(390, proof["total_tokens"])
        self.assertEqual(9200, proof["total_cost_nano_usd"])
        self.assertEqual(-2200, proof["estimated_cost_savings_nano_usd"])
        self.assertIsNone(proof["token_savings"])

    def test_final_result_reports_cache_write_and_reasoning_tokens(self) -> None:
        self.configure()
        route = self.route("token-details", "balanced", 10, output=5, logical="details")
        self.settle(
            "token-details", route.selected_model, 10, output_tokens=5,
            cached_tokens=2, cache_write_tokens=3, reasoning_tokens=4,
        )
        self.mod.assess_quality(
            self.db, self.user, "token-details", "pass", "verified", now=self.now
        )
        result = self.mod.get_final_result(self.db, self.user, "details")
        self.assertEqual(2, result.total_cached_tokens)
        self.assertEqual(3, result.total_cache_write_tokens)
        self.assertEqual(4, result.total_reasoning_tokens)

    def test_concurrent_routes_do_not_oversubscribe_hard_budget(self) -> None:
        self.configure(budget=100, maximum=99, restore=90, startup=99)

        def invoke(number: int):
            return self.route(f"parallel-{number}", "economy", 10)

        with ThreadPoolExecutor(max_workers=20) as executor:
            decisions = list(executor.map(invoke, range(20)))
        allowed = [item for item in decisions if item.selected_model is not None]
        blocked = [item for item in decisions if item.action == "block"]
        self.assertEqual(10, len(allowed))
        self.assertEqual(10, len(blocked))
        self.assertEqual(100, self.mod.get_status(self.db, self.user, now=self.now).reserved_nano_usd)
        with contextlib.closing(sqlite3.connect(self.db)) as connection, connection:
            self.assertEqual("ok", connection.execute("PRAGMA integrity_check").fetchone()[0])

    def test_concurrent_same_request_creates_one_reservation(self) -> None:
        self.configure()

        def invoke(_: int):
            return self.route("same-request", "balanced", 10)

        with ThreadPoolExecutor(max_workers=12) as executor:
            decisions = list(executor.map(invoke, range(12)))
        self.assertEqual(1, sum(item.reservation_created for item in decisions))
        self.assertEqual(1, sum(item.execution_authorized for item in decisions))
        self.assertEqual(1, sum(item.selected_model == "balanced" for item in decisions))
        self.assertEqual(11, sum(item.selected_model is None for item in decisions))
        self.assertEqual(10, self.mod.get_status(self.db, self.user, now=self.now).reserved_nano_usd)

    def test_writer_lock_uses_bounded_retry_then_succeeds(self) -> None:
        self.configure()
        holder = sqlite3.connect(self.db)
        self.addCleanup(holder.close)
        holder.execute("BEGIN IMMEDIATE")
        retry_sleeps: list[float] = []
        real_sleep = time.sleep

        def acquire() -> None:
            connection = self.mod._connect(self.db)
            try:
                self.mod._begin_immediate(connection)
                connection.rollback()
            finally:
                connection.close()

        def retry_sleep(delay: float) -> None:
            retry_sleeps.append(delay)
            real_sleep(delay)

        try:
            with (
                mock.patch.object(self.mod, "SQLITE_TRANSACTION_RETRY_MAX_SECONDS", 0.5),
                mock.patch.object(self.mod, "SQLITE_BUSY_RETRY_BASE_SECONDS", 0.005),
                mock.patch.object(self.mod, "SQLITE_BUSY_RETRY_MAX_SECONDS", 0.01),
                mock.patch.object(self.mod.time, "sleep", side_effect=retry_sleep),
                ThreadPoolExecutor(max_workers=1) as executor,
            ):
                future = executor.submit(acquire)
                real_sleep(0.08)
                holder.commit()
                future.result(timeout=2)
        finally:
            if holder.in_transaction:
                holder.rollback()
            holder.close()
        self.assertGreaterEqual(len(retry_sleeps), 1)

    def test_writer_lock_timeout_leaves_no_route_facts(self) -> None:
        self.configure()
        holder = sqlite3.connect(self.db)
        self.addCleanup(holder.close)
        holder.execute("BEGIN IMMEDIATE")
        try:
            with (
                mock.patch.object(self.mod, "SQLITE_TRANSACTION_RETRY_MAX_SECONDS", 0.03),
                mock.patch.object(self.mod, "SQLITE_BUSY_RETRY_BASE_SECONDS", 0.005),
                mock.patch.object(self.mod, "SQLITE_BUSY_RETRY_MAX_SECONDS", 0.01),
                self.assertRaisesRegex(sqlite3.OperationalError, "locked"),
            ):
                self.route("lock-timeout", "balanced", 10)
        finally:
            holder.rollback()
            holder.close()
        with contextlib.closing(sqlite3.connect(self.db)) as connection, connection:
            self.assertEqual(
                0,
                connection.execute(
                    "SELECT COUNT(*) FROM route_decisions WHERE request_payload_sha256=?",
                    (hashlib.sha256(b"test-payload:lock-timeout").hexdigest(),),
                ).fetchone()[0],
            )

    def test_non_lock_operational_error_is_not_retried(self) -> None:
        class BrokenConnection:
            calls = 0

            def execute(self, _statement: str):
                self.calls += 1
                raise sqlite3.OperationalError("no such table: broken")

            def rollback(self) -> None:
                raise AssertionError("non-lock errors must not be retried")

        connection = BrokenConnection()
        with (
            mock.patch.object(self.mod.time, "sleep") as sleeper,
            self.assertRaisesRegex(sqlite3.OperationalError, "no such table"),
        ):
            self.mod._begin_immediate(connection)
        self.assertEqual(1, connection.calls)
        sleeper.assert_not_called()

    def test_initialized_connect_does_not_take_the_writer_lock(self) -> None:
        self.configure()
        holder = sqlite3.connect(self.db)
        self.addCleanup(holder.close)
        holder.execute("BEGIN IMMEDIATE")
        try:
            connection = self.mod._connect(self.db)
            connection.close()
        finally:
            holder.rollback()
            holder.close()

    def test_cold_schema_writer_lock_retries_then_initializes_atomically(self) -> None:
        self.db.parent.mkdir(parents=True)
        holder = sqlite3.connect(self.db)
        self.addCleanup(holder.close)
        holder.execute("PRAGMA journal_mode=WAL")
        holder.execute("BEGIN IMMEDIATE")
        real_sleep = time.sleep

        def initialize() -> None:
            connection = self.mod._connect(self.db)
            connection.close()

        try:
            with (
                mock.patch.object(self.mod, "SQLITE_TRANSACTION_RETRY_MAX_SECONDS", 0.5),
                mock.patch.object(self.mod, "SQLITE_BUSY_RETRY_BASE_SECONDS", 0.005),
                mock.patch.object(self.mod, "SQLITE_BUSY_RETRY_MAX_SECONDS", 0.01),
                ThreadPoolExecutor(max_workers=1) as executor,
            ):
                future = executor.submit(initialize)
                real_sleep(0.08)
                holder.commit()
                future.result(timeout=2)
        finally:
            if holder.in_transaction:
                holder.rollback()
            holder.close()
        connection = self.mod._connect(self.db)
        try:
            self.assertEqual(
                self.mod.SCHEMA_VERSION,
                connection.execute("PRAGMA user_version").fetchone()[0],
            )
            self.mod._validate_schema_contract(connection)
        finally:
            connection.close()

    def test_cold_schema_lock_timeout_leaves_no_partial_schema(self) -> None:
        self.db.parent.mkdir(parents=True)
        holder = sqlite3.connect(self.db)
        self.addCleanup(holder.close)
        holder.execute("PRAGMA journal_mode=WAL")
        holder.execute("BEGIN IMMEDIATE")
        try:
            with (
                mock.patch.object(self.mod, "SQLITE_TRANSACTION_RETRY_MAX_SECONDS", 0.03),
                mock.patch.object(self.mod, "SQLITE_BUSY_RETRY_BASE_SECONDS", 0.005),
                mock.patch.object(self.mod, "SQLITE_BUSY_RETRY_MAX_SECONDS", 0.01),
                self.assertRaisesRegex(sqlite3.OperationalError, "locked"),
            ):
                self.mod._connect(self.db)
        finally:
            holder.rollback()
            holder.close()
        with contextlib.closing(sqlite3.connect(self.db)) as connection, connection:
            self.assertEqual(0, connection.execute("PRAGMA user_version").fetchone()[0])
            self.assertEqual(
                0,
                connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
                ).fetchone()[0],
            )

    def test_cold_delete_schema_exclusive_lock_is_retried(self) -> None:
        self.db.parent.mkdir(parents=True)
        holder = sqlite3.connect(self.db)
        self.addCleanup(holder.close)
        holder.execute("BEGIN EXCLUSIVE")
        real_sleep = time.sleep

        def initialize() -> None:
            connection = self.mod._connect(self.db)
            connection.close()

        try:
            with (
                mock.patch.object(self.mod, "SQLITE_TRANSACTION_RETRY_MAX_SECONDS", 0.5),
                mock.patch.object(self.mod, "SQLITE_BUSY_RETRY_BASE_SECONDS", 0.005),
                mock.patch.object(self.mod, "SQLITE_BUSY_RETRY_MAX_SECONDS", 0.01),
                ThreadPoolExecutor(max_workers=1) as executor,
            ):
                future = executor.submit(initialize)
                real_sleep(0.08)
                holder.commit()
                future.result(timeout=2)
        finally:
            if holder.in_transaction:
                holder.rollback()
            holder.close()
        with contextlib.closing(sqlite3.connect(self.db)) as connection, connection:
            self.assertEqual(
                self.mod.SCHEMA_VERSION,
                connection.execute("PRAGMA user_version").fetchone()[0],
            )
            self.assertEqual("wal", connection.execute("PRAGMA journal_mode").fetchone()[0])

    def test_cold_delete_exclusive_timeout_leaves_no_partial_schema(self) -> None:
        self.db.parent.mkdir(parents=True)
        holder = sqlite3.connect(self.db)
        self.addCleanup(holder.close)
        holder.execute("BEGIN EXCLUSIVE")
        try:
            with (
                mock.patch.object(self.mod, "SQLITE_TRANSACTION_RETRY_MAX_SECONDS", 0.03),
                mock.patch.object(self.mod, "SQLITE_BUSY_RETRY_BASE_SECONDS", 0.005),
                mock.patch.object(self.mod, "SQLITE_BUSY_RETRY_MAX_SECONDS", 0.01),
                self.assertRaisesRegex(sqlite3.OperationalError, "locked"),
            ):
                self.mod._connect(self.db)
        finally:
            holder.rollback()
            holder.close()
        with contextlib.closing(sqlite3.connect(self.db)) as connection, connection:
            self.assertEqual(0, connection.execute("PRAGMA user_version").fetchone()[0])
            self.assertEqual(
                0,
                connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
                ).fetchone()[0],
            )

    def test_business_transaction_rechecks_schema_under_writer_lock(self) -> None:
        self.configure()
        original_require_wal = self.mod._require_wal
        injected = False

        def advance_schema(connection) -> None:
            nonlocal injected
            original_require_wal(connection)
            if injected:
                return
            injected = True
            with contextlib.closing(sqlite3.connect(self.db)) as competitor, competitor:
                competitor.execute(f"PRAGMA user_version={self.mod.SCHEMA_VERSION + 1}")

        with (
            mock.patch.object(self.mod, "_require_wal", side_effect=advance_schema),
            self.assertRaisesRegex(ValueError, "newer than supported"),
        ):
            self.route("future-schema-race", "balanced", 10)
        with contextlib.closing(sqlite3.connect(self.db)) as connection, connection:
            self.assertEqual(
                0,
                connection.execute(
                    "SELECT COUNT(*) FROM route_decisions WHERE request_payload_sha256=?",
                    (hashlib.sha256(b"test-payload:future-schema-race").hexdigest(),),
                ).fetchone()[0],
            )

    def test_cold_schema_rechecks_future_version_after_writer_lock(self) -> None:
        self.db.parent.mkdir(parents=True)
        sqlite3.connect(self.db).close()
        original_require_wal = self.mod._require_wal
        injected = False

        def advance_schema(connection) -> None:
            nonlocal injected
            original_require_wal(connection)
            if injected:
                return
            injected = True
            with contextlib.closing(sqlite3.connect(self.db)) as competitor, competitor:
                competitor.execute("CREATE TABLE future_marker (value TEXT)")
                competitor.execute(f"PRAGMA user_version={self.mod.SCHEMA_VERSION + 1}")

        with (
            mock.patch.object(self.mod, "_require_wal", side_effect=advance_schema),
            self.assertRaisesRegex(ValueError, "newer than supported"),
        ):
            self.mod._connect(self.db)
        with contextlib.closing(sqlite3.connect(self.db)) as connection, connection:
            self.assertEqual(
                self.mod.SCHEMA_VERSION + 1,
                connection.execute("PRAGMA user_version").fetchone()[0],
            )
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertEqual({"future_marker"}, tables)

    def test_cycle_rollover_preserves_old_evidence_but_resets_current_status(self) -> None:
        self.configure(window="monthly")
        route = self.route("august", "balanced", 10)
        self.settle("august", route.selected_model, 10)
        september = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
        status = self.mod.get_status(self.db, self.user, now=september)
        self.assertEqual(0, status.committed_nano_usd)
        with contextlib.closing(sqlite3.connect(self.db)) as connection, connection:
            self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0])

    def test_fallback_state_resets_at_cycle_boundary(self) -> None:
        self.configure(window="monthly", startup=10)
        seed = self.route("seed", "quality", 100)
        self.settle("seed", seed.selected_model, 100)
        fallback = self.route("fallback", "quality", 1)
        self.assertEqual("fallback", fallback.state_after)
        self.mod.release_reservation(self.db, self.user, "fallback", now=self.now)
        september = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
        new_cycle = self.route("september", "quality", 100, now=september)
        self.assertEqual("normal", new_cycle.state_before)
        self.assertEqual("quality", new_cycle.selected_model)

    def test_raw_user_and_request_identifiers_are_not_stored(self) -> None:
        self.configure()
        route = self.route("private-request-id", "balanced", 10)
        self.settle("private-request-id", route.selected_model, 10)
        raw = self.db.read_bytes()
        self.assertNotIn(self.user.encode(), raw)
        self.assertNotIn(b"private-request-id", raw)

    def test_symlink_database_is_rejected(self) -> None:
        target = Path(self.temp.name) / "target.sqlite3"
        target.write_bytes(b"")
        link = Path(self.temp.name) / "link.sqlite3"
        try:
            link.symlink_to(target)
        except OSError:
            self.skipTest("symlinks unavailable")
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.mod._connect(link)

    def test_dangling_symlink_database_is_rejected(self) -> None:
        link = Path(self.temp.name) / "dangling.sqlite3"
        try:
            link.symlink_to(Path(self.temp.name) / "does-not-exist.sqlite3")
        except OSError:
            self.skipTest("symlinks unavailable")
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.mod._connect(link)

    @unittest.skipIf(os.name == "nt", "POSIX permission bits are not portable to Windows")
    def test_database_and_wal_files_are_private(self) -> None:
        connection = self.mod._connect(self.db)
        try:
            for path in (self.db, Path(f"{self.db}-wal"), Path(f"{self.db}-shm")):
                self.assertTrue(path.exists())
                self.assertEqual(0, stat.S_IMODE(path.stat().st_mode) & 0o077)
        finally:
            connection.close()

    def test_concurrent_first_connect_creates_one_complete_schema(self) -> None:
        workers = 32
        barrier = threading.Barrier(workers)

        def invoke(_: int) -> int:
            barrier.wait()
            connection = self.mod._connect(self.db)
            try:
                return int(connection.execute("PRAGMA user_version").fetchone()[0])
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=workers) as executor:
            versions = list(executor.map(invoke, range(workers)))
        self.assertEqual([self.mod.SCHEMA_VERSION] * workers, versions)
        connection = self.mod._connect(self.db)
        try:
            self.mod._validate_schema_contract(connection)
            self.assertEqual(
                "ok", connection.execute("PRAGMA integrity_check").fetchone()[0]
            )
        finally:
            connection.close()

    def test_database_schema_version_and_exact_money_types_are_enforced(self) -> None:
        self.configure()
        with contextlib.closing(sqlite3.connect(self.db)) as connection, connection:
            self.assertEqual(
                self.mod.SCHEMA_VERSION,
                connection.execute("PRAGMA user_version").fetchone()[0],
            )
            route_types = {
                row[1]: row[2].upper()
                for row in connection.execute("PRAGMA table_info(route_decisions)")
            }
            usage_types = {
                row[1]: row[2].upper()
                for row in connection.execute("PRAGMA table_info(usage_events)")
            }
        self.assertEqual("TEXT", route_types["requested_model_cost_nano_usd"])
        for name in (
            "estimated_cost_nano_usd", "actual_cost_nano_usd",
            "reservation_variance_nano_usd", "estimated_savings_nano_usd",
        ):
            self.assertEqual("TEXT", usage_types[name])

        with contextlib.closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("PRAGMA user_version=0")
        upgraded = self.mod._connect(self.db)
        upgraded.close()
        with contextlib.closing(sqlite3.connect(self.db)) as connection, connection:
            self.assertEqual(
                self.mod.SCHEMA_VERSION,
                connection.execute("PRAGMA user_version").fetchone()[0],
            )

        with contextlib.closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute(f"PRAGMA user_version={self.mod.SCHEMA_VERSION + 1}")
        with self.assertRaisesRegex(ValueError, "newer than supported"):
            self.mod._connect(self.db)

    def test_schema_preflight_rejects_incompatible_files_before_ddl(self) -> None:
        future = Path(self.temp.name) / "future.sqlite3"
        with contextlib.closing(sqlite3.connect(future)) as connection, connection:
            connection.execute("CREATE TABLE future_marker (value TEXT)")
            connection.execute(f"PRAGMA user_version={self.mod.SCHEMA_VERSION + 1}")
        with self.assertRaisesRegex(ValueError, "newer than supported"):
            self.mod._connect(future)
        with contextlib.closing(sqlite3.connect(future)) as connection, connection:
            names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertEqual({"future_marker"}, names)

        incompatible = Path(self.temp.name) / "incompatible.sqlite3"
        with contextlib.closing(sqlite3.connect(incompatible)) as connection, connection:
            connection.execute(
                "CREATE TABLE budget_users "
                "(user_hash TEXT PRIMARY KEY, period_budget_nano_usd REAL)"
            )
            connection.execute(f"PRAGMA user_version={self.mod.SCHEMA_VERSION}")
        with self.assertRaisesRegex(ValueError, "columns/types"):
            self.mod._connect(incompatible)
        with contextlib.closing(sqlite3.connect(incompatible)) as connection, connection:
            names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertEqual({"budget_users"}, names)

        uppercase = Path(self.temp.name) / "uppercase.sqlite3"
        with contextlib.closing(sqlite3.connect(uppercase)) as connection, connection:
            connection.execute("CREATE TABLE BUDGET_USERS (value REAL)")
        with self.assertRaisesRegex(ValueError, "budget_users object"):
            self.mod._connect(uppercase)
        with contextlib.closing(sqlite3.connect(uppercase)) as connection, connection:
            objects = connection.execute(
                "SELECT type, name FROM sqlite_master "
                "WHERE type IN ('table', 'view') ORDER BY name"
            ).fetchall()
            journal = connection.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual([("table", "BUDGET_USERS")], objects)
        self.assertNotEqual("wal", journal)

        view = Path(self.temp.name) / "view.sqlite3"
        with contextlib.closing(sqlite3.connect(view)) as connection, connection:
            connection.execute("CREATE VIEW budget_users AS SELECT 1 AS value")
        with self.assertRaisesRegex(ValueError, "budget_users object"):
            self.mod._connect(view)
        with contextlib.closing(sqlite3.connect(view)) as connection, connection:
            objects = connection.execute(
                "SELECT type, name FROM sqlite_master "
                "WHERE type IN ('table', 'view') ORDER BY name"
            ).fetchall()
            journal = connection.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual([("view", "budget_users")], objects)
        self.assertNotEqual("wal", journal)

    def test_schema_contract_rejects_relaxed_reservation_status_check(self) -> None:
        self.configure()
        with contextlib.closing(sqlite3.connect(self.db)) as connection, connection:
            sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='reservations'"
            ).fetchone()[0]
            replaced = sql.replace(
                "CHECK (status IN ('active', 'settled', 'released', 'expired'))",
                "CHECK (status IN ('active', 'settled', 'released', 'expired', 'pending'))",
            )
            self.assertNotEqual(sql, replaced)
            schema_version = connection.execute("PRAGMA schema_version").fetchone()[0]
            connection.execute("PRAGMA writable_schema=ON")
            connection.execute(
                "UPDATE sqlite_master SET sql=? WHERE type='table' AND name='reservations'",
                (replaced,),
            )
            connection.execute("PRAGMA writable_schema=OFF")
            connection.execute(f"PRAGMA schema_version={schema_version + 1}")
        with self.assertRaisesRegex(ValueError, "check constraints"):
            self.mod._connect(self.db)

    def test_unknown_reservation_state_is_conservatively_accounted(self) -> None:
        self.configure(budget=100, maximum=99, restore=90, startup=99)
        first = self.route("legacy-pending", "economy", 80)
        with contextlib.closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("PRAGMA ignore_check_constraints=ON")
            connection.execute(
                "UPDATE reservations SET status='pending' WHERE request_hash=?",
                (first.request_hash,),
            )
        status = self.mod.get_status(self.db, self.user, now=self.now)
        self.assertEqual(80, status.reserved_nano_usd)
        blocked = self.route("after-legacy-pending", "economy", 30)
        self.assertEqual("block", blocked.action)
        self.assertFalse(blocked.execution_authorized)
        with self.assertRaisesRegex(ValueError, "active or nonterminal"):
            self.configure(budget=200)

    def test_schema_contract_rejects_unexpected_triggers(self) -> None:
        self.configure()
        with contextlib.closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute(
                "CREATE TRIGGER mutate_budget AFTER UPDATE ON budget_users "
                "BEGIN SELECT 1; END"
            )
        with self.assertRaisesRegex(ValueError, "unexpected triggers"):
            self.mod._connect(self.db)

    def test_cli_simulation_emits_verified_final_model_json(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = self.mod.main(["simulate", "--format", "json"])
        self.assertEqual(0, result)
        data = json.loads(output.getvalue())
        self.assertEqual("downgrade", data["first_route"]["action"])
        self.assertEqual("quality-upgrade", data["second_route"]["action"])
        self.assertEqual("quality-model", data["final_result"]["final_model"])
        self.assertEqual("9200", data["final_result"]["total_cost_nano_usd"])
        self.assertIsInstance(data["first_usage"]["actual_cost_nano_usd"], str)

    def test_cli_block_returns_distinct_exit_code(self) -> None:
        configure_out = io.StringIO()
        with contextlib.redirect_stdout(configure_out):
            self.assertEqual(0, self.mod.main([
                "configure", "--db", str(self.db), "--user-key", self.user,
                "--period-budget-usd", "0.000000100", "--preferred-share", "99",
                "--restore-share", "90", "--startup-allowance", "99",
                "--model", "quality", "--model", "economy",
                "--price", "quality:0.001:0.001:0.001:0.001",
                "--price", "economy:0.001:0.001:0.001:0.001",
            ]))
        route_out = io.StringIO()
        with contextlib.redirect_stdout(route_out):
            code = self.mod.main([
                "route", "--db", str(self.db), "--user-key", self.user,
                "--request-id", "blocked-cli", "--requested-model", "economy",
                "--request-payload-sha256", "a" * 64,
                "--projected-input-tokens", "101", "--projected-output-tokens", "0",
            ])
        self.assertEqual(3, code)
        self.assertEqual("block", json.loads(route_out.getvalue())["action"])

    def test_extreme_cli_numbers_return_a_clean_error(self) -> None:
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            code = self.mod.main([
                "configure", "--db", str(self.db), "--user-key", self.user,
                "--period-budget-usd", "1e999999999", "--preferred-share", "40",
                "--restore-share", "30", "--model", "quality", "--model", "economy",
                "--price", "quality:1:1:1:1", "--price", "economy:1:1:1:1",
            ])
        self.assertEqual(2, code)
        self.assertIn("error:", error.getvalue())

    def test_deeply_nested_response_json_returns_a_clean_cli_error(self) -> None:
        response = Path(self.temp.name) / "deep-response.json"
        response.write_text("[" * 10_000 + "0" + "]" * 10_000, encoding="utf-8")
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            code = self.mod.main([
                "settle", "--db", str(self.db), "--user-key", self.user,
                "--request-id", "deep", "--model", "economy",
                "--response", str(response),
            ])
        self.assertEqual(2, code)
        self.assertIn("error:", error.getvalue())

    def test_provider_qualified_model_price_spec_can_contain_colons(self) -> None:
        card = self.mod._parse_price_spec("provider:family:model:1:0.1:1.25:4")
        self.assertEqual("provider:family:model", card.model)


if __name__ == "__main__":
    unittest.main()
