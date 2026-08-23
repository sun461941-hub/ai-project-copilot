#!/usr/bin/env python3
"""Apply or roll back the AI Project Copilot multi-interface preview patch safely."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

PACKAGE_VERSION = "2.2.0-preview.2"
PACKAGE_ROOT = Path(__file__).resolve().parent
PAYLOAD = PACKAGE_ROOT / "payload"
MANIFEST = PACKAGE_ROOT / "MANIFEST.json"
SKILL_REL = Path("skills/ai-project-copilot/SKILL.md")
STATE_REL = Path(".aipc") / f"multi-interface-upgrade-{PACKAGE_VERSION}"
RECEIPT_NAME = "receipt.json"
ANCHOR = "\n## Capability lanes\n"
INSERT = """\n## Multi-interface gateway\n\nKeep the Agent Skill as the first-class conversational interface. When another AI agent, local automation, or an application needs the same deterministic Project Copilot capabilities, read `references/multi-interface-gateway.md` and use the shared multi-interface core instead of duplicating lane logic.\n\n- Use `scripts/project_copilot.py` for local CLI automation.\n- Use `scripts/project_copilot_mcp.py` for MCP stdio clients such as coding agents and IDEs; scope caller-visible paths with `--allow-root`.\n- Use `scripts/project_copilot_api.py` for programmatic REST access; keep loopback as the default and require environment-sourced bearer authentication before any non-loopback bind.\n- Use `scripts/project_copilot_core.py` as the single fixed capability registry underneath adapters.\n- Keep arbitrary shell execution out of the gateway. Keep merge, publish, deploy, delete, permission changes, and repository writes behind explicit human-controlled workflows.\n- Prefer `copilot_run` when a caller has a natural-language goal and individual capability names when the caller already knows the exact deterministic operation.\n\n"""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def payload_files() -> list[Path]:
    return sorted(
        path
        for path in PAYLOAD.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    )


def verify_payload_integrity() -> None:
    """Verify packaged payload bytes against MANIFEST.json before touching a repo."""

    try:
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot read package manifest: {exc}") from exc
    if data.get("version") != PACKAGE_VERSION:
        raise SystemExit(f"package manifest version mismatch: expected {PACKAGE_VERSION!r}")
    expected = {
        item["path"]: item["sha256"]
        for item in data.get("files", [])
        if isinstance(item, dict)
        and isinstance(item.get("path"), str)
        and item["path"].startswith("payload/")
        and isinstance(item.get("sha256"), str)
    }
    actual = {f"payload/{path.relative_to(PAYLOAD).as_posix()}": path for path in payload_files()}
    if set(expected) != set(actual):
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        raise SystemExit(f"payload/manifest file set mismatch; missing={missing}; extra={extra}")
    for rel, path in actual.items():
        observed = sha256(path)
        if observed != expected[rel]:
            raise SystemExit(f"payload integrity check failed for {rel}: expected {expected[rel]}, got {observed}")


def validate_repo(repo: Path) -> Path:
    repo = repo.expanduser().resolve()
    skill = repo / SKILL_REL
    scripts = repo / "skills/ai-project-copilot/scripts"
    for candidate in (repo / "skills", repo / "skills/ai-project-copilot", skill, scripts):
        if candidate.is_symlink():
            raise SystemExit(f"repository target contains a symlink component: {candidate.relative_to(repo)}")
    if not skill.is_file() or not scripts.is_dir():
        raise SystemExit(f"not an AI Project Copilot repository root: {repo}")
    text = skill.read_text(encoding="utf-8")
    if "# AI Project Copilot 2.1" not in text and "# AI Project Copilot" not in text:
        raise SystemExit("SKILL.md does not look like AI Project Copilot")
    return repo


def _safe_rel(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or path.drive or ".." in path.parts or not path.parts:
        raise SystemExit(f"unsafe path in patch receipt: {value!r}")
    return path


def _safe_target(repo: Path, rel: Path) -> Path:
    """Return a repository-confined target and reject every symlink component."""
    if rel.is_absolute() or rel.drive or ".." in rel.parts or not rel.parts:
        raise SystemExit(f"unsafe patch target: {rel}")
    candidate = repo / rel
    try:
        candidate.relative_to(repo)
    except ValueError as exc:
        raise SystemExit(f"patch target escapes repository: {rel}") from exc
    current = repo
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            raise SystemExit(f"patch target contains a symlink component: {rel}")
    # `strict=False` has platform-specific behavior for a non-existent leaf
    # below a symlinked temporary directory (notably on macOS and Windows).
    # The lexical confinement check above, paired with rejecting every existing
    # symlink component, gives the same safety guarantee without false escapes.
    return candidate


def _state_dir(repo: Path) -> Path:
    return repo / STATE_REL


def _read_receipt(repo: Path) -> dict[str, object]:
    path = _safe_target(repo, STATE_REL / RECEIPT_NAME)
    if not path.is_file():
        raise SystemExit(
            f"no installer receipt found at {path}; rollback is only automatic for changes applied by this preview.2 installer"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot read installer receipt: {exc}") from exc
    if data.get("package_version") != PACKAGE_VERSION or not isinstance(data.get("files"), list):
        raise SystemExit("installer receipt is incompatible or malformed")
    return data


def _apply_plan(repo: Path, *, force: bool) -> tuple[list[str], list[dict[str, str]], bool, str]:
    changes: list[str] = []
    file_plan: list[dict[str, str]] = []
    for source in payload_files():
        rel = source.relative_to(PAYLOAD)
        target = _safe_target(repo, rel)
        source_hash = sha256(source)
        if target.exists():
            if not target.is_file():
                raise SystemExit(f"refusing to overwrite non-file payload target: {rel}")
            target_hash = sha256(target)
            if target_hash == source_hash:
                changes.append(f"unchanged {rel.as_posix()}")
                file_plan.append({"path": rel.as_posix(), "action": "preexisting_same", "payload_sha256": source_hash})
                continue
            if not force:
                raise SystemExit(f"refusing to overwrite existing modified file without --force: {rel}")
            changes.append(f"replace {rel.as_posix()}")
            file_plan.append(
                {
                    "path": rel.as_posix(),
                    "action": "replaced",
                    "payload_sha256": source_hash,
                    "original_sha256": target_hash,
                }
            )
        else:
            changes.append(f"add {rel.as_posix()}")
            file_plan.append({"path": rel.as_posix(), "action": "added", "payload_sha256": source_hash})

    skill = _safe_target(repo, SKILL_REL)
    original_skill = skill.read_text(encoding="utf-8")
    skill_inserted = False
    if INSERT.strip() in original_skill:
        changes.append("unchanged skills/ai-project-copilot/SKILL.md (gateway section already present)")
    else:
        if ANCHOR not in original_skill:
            raise SystemExit("cannot find `## Capability lanes` anchor in SKILL.md; apply manually")
        skill_inserted = True
        changes.append("update skills/ai-project-copilot/SKILL.md")
    return changes, file_plan, skill_inserted, original_skill


def apply(repo: Path, *, dry_run: bool, force: bool) -> list[str]:
    verify_payload_integrity()
    state_dir = _safe_target(repo, STATE_REL)
    changes, file_plan, skill_inserted, original_skill = _apply_plan(repo, force=force)
    if dry_run:
        return changes
    if state_dir.exists():
        # A fully installed patch is idempotent, but do not silently mutate around an
        # existing receipt: it is the rollback ownership record.
        mutations = [item for item in file_plan if item["action"] in {"added", "replaced"}]
        if mutations or skill_inserted:
            raise SystemExit(f"existing installer state found at {state_dir}; roll back it before reapplying")
        return changes

    skill = _safe_target(repo, SKILL_REL)
    state_parent = state_dir.parent
    state_parent_existed = state_parent.exists()
    state_parent.mkdir(parents=True, exist_ok=True)
    temp_state = state_parent / f".{state_dir.name}.tmp-{uuid.uuid4().hex}"
    temp_state.mkdir(parents=True)
    transaction_skill = temp_state / "skill-before-apply.md"
    transaction_skill.write_text(original_skill, encoding="utf-8")

    try:
        for item in file_plan:
            rel = _safe_rel(item["path"])
            if item["action"] == "replaced":
                target = _safe_target(repo, rel)
                backup = temp_state / "backups" / rel
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)

        for item in file_plan:
            if item["action"] not in {"added", "replaced"}:
                continue
            rel = _safe_rel(item["path"])
            source = PAYLOAD / rel
            target = _safe_target(repo, rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

        if skill_inserted:
            updated = original_skill.replace(ANCHOR, INSERT + "## Capability lanes\n", 1)
            skill.write_text(updated, encoding="utf-8")

        receipt = {
            "package_version": PACKAGE_VERSION,
            "skill_inserted": skill_inserted,
            "files": file_plan,
        }
        (temp_state / RECEIPT_NAME).write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        transaction_skill.unlink()
        temp_state.rename(state_dir)
    except BaseException:
        for item in reversed(file_plan):
            rel = _safe_rel(item["path"])
            target = _safe_target(repo, rel)
            if item["action"] == "added":
                if target.is_file():
                    target.unlink()
            elif item["action"] == "replaced":
                backup = temp_state / "backups" / rel
                if backup.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup, target)
        skill.write_text(original_skill, encoding="utf-8")
        shutil.rmtree(temp_state, ignore_errors=True)
        if not state_parent_existed and state_parent.exists() and not any(state_parent.iterdir()):
            state_parent.rmdir()
        raise
    return changes


def _remove_gateway_section(text: str, *, force: bool) -> str:
    if INSERT in text:
        return text.replace(INSERT, "\n", 1)
    heading = "\n## Multi-interface gateway\n"
    if heading not in text:
        return text
    if not force:
        raise SystemExit("gateway section was modified after install; refusing to remove it without --force")
    start = text.index(heading)
    end = text.find("\n## Capability lanes\n", start)
    if end < 0:
        raise SystemExit("modified gateway section has no `## Capability lanes` boundary; remove it manually")
    return text[:start] + "\n" + text[end + 1 :]


def _rollback_plan(repo: Path, receipt: dict[str, object], *, force: bool) -> tuple[list[str], list[dict[str, str]], str]:
    changes: list[str] = []
    operations: list[dict[str, str]] = []
    state_dir = _safe_target(repo, STATE_REL)
    raw_files = receipt.get("files")
    assert isinstance(raw_files, list)
    for raw in raw_files:
        if not isinstance(raw, dict) or not all(isinstance(raw.get(key), str) for key in ("path", "action", "payload_sha256")):
            raise SystemExit("malformed file entry in installer receipt")
        item = {str(k): str(v) for k, v in raw.items()}
        rel = _safe_rel(item["path"])
        target = _safe_target(repo, rel)
        action = item["action"]
        if action == "preexisting_same":
            changes.append(f"leave pre-existing {rel.as_posix()}")
            continue
        if action not in {"added", "replaced"}:
            raise SystemExit(f"unknown receipt action for {rel}: {action}")
        if action == "replaced":
            backup = state_dir / "backups" / rel
            if not backup.is_file() or sha256(backup) != item.get("original_sha256"):
                raise SystemExit(f"rollback backup is missing or corrupted for {rel}")
        if target.exists():
            if not target.is_file():
                raise SystemExit(f"cannot roll back non-file payload target: {rel}")
            if sha256(target) != item["payload_sha256"] and not force:
                raise SystemExit(f"refusing to overwrite/delete locally modified patched file without --force: {rel}")
        elif action == "replaced" and not force:
            raise SystemExit(f"patched replacement was deleted after install; use --force to restore original: {rel}")
        changes.append(("remove " if action == "added" else "restore original ") + rel.as_posix())
        operations.append(item)

    skill = _safe_target(repo, SKILL_REL)
    skill_text = skill.read_text(encoding="utf-8")
    restored_skill = skill_text
    if receipt.get("skill_inserted") is True:
        restored_skill = _remove_gateway_section(skill_text, force=force)
        if restored_skill != skill_text:
            changes.append("restore skills/ai-project-copilot/SKILL.md gateway section")
    return changes, operations, restored_skill


def rollback(repo: Path, *, dry_run: bool, force: bool) -> list[str]:
    verify_payload_integrity()
    receipt = _read_receipt(repo)
    changes, operations, restored_skill = _rollback_plan(repo, receipt, force=force)
    if dry_run:
        return changes

    state_dir = _safe_target(repo, STATE_REL)
    skill = _safe_target(repo, SKILL_REL)
    current_skill = skill.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="aipc-rollback-txn-") as temp:
        txn = Path(temp)
        (txn / "skill.md").write_text(current_skill, encoding="utf-8")
        for item in operations:
            rel = _safe_rel(item["path"])
            target = _safe_target(repo, rel)
            if target.is_file():
                saved = txn / "current" / rel
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, saved)
        try:
            for item in operations:
                rel = _safe_rel(item["path"])
                target = _safe_target(repo, rel)
                if item["action"] == "added":
                    if target.is_file():
                        target.unlink()
                else:
                    backup = state_dir / "backups" / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup, target)
            if restored_skill != current_skill:
                skill.write_text(restored_skill, encoding="utf-8")
        except BaseException:
            for item in operations:
                rel = _safe_rel(item["path"])
                target = _safe_target(repo, rel)
                saved = txn / "current" / rel
                if saved.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(saved, target)
                elif target.is_file():
                    target.unlink()
            skill.write_text(current_skill, encoding="utf-8")
            raise

    shutil.rmtree(state_dir)
    for item in operations:
        rel = _safe_rel(item["path"])
        parent = _safe_target(repo, rel).parent
        while parent != repo and parent.exists() and parent != state_dir.parent and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    state_parent = state_dir.parent
    if state_parent.exists() and not any(state_parent.iterdir()):
        state_parent.rmdir()
    return changes


def run_tests(repo: Path) -> int:
    commands = [
        [sys.executable, "tools/validate_skill.py", "skills/ai-project-copilot"],
        [sys.executable, "skills/ai-project-copilot/scripts/run_skill_evals.py", "--format", "json"],
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        [sys.executable, "-m", "compileall", "-q", "tools", "tests", "skills/ai-project-copilot/scripts"],
    ]
    for command in commands:
        result = subprocess.run(command, cwd=repo, check=False)
        if result.returncode:
            return result.returncode
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", type=Path, help="AI Project Copilot repository root")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="overwrite/delete conflicting patched files")
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--run-tests", action="store_true")
    args = parser.parse_args(argv)
    repo = validate_repo(args.repo)
    changes = (
        rollback(repo, dry_run=args.dry_run, force=args.force)
        if args.rollback
        else apply(repo, dry_run=args.dry_run, force=args.force)
    )
    for item in changes:
        print(item)
    if args.run_tests and not args.rollback and not args.dry_run:
        result = run_tests(repo)
        if result:
            print("verification failed; rolling back the preview installation", file=sys.stderr)
            rollback(repo, dry_run=False, force=False)
        return result
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
