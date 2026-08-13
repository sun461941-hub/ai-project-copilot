#!/usr/bin/env python3
"""Run deterministic local context-efficiency benchmarks for AI Project Copilot v2."""

from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "ai-project-copilot" / "scripts"


def load(name: str):
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"bench_{name}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ACCEL = load("context_accelerator")
REPO_CONTEXT = load("repo_context")
COMPACTOR = load("tool_output_compactor")
CACHE = load("evidence_cache")


def build_repo(root: Path, count: int) -> None:
    (root / "src" / "auth").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "docs").mkdir()
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "AGENTS.md").write_text("Use targeted tests. Read docs only when relevant.\n", encoding="utf-8")
    (root / "src" / "AGENTS.md").write_text("Source rules.\n", encoding="utf-8")
    (root / "README.md").write_text("# Synthetic benchmark repo\n", encoding="utf-8")
    (root / "SECURITY.md").write_text("# Security\n", encoding="utf-8")
    (root / "CONTRIBUTING.md").write_text("# Contributing\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='bench'\n", encoding="utf-8")
    (root / "src" / "cli.py").write_text("def main(): return 0\n", encoding="utf-8")
    (root / "tests" / "test_cli.py").write_text("def test_cli(): assert True\n", encoding="utf-8")
    (root / "src" / "auth" / "session.py").write_text("def session(): return None\n", encoding="utf-8")
    (root / "tests" / "test_session.py").write_text("def test_session(): assert True\n", encoding="utf-8")
    (root / ".github" / "workflows" / "ci.yml").write_text("name: CI\n", encoding="utf-8")
    (root / ".github" / "workflows" / "release.yml").write_text("name: Release\n", encoding="utf-8")
    existing = sum(1 for p in root.rglob("*") if p.is_file())
    remaining = max(0, count - existing)
    for i in range(remaining):
        bucket = root / ("docs" if i % 3 == 0 else "src")
        suffix = ".md" if i % 3 == 0 else ".py"
        (bucket / f"filler_{i:05}{suffix}").write_text("x\n", encoding="utf-8")


def all_path_chars(root: Path) -> tuple[int, int]:
    rels = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    return len(rels), sum(len(x) + 1 for x in rels)


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * p)))
    return ordered[index]


def run(repeats: int) -> dict[str, object]:
    spec = json.loads((Path(__file__).with_name("context_efficiency_cases.json")).read_text(encoding="utf-8"))
    cases_out: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory() as temp:
        base = Path(temp)
        for case in spec["cases"]:
            repo = base / case["id"]
            repo.mkdir()
            build_repo(repo, int(case["files"]))
            file_count, baseline_chars = all_path_chars(repo)
            timings: list[float] = []
            baseline_timings: list[float] = []
            packet = None
            for _ in range(repeats):
                started = time.perf_counter()
                REPO_CONTEXT.build_context(repo, case["task"], max_files=file_count + 10)
                baseline_timings.append((time.perf_counter() - started) * 1000)
                started = time.perf_counter()
                packet = ACCEL.compile_context(repo, case["task"], case["changed_files"], max_files=file_count + 10)
                timings.append((time.perf_counter() - started) * 1000)
            assert packet is not None
            selected_chars = sum(len(x) + 1 for x in packet.files_to_read)
            cases_out.append({
                "id": case["id"],
                "expected_mode": case["expected_mode"],
                "actual_mode": packet.mode,
                "files_in_repo": file_count,
                "selected_files": len(packet.files_to_read),
                "selected_file_ratio": round(len(packet.files_to_read) / file_count, 6),
                "naive_all_path_chars": baseline_chars,
                "selected_path_chars": selected_chars,
                "path_char_reduction_vs_all_paths": round(1 - selected_chars / baseline_chars, 6),
                "baseline_repo_map_runtime_ms_median": round(statistics.median(baseline_timings), 3),
                "accelerator_runtime_ms_median": round(statistics.median(timings), 3),
                "accelerator_runtime_ms_p95": round(percentile(timings, 0.95), 3),
                "reconnaissance_speed_ratio_vs_full_map": round(statistics.median(baseline_timings) / max(0.000001, statistics.median(timings)), 3),
                "runtime_metric_note": "local deterministic reconnaissance only; not Codex model generation speed",
                "repeats": repeats,
            })

        noisy = [f"tests/test_bulk.py::test_{i} PASSED" for i in range(5000)]
        noisy += [
            "FAILED tests/test_auth.py::test_expired - AssertionError: expected 401",
            "FAILED tests/test_release.py::test_gate - AssertionError: release must be blocked",
            "2 failed, 5000 passed in 22.0s",
        ]
        raw_log = "\n".join(noisy) + "\n"
        compact = COMPACTOR.compact_text(raw_log, 80)

        cache_repo = base / "cache"
        cache_repo.mkdir()
        (cache_repo / "source.py").write_text("value = 1\n", encoding="utf-8")
        cache_path = Path(".aipc/cache/evidence.json")
        CACHE.record_entry(cache_repo, cache_path, "unit", "pytest", ["source.py"], "pass", "1 passed")
        hit = CACHE.check_entry(cache_repo, cache_path, "unit", "pytest", ["source.py"])
        critical = CACHE.check_entry(cache_repo, cache_path, "unit", "pytest", ["source.py"], critical=True)
        (cache_repo / "source.py").write_text("value = 2\n", encoding="utf-8")
        changed = CACHE.check_entry(cache_repo, cache_path, "unit", "pytest", ["source.py"])

    return {
        "benchmark_version": "2.0.0",
        "environment": {"python": sys.version.split()[0], "platform": sys.platform},
        "metric_policy": spec["metric_policy"],
        "context_cases": cases_out,
        "tool_output_compaction": {
            "raw_lines": compact.raw_lines,
            "raw_chars": compact.raw_chars,
            "compact_lines": compact.compact_lines,
            "compact_chars": compact.compact_chars,
            "char_reduction": compact.char_reduction,
            "failure_markers_preserved": all(marker in compact.text for marker in ("test_expired", "test_gate", "2 failed, 5000 passed")),
            "raw_sha256": compact.raw_sha256,
        },
        "evidence_cache": {
            "exact_fingerprint_hit": hit.reusable,
            "critical_gate_reused": critical.reusable,
            "changed_input_reused": changed.reusable,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be >= 1")
    data = run(args.repeats)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
