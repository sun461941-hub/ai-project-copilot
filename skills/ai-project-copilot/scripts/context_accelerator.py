#!/usr/bin/env python3
"""Compile a small, task-specific repository context packet for coding agents."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from repo_context import LANGUAGE_SUFFIXES, MANIFEST_NAMES, build_context  # noqa: E402
from token_governor import BudgetPlan, plan_task  # noqa: E402

INSTRUCTION_NAMES = ("AGENTS.override.md", "AGENTS.md")
MANIFEST_PRIORITY = (
    "pyproject.toml", "package.json", "Cargo.toml", "go.mod", "pom.xml",
    "build.gradle.kts", "build.gradle", "requirements.txt",
)


def _normalize_rel(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    # Remove an explicit current-directory prefix without treating ``./`` as
    # a bag of characters.  ``str.lstrip("./")`` corrupts legitimate hidden
    # paths such as ``.github/workflows/ci.yml`` and ``.env``.
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_rel(root: Path, raw: str) -> str:
    original = raw.replace("\\", "/").strip()
    if not original:
        raise ValueError("changed file path cannot be empty")
    parsed = PurePosixPath(original)
    if parsed.is_absolute() or ".." in parsed.parts:
        raise ValueError(f"changed file escapes repository: {raw}")
    text = parsed.as_posix()
    candidate = (root / text).resolve(strict=False)
    if not _within(candidate, root):
        raise ValueError(f"changed file escapes repository: {raw}")
    return candidate.relative_to(root).as_posix()


def _git_changed_files(root: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain=v1", "-z"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=4,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    data = result.stdout.decode("utf-8", errors="replace")
    items = data.split("\0")
    changed: list[str] = []
    index = 0
    while index < len(items):
        item = items[index]
        index += 1
        if len(item) < 4:
            continue
        status = item[:2]
        path = item[3:]
        if path:
            changed.append(_normalize_rel(path))
        if status and any(code in status for code in ("R", "C")) and index < len(items):
            previous_path = items[index]
            index += 1
            if previous_path:
                changed.append(_normalize_rel(previous_path))
    return sorted(dict.fromkeys(changed))


def _ancestors(rel: str) -> Iterable[PurePosixPath]:
    path = PurePosixPath(rel)
    parent = path.parent
    lineage = [PurePosixPath(".")]
    if str(parent) != ".":
        current = PurePosixPath()
        for part in parent.parts:
            current = current / part
            lineage.append(current)
    return lineage


def _governing_instructions(root: Path, rels: list[str]) -> list[str]:
    found: list[str] = []
    targets = rels or [""]
    for rel in targets:
        for parent in _ancestors(rel):
            base = root if str(parent) == "." else root / parent.as_posix()
            for name in INSTRUCTION_NAMES:
                path = base / name
                try:
                    if path.is_file() and not path.is_symlink():
                        found.append(path.relative_to(root).as_posix())
                except OSError:
                    continue
    return sorted(dict.fromkeys(found), key=lambda x: (x.count("/"), x.casefold()))


def _related_tests(changed: list[str], tests: list[str], limit: int) -> list[str]:
    scored: list[tuple[int, str]] = []
    for test in tests:
        test_path = PurePosixPath(test)
        low_test = test.casefold()
        score = 0
        for changed_path in changed:
            cp = PurePosixPath(changed_path)
            stem = cp.stem.casefold()
            if stem and stem in test_path.stem.casefold():
                score = max(score, 10)
            if cp.parent == test_path.parent:
                score = max(score, 6)
            if cp.parent.name and cp.parent.name.casefold() in low_test:
                score = max(score, 4)
        if score:
            scored.append((score, test))
    scored.sort(key=lambda item: (-item[0], item[1].casefold()))
    return [path for _, path in scored[:limit]]


def _manifest_sort(path: str) -> tuple[int, str]:
    name = PurePosixPath(path).name
    try:
        rank = MANIFEST_PRIORITY.index(name)
    except ValueError:
        rank = len(MANIFEST_PRIORITY)
    return rank, path.casefold()


def _task_paths(root: Path, task: str) -> list[str]:
    import re
    candidates = re.findall(r"(?<![A-Za-z0-9_.-])((?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.[A-Za-z0-9]+)", task)
    found: list[str] = []
    for raw in candidates:
        try:
            rel = _safe_rel(root, raw)
        except ValueError:
            continue
        path = root / rel
        try:
            if path.is_file() and not path.is_symlink():
                found.append(rel)
        except OSError:
            continue
    return list(dict.fromkeys(found))


def _targeted_tests(root: Path, changed: list[str], limit: int = 8) -> list[str]:
    stems = {PurePosixPath(path).stem.casefold() for path in changed if PurePosixPath(path).suffix.casefold() not in {".md", ".txt", ".rst", ".adoc"}}
    stems.discard("")
    if not stems:
        return []
    roots = [root / name for name in ("tests", "test", "spec", "specs") if (root / name).is_dir()]
    matches: list[str] = []
    for test_root in roots:
        try:
            for path in test_root.rglob("*"):
                if len(matches) >= limit:
                    return sorted(dict.fromkeys(matches))
                if path.is_symlink() or not path.is_file():
                    continue
                low = path.name.casefold()
                if any(stem in low for stem in stems):
                    matches.append(path.relative_to(root).as_posix())
        except OSError:
            continue
    return sorted(dict.fromkeys(matches))



def _readable_paths(root: Path, paths: list[str]) -> tuple[list[str], list[str]]:
    readable: list[str] = []
    unavailable: list[str] = []
    for rel in paths:
        path = root / rel
        try:
            if path.is_file() and not path.is_symlink():
                readable.append(rel)
            else:
                unavailable.append(rel)
        except OSError:
            unavailable.append(rel)
    return readable, unavailable


@dataclass(frozen=True)
class ContextPacket:
    mode: str
    budget: dict[str, object]
    changed_files: list[str]
    governing_instructions: list[str]
    files_to_read: list[str]
    tests_to_consider: list[str]
    manifests: list[str]
    ci: list[str]
    languages: list[dict[str, object]]
    scan: dict[str, object]
    context_efficiency: dict[str, object]
    notes: list[str]

def _compile_sparse_fast(root: Path, task: str, changed: list[str], budget: BudgetPlan) -> ContextPacket:
    task_paths = _task_paths(root, task)
    if not changed:
        changed = task_paths
    if not changed and ("readme" in task.casefold() or "文档" in task or "错别字" in task):
        for name in ("README.md", "README.rst", "README.txt"):
            if (root / name).is_file():
                changed = [name]
                break
    instructions = _governing_instructions(root, changed or task_paths)
    tests = _targeted_tests(root, changed, limit=6)
    manifests = [name for name in MANIFEST_PRIORITY if (root / name).is_file()]
    selected = list(dict.fromkeys(changed + instructions + tests + manifests[:1]))[: max(budget.max_focus_files, len(changed) + len(instructions))]
    selected, unavailable = _readable_paths(root, selected)
    languages: list[dict[str, object]] = []
    counts: dict[str, int] = {}
    for rel in changed:
        lang = LANGUAGE_SUFFIXES.get(PurePosixPath(rel).suffix.casefold())
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    languages = [{"name": name, "files": count} for name, count in sorted(counts.items())]
    selected_chars = sum(len(path) + 1 for path in selected)
    return ContextPacket(
        mode=budget.mode,
        budget=asdict(budget),
        changed_files=changed,
        governing_instructions=instructions,
        files_to_read=selected,
        tests_to_consider=tests,
        manifests=manifests[:1],
        ci=[],
        languages=languages,
        scan={
            "scan_mode": "sparse-fast",
            "files_scanned": 0,
            "scan_warnings": [],
            "focus_candidates": len(selected),
        },
        context_efficiency={
            "selected_files": len(selected),
            "selected_vs_scanned_ratio": 1.0 if selected else 0.0,
            "high_signal_path_chars": selected_chars,
            "selected_path_chars": selected_chars,
            "high_signal_path_char_reduction": 0.0,
            "metric_note": "FAST sparse mode avoids a full repository scan; path reduction versus the whole repo is intentionally not estimated here",
        },
        notes=[
            "sparse FAST path avoided full-repository reconnaissance",
            "expand context only if the edit or validation reveals broader risk",
        ] + (["changed/unavailable paths require diff/history evidence: " + ", ".join(unavailable[:8])] if unavailable else []),
    )


def compile_context(
    root: Path,
    task: str,
    changed_files: list[str] | None = None,
    max_files: int = 5000,
    use_git_status: bool = False,
) -> ContextPacket:
    root = root.expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"repository directory does not exist: {root}")
    if max_files < 1:
        raise ValueError("max_files must be at least 1")

    explicit = [_safe_rel(root, item) for item in (changed_files or [])]
    git_changed = _git_changed_files(root) if use_git_status and not explicit else []
    changed = sorted(dict.fromkeys(explicit or git_changed))
    budget: BudgetPlan = plan_task(task, changed)
    if budget.mode == "FAST":
        return _compile_sparse_fast(root, task, changed, budget)
    context = build_context(root, task, max_files)

    focus_cap = budget.max_focus_files
    instructions = _governing_instructions(root, changed or [item["path"] for item in context.focus_files[:4]])
    minimum_cap = len(dict.fromkeys(changed + instructions))
    focus_cap = max(focus_cap, minimum_cap)
    selected: list[str] = []
    selected.extend(changed)
    selected.extend(instructions)
    selected.extend(str(item["path"]) for item in context.focus_files[:focus_cap])

    related_tests = _related_tests(changed, context.tests, max(2, focus_cap // 4)) if changed else context.tests[: max(2, focus_cap // 5)]
    selected.extend(related_tests)

    manifests = sorted(context.manifests, key=_manifest_sort)
    if budget.mode == "FAST":
        selected.extend(manifests[:1])
    elif budget.mode == "BALANCED":
        selected.extend(manifests[:3])
        selected.extend(context.ci[:1])
    else:
        selected.extend(manifests[:6])
        selected.extend(context.ci[:6])
        selected.extend(context.governance[:6])

    selected = [item for item in dict.fromkeys(selected) if item]
    if len(selected) > focus_cap:
        # Changed files and governing instructions stay first by construction.
        selected = selected[:focus_cap]
    selected, unavailable = _readable_paths(root, selected)

    all_path_chars = sum(len(path) + 1 for path in (
        context.manifests + context.entrypoints + context.tests + context.ci + context.docs + context.governance
    ))
    selected_path_chars = sum(len(path) + 1 for path in selected)
    reduction = 0.0 if all_path_chars <= 0 else max(0.0, 1.0 - selected_path_chars / all_path_chars)
    notes = list(context.warnings)
    if not changed:
        notes.append("no changed-file set supplied; focus is task/path based")
    if budget.mode == "FAST":
        notes.append("fast path intentionally avoids broad CI/security/release context unless the task requires it")
    if budget.mode == "DEEP":
        notes.append("deep path still caps initial context; expand only when evidence requires it")
    if unavailable:
        notes.append("changed/unavailable paths require diff/history evidence: " + ", ".join(unavailable[:8]))

    return ContextPacket(
        mode=budget.mode,
        budget=asdict(budget),
        changed_files=changed,
        governing_instructions=instructions,
        files_to_read=selected,
        tests_to_consider=related_tests,
        manifests=manifests[:6],
        ci=context.ci[:6],
        languages=context.languages[:6],
        scan={
            "scan_mode": "full-bounded",
            "files_scanned": context.file_count,
            "scan_warnings": context.warnings,
            "focus_candidates": len(context.focus_files),
        },
        context_efficiency={
            "selected_files": len(selected),
            "selected_vs_scanned_ratio": round(len(selected) / max(1, context.file_count, len(selected)), 6),
            "high_signal_path_chars": all_path_chars,
            "selected_path_chars": selected_path_chars,
            "high_signal_path_char_reduction": round(reduction, 4),
            "metric_note": "path-character metrics are a deterministic context-size proxy, not actual model token usage",
        },
        notes=notes,
    )


def markdown(packet: ContextPacket) -> str:
    eff = packet.context_efficiency
    lines = [
        "# Context Accelerator packet",
        "",
        f"- Mode: **{packet.mode}**",
        f"- Files scanned: **{packet.scan['files_scanned']}**",
        f"- Files selected for initial read: **{eff['selected_files']}**",
        f"- Selected/scanned ratio: **{eff['selected_vs_scanned_ratio']:.4%}**",
        f"- High-signal path-character reduction: **{eff['high_signal_path_char_reduction']:.2%}**",
        "",
        "## Governing instructions",
    ]
    lines.extend(f"- `{path}`" for path in packet.governing_instructions) or lines.append("- None detected")
    lines.extend(["", "## Initial files to read"])
    lines.extend(f"- `{path}`" for path in packet.files_to_read) or lines.append("- None selected")
    lines.extend(["", "## Tests to consider"])
    lines.extend(f"- `{path}`" for path in packet.tests_to_consider) or lines.append("- None detected")
    lines.extend(["", "## Notes"])
    lines.extend(f"- {note}" for note in packet.notes) or lines.append("- No additional notes")
    lines.extend([
        "",
        "> Expand context only when the selected files, test failures, or risk evidence justify it.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--changed-file", action="append", default=[])
    parser.add_argument("--git-status", action="store_true", help="Use local git status when no --changed-file is supplied.")
    parser.add_argument("--max-files", type=int, default=5000)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()
    try:
        packet = compile_context(args.repo, args.task, args.changed_file, args.max_files, args.git_status)
    except ValueError as exc:
        parser.error(str(exc))
    if args.format == "json":
        print(json.dumps(asdict(packet), ensure_ascii=False, indent=2))
    else:
        print(markdown(packet), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
