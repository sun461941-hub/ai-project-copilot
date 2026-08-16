#!/usr/bin/env python3
"""Shared execution core for AI Project Copilot Skill, CLI, REST, and MCP adapters.

The core deliberately reuses the project's existing deterministic scripts instead of
forking their logic.  Adapters submit a named capability plus structured arguments;
this module validates the request, builds an argv from a fixed registry, executes it
without a shell, bounds captured output, and normalizes the result as JSON-friendly
objects.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

VERSION = "2.2.0-preview.2"
DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_CAPTURE_BYTES = 2 * 1024 * 1024


class CopilotError(RuntimeError):
    """Base error raised for rejected or failed Project Copilot requests."""


class ValidationError(CopilotError):
    """Raised when a caller supplies invalid or unsafe arguments."""


class ExecutionError(CopilotError):
    """Raised when a deterministic helper cannot be executed."""


@dataclass(frozen=True)
class ExecutionPolicy:
    """Runtime limits shared by non-Skill adapters.

    allowed_roots is optional.  When set, any repository or file path submitted by a
    remote/agent caller must resolve beneath at least one allowed root.  The direct
    CLI can intentionally omit the restriction because the local user already chose
    the process boundary.
    """

    allowed_roots: tuple[Path, ...] = ()
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_capture_bytes: int = DEFAULT_MAX_CAPTURE_BYTES

    def normalized(self) -> "ExecutionPolicy":
        roots = tuple(path.expanduser().resolve() for path in self.allowed_roots)
        timeout = float(self.timeout_seconds)
        maximum = int(self.max_capture_bytes)
        if timeout <= 0 or timeout > 3600:
            raise ValidationError("timeout_seconds must be > 0 and <= 3600")
        if maximum < 1024 or maximum > 64 * 1024 * 1024:
            raise ValidationError("max_capture_bytes must be between 1 KiB and 64 MiB")
        return ExecutionPolicy(roots, timeout, maximum)


@dataclass(frozen=True)
class CapabilitySpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    builder: Callable[[Mapping[str, Any], "CopilotEngine"], list[list[str]]]
    consequential: bool = False


@dataclass
class CommandResult:
    argv: list[str]
    exit_code: int
    duration_ms: int
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    parsed: Any = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "argv": self.argv,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "stdout_truncated": self.stdout_truncated,
            "stderr_truncated": self.stderr_truncated,
            "parsed": self.parsed,
        }


def _json_schema_object(properties: dict[str, Any], required: Iterable[str] = ()) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    required_list = list(required)
    if required_list:
        schema["required"] = required_list
    return schema


def _string(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


def _boolean(description: str, default: bool | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"type": "boolean", "description": description}
    if default is not None:
        item["default"] = default
    return item


def _require_text(args: Mapping[str, Any], name: str) -> str:
    value = args.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{name} must be a non-empty string")
    if "\x00" in value:
        raise ValidationError(f"{name} contains a NUL byte")
    return value.strip()


def _optional_text(args: Mapping[str, Any], name: str, default: str | None = None) -> str | None:
    value = args.get(name, default)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{name} must be a string")
    value = value.strip()
    if not value:
        return default
    if "\x00" in value:
        raise ValidationError(f"{name} contains a NUL byte")
    return value


def _bool(args: Mapping[str, Any], name: str, default: bool = False) -> bool:
    value = args.get(name, default)
    if not isinstance(value, bool):
        raise ValidationError(f"{name} must be a boolean")
    return value


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _parse_json_if_possible(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        # Some helpers may print a short informational prefix before JSON.  Try the
        # last syntactically plausible object/array without treating text as code.
        for marker in ("{", "["):
            index = stripped.find(marker)
            if index > 0:
                try:
                    return json.loads(stripped[index:])
                except json.JSONDecodeError:
                    pass
    return None


@dataclass
class _CaptureBuffer:
    maximum: int
    data: bytearray = field(default_factory=bytearray)
    total: int = 0
    truncated: bool = False

    def add(self, chunk: bytes) -> None:
        self.total += len(chunk)
        remaining = self.maximum - len(self.data)
        if remaining > 0:
            self.data.extend(chunk[:remaining])
        if self.total > self.maximum:
            self.truncated = True

    def text(self) -> str:
        value = bytes(self.data).decode("utf-8", errors="replace")
        if self.truncated:
            value += f"\n...[truncated; captured {len(self.data)} of {self.total} bytes]"
        return value


def _drain_pipe(stream: Any, capture: _CaptureBuffer) -> None:
    try:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            capture.add(chunk)
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _terminate_process_tree(process: subprocess.Popen[Any]) -> None:
    # On POSIX, try the process group even if the direct child already exited: a
    # descendant can otherwise keep stdout/stderr pipes open after its parent dies.
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError):
            pass
    if process.poll() is not None:
        return
    if os.name == "nt":
        # taskkill /T terminates descendants as well. No shell is involved.
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=5,
            )
            return
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        process.kill()
    except ProcessLookupError:
        pass


def run_bounded(argv: list[str], *, cwd: Path, policy: ExecutionPolicy) -> CommandResult:
    """Run a fixed argv without a shell while bounding retained stdout/stderr bytes.

    Pipes are continuously drained on reader threads, so child processes cannot block
    on a full pipe. Only ``max_capture_bytes`` is retained per stream; excess output is
    discarded as it arrives instead of being accumulated in RAM or an unbounded temp
    file.
    """

    if not argv or any(not isinstance(item, str) or "\x00" in item for item in argv):
        raise ValidationError("argv must contain safe strings")
    started = time.monotonic()
    creationflags = 0
    start_new_session = False
    if os.name == "posix":
        start_new_session = True
    elif os.name == "nt" and hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

    try:
        process = subprocess.Popen(
            argv,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=start_new_session,
            creationflags=creationflags,
            close_fds=(os.name != "nt"),
            env=_sanitized_env(),
        )
    except OSError as exc:
        raise ExecutionError(f"failed to start {argv[0]}: {exc}") from exc

    if process.stdout is None or process.stderr is None:  # pragma: no cover - Popen contract
        _terminate_process_tree(process)
        raise ExecutionError("failed to capture helper output")

    stdout_capture = _CaptureBuffer(policy.max_capture_bytes)
    stderr_capture = _CaptureBuffer(policy.max_capture_bytes)
    readers = [
        threading.Thread(target=_drain_pipe, args=(process.stdout, stdout_capture), daemon=True),
        threading.Thread(target=_drain_pipe, args=(process.stderr, stderr_capture), daemon=True),
    ]
    for reader in readers:
        reader.start()

    timed_out = False
    try:
        exit_code = process.wait(timeout=policy.timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        _terminate_process_tree(process)
        try:
            exit_code = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            exit_code = -1
        timeout_exc = exc
    finally:
        for reader in readers:
            reader.join(timeout=1)
        if any(reader.is_alive() for reader in readers):
            # A descendant inherited the pipe after the direct helper exited. Keep
            # the gateway deterministic: terminate the isolated process group/tree
            # rather than leaking a background child or reader thread.
            _terminate_process_tree(process)
            for reader in readers:
                reader.join(timeout=4)
        if any(reader.is_alive() for reader in readers):
            raise ExecutionError("helper descendants kept stdout/stderr pipes open after termination")

    stdout = stdout_capture.text()
    stderr = stderr_capture.text()
    if timed_out:
        raise ExecutionError(
            f"command timed out after {policy.timeout_seconds:.1f}s: {argv!r}; "
            f"stdout={stdout[-1000:]!r}; stderr={stderr[-1000:]!r}"
        ) from timeout_exc

    return CommandResult(
        argv=argv,
        exit_code=exit_code,
        duration_ms=int((time.monotonic() - started) * 1000),
        stdout=stdout,
        stderr=stderr,
        stdout_truncated=stdout_capture.truncated,
        stderr_truncated=stderr_capture.truncated,
        parsed=_parse_json_if_possible(stdout),
    )

def _sanitized_env() -> dict[str, str]:
    """Pass only ordinary runtime variables to deterministic helper subprocesses.

    Existing AIPC/OpenAI/GitHub/cloud credentials are intentionally not inherited by
    the generic multi-interface core.  A specialized provider gateway can keep its
    own explicit credential contract.
    """

    allow = {
        "PATH",
        "HOME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "TMP",
        "TEMP",
        "TMPDIR",
        "SYSTEMROOT",
        "WINDIR",
        "PATHEXT",
        "PYTHONPATH",
        "PYTHONUTF8",
        "PYTHONIOENCODING",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TERM",
    }
    return {key: value for key, value in os.environ.items() if key in allow}


def _validate_arguments(spec: CapabilitySpec, args: Mapping[str, Any]) -> None:
    schema = spec.input_schema
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    if schema.get("additionalProperties") is False:
        unknown = sorted(set(args) - set(properties))
        if unknown:
            raise ValidationError(f"unexpected argument(s) for {spec.name}: {', '.join(unknown)}")
    missing = [name for name in required if name not in args]
    if missing:
        raise ValidationError(f"missing required argument(s) for {spec.name}: {', '.join(missing)}")
    for name, value in args.items():
        expected = properties.get(name, {}).get("type")
        if expected == "string" and not isinstance(value, str):
            raise ValidationError(f"{name} must be a string")
        if expected == "boolean" and not isinstance(value, bool):
            raise ValidationError(f"{name} must be a boolean")


class CopilotEngine:
    """Uniform, read-oriented execution surface for Project Copilot capabilities."""

    def __init__(self, skill_root: Path | None = None, policy: ExecutionPolicy | None = None):
        self.skill_root = (skill_root or Path(__file__).resolve().parents[1]).expanduser().resolve()
        self.scripts_dir = self.skill_root / "scripts"
        self.policy = (policy or ExecutionPolicy()).normalized()
        self._specs = _build_specs()

    def capability_specs(self) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "inputSchema": spec.input_schema,
                "consequential": spec.consequential,
            }
            for spec in self._specs.values()
        ]

    def resolve_path(self, raw: str, *, kind: str = "path", must_exist: bool = True) -> Path:
        if not isinstance(raw, str) or not raw.strip() or "\x00" in raw:
            raise ValidationError(f"{kind} must be a non-empty path string")
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        resolved = candidate.resolve()
        if must_exist and not resolved.exists():
            raise ValidationError(f"{kind} does not exist: {resolved}")
        if self.policy.allowed_roots and not any(_within(resolved, root) for root in self.policy.allowed_roots):
            allowed = ", ".join(str(root) for root in self.policy.allowed_roots)
            raise ValidationError(f"{kind} is outside allowed roots ({allowed}): {resolved}")
        return resolved

    def script(self, name: str) -> Path:
        if not name.replace("_", "").isalnum():
            raise ValidationError("invalid script name")
        path = (self.scripts_dir / f"{name}.py").resolve()
        if not _within(path, self.scripts_dir):
            raise ValidationError("script path escaped the skill scripts directory")
        if not path.is_file():
            raise ExecutionError(f"required Project Copilot helper is missing: {path}")
        return path

    def python_argv(self, script_name: str, *args: str) -> list[str]:
        return [sys.executable, str(self.script(script_name)), *args]

    def execute(self, capability: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        args = dict(arguments or {})
        spec = self._specs.get(capability)
        if spec is None:
            raise ValidationError(f"unknown capability: {capability}")
        _validate_arguments(spec, args)
        commands = spec.builder(args, self)
        if not commands:
            raise ExecutionError(f"capability produced no deterministic command: {capability}")
        results: list[CommandResult] = []
        for argv in commands:
            results.append(run_bounded(argv, cwd=self.skill_root, policy=self.policy))

        ok = all(result.exit_code == 0 for result in results)
        response: dict[str, Any] = {
            "schema_version": "aipc.multi-interface.v1",
            "engine_version": VERSION,
            "request_id": request_id,
            "capability": capability,
            "status": "completed" if ok else "failed",
            "consequential": spec.consequential,
            "results": [result.as_dict() for result in results],
        }
        if len(results) == 1:
            response["data"] = results[0].parsed if results[0].parsed is not None else results[0].stdout
        else:
            response["data"] = [
                result.parsed if result.parsed is not None else result.stdout for result in results
            ]
        return response

    def orchestrate(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Route a natural-language goal and execute safe deterministic stages.

        The orchestrator never performs merge/publish/deploy/delete/repository-write
        actions.  It executes read-oriented evidence helpers only and reports stages
        that need more caller input instead of inventing it.
        """

        _validate_arguments(self._specs["copilot_run"], arguments)
        goal = _require_text(arguments, "goal")
        repo = _optional_text(arguments, "repo")
        include_evals = _bool(arguments, "include_evals", False)
        plan = self.execute("route", {"prompt": goal})
        lanes = _extract_lanes(plan.get("data"))

        stages: list[dict[str, Any]] = [
            {"stage": "route", "status": plan["status"], "result": plan}
        ]

        if repo:
            try:
                result = self.execute("analyze_repository", {"repo": repo, "task": goal})
                stages.append({"stage": "discover", "status": result["status"], "result": result})
            except CopilotError as exc:
                stages.append({"stage": "discover", "status": "failed", "error": str(exc)})

        selected = lanes or ["discover"]
        for lane in selected:
            if lane == "discover":
                continue
            if lane == "review":
                if not repo:
                    stages.append(_skipped("review", "repo is required"))
                    continue
                payload = {
                    "repo": repo,
                    "base": _optional_text(arguments, "base", "main"),
                    "head": _optional_text(arguments, "head", "HEAD"),
                }
                stages.append(_stage_from_call(self, "review", "review_changes", payload))
            elif lane == "secure":
                if not repo:
                    stages.append(_skipped("secure", "repo is required"))
                    continue
                stages.append(_stage_from_call(self, "secure", "scan_security", {"repo": repo}))
            elif lane == "release":
                if not repo:
                    stages.append(_skipped("release", "repo is required"))
                    continue
                from_ref = _optional_text(arguments, "from_ref")
                current_version = _optional_text(arguments, "current_version")
                if not from_ref or not current_version:
                    stages.append(_skipped("release", "from_ref and current_version are required"))
                    continue
                stages.append(
                    _stage_from_call(
                        self,
                        "release",
                        "release_readiness",
                        {"repo": repo, "from_ref": from_ref, "current_version": current_version},
                    )
                )
            elif lane == "maintain":
                issue_json = _optional_text(arguments, "issue_json")
                if not issue_json:
                    stages.append(_skipped("maintain", "issue_json is required"))
                    continue
                stages.append(_stage_from_call(self, "maintain", "maintainer_triage", {"issue_json": issue_json}))
            elif lane == "quality":
                if include_evals:
                    stages.append(_stage_from_call(self, "quality", "run_evals", {}))
                else:
                    stages.append(_skipped("quality", "set include_evals=true to run the bundled deterministic eval suite"))
            else:
                stages.append(
                    {
                        "stage": lane,
                        "status": "planned",
                        "reason": "lane is guidance/model-driven and has no read-only deterministic adapter in this preview",
                    }
                )

        failed = any(stage.get("status") == "failed" for stage in stages)
        incomplete = any(stage.get("status") in {"skipped", "planned"} for stage in stages)
        overall_status = "failed" if failed else ("partial" if incomplete else "completed")
        return {
            "schema_version": "aipc.multi-interface.v1",
            "engine_version": VERSION,
            "request_id": str(uuid.uuid4()),
            "capability": "copilot_run",
            "status": overall_status,
            "goal": goal,
            "lanes": selected,
            "stages": stages,
            "human_authority_required_for": ["merge", "publish", "deploy", "delete", "permission changes", "repository writes"],
        }


def _skipped(stage: str, reason: str) -> dict[str, Any]:
    return {"stage": stage, "status": "skipped", "reason": reason}


def _stage_from_call(engine: CopilotEngine, stage: str, capability: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    try:
        result = engine.execute(capability, payload)
        return {"stage": stage, "status": result["status"], "result": result}
    except CopilotError as exc:
        return {"stage": stage, "status": "failed", "error": str(exc)}


def _extract_lanes(value: Any) -> list[str]:
    allowed = {"discover", "launch", "retrofit", "maintain", "review", "release", "secure", "quality", "showcase"}
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            for key, item in node.items():
                if key in {"mode", "lane", "name"} and isinstance(item, str):
                    candidate = item.strip().lower()
                    if candidate in allowed and candidate not in found:
                        found.append(candidate)
                walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, str):
            # Text fallback for older helpers that do not emit JSON.
            lowered = node.lower()
            for candidate in allowed:
                if candidate in lowered and candidate not in found:
                    found.append(candidate)

    walk(value)
    return found


def _build_specs() -> dict[str, CapabilitySpec]:
    repo_prop = _string("Local repository directory available to the Project Copilot process.")
    return {
        "route": CapabilitySpec(
            "route",
            "Route a broad repository/product request into Project Copilot capability lanes.",
            _json_schema_object({"prompt": _string("Natural-language task or goal.")}, ["prompt"]),
            _build_route,
        ),
        "analyze_repository": CapabilitySpec(
            "analyze_repository",
            "Map repository context and return task-focused files and project signals.",
            _json_schema_object(
                {"repo": repo_prop, "task": _string("Task used to focus repository context.")},
                ["repo", "task"],
            ),
            _build_analyze,
        ),
        "review_changes": CapabilitySpec(
            "review_changes",
            "Prioritize PR/diff risk using the existing deterministic change-risk helper.",
            _json_schema_object(
                {
                    "repo": repo_prop,
                    "base": _string("Base git ref; defaults to main."),
                    "head": _string("Head git ref; defaults to HEAD."),
                    "patch": _string("Optional patch file. When set, repo/base/head are not required."),
                }
            ),
            _build_review,
        ),
        "scan_security": CapabilitySpec(
            "scan_security",
            "Run read-only GitHub Actions/supply-chain and MCP configuration security checks.",
            _json_schema_object({"repo": repo_prop}, ["repo"]),
            _build_security,
        ),
        "release_readiness": CapabilitySpec(
            "release_readiness",
            "Build deterministic SemVer/release intelligence without publishing anything.",
            _json_schema_object(
                {
                    "repo": repo_prop,
                    "from_ref": _string("Previous release/reference to compare from."),
                    "current_version": _string("Current semantic version, e.g. 2.1.0."),
                },
                ["repo", "from_ref", "current_version"],
            ),
            _build_release,
        ),
        "maintainer_triage": CapabilitySpec(
            "maintainer_triage",
            "Run reviewable issue pre-triage from a local issue JSON fixture.",
            _json_schema_object({"issue_json": _string("Path to an issue JSON file.")}, ["issue_json"]),
            _build_maintain,
        ),
        "run_evals": CapabilitySpec(
            "run_evals",
            "Run the bundled deterministic/structural Skill eval suite.",
            _json_schema_object({}),
            _build_evals,
        ),
        "copilot_run": CapabilitySpec(
            "copilot_run",
            "Route a goal and execute the available read-only deterministic stages through one request.",
            _json_schema_object(
                {
                    "goal": _string("Natural-language repository engineering goal."),
                    "repo": repo_prop,
                    "base": _string("Base git ref for review; defaults to main."),
                    "head": _string("Head git ref for review; defaults to HEAD."),
                    "from_ref": _string("Previous release ref for release intelligence."),
                    "current_version": _string("Current semantic version for release intelligence."),
                    "issue_json": _string("Issue fixture path for maintain lane."),
                    "include_evals": _boolean("Run bundled deterministic evals when quality is selected.", False),
                },
                ["goal"],
            ),
            # CopilotEngine.execute special-cases neither orchestration nor recursion;
            # adapters call engine.orchestrate for this capability.
            lambda _args, _engine: [],
        ),
    }


def _build_route(args: Mapping[str, Any], engine: CopilotEngine) -> list[list[str]]:
    prompt = _require_text(args, "prompt")
    return [engine.python_argv("workflow_router", "--prompt", prompt, "--format", "json")]


def _build_analyze(args: Mapping[str, Any], engine: CopilotEngine) -> list[list[str]]:
    repo = engine.resolve_path(_require_text(args, "repo"), kind="repo")
    if not repo.is_dir():
        raise ValidationError(f"repo is not a directory: {repo}")
    task = _require_text(args, "task")
    return [engine.python_argv("repo_context", "--repo", str(repo), "--task", task, "--format", "json")]


def _build_review(args: Mapping[str, Any], engine: CopilotEngine) -> list[list[str]]:
    patch = _optional_text(args, "patch")
    if patch:
        patch_path = engine.resolve_path(patch, kind="patch")
        if not patch_path.is_file():
            raise ValidationError(f"patch is not a file: {patch_path}")
        return [engine.python_argv("change_risk", "--patch", str(patch_path), "--format", "json")]
    repo = engine.resolve_path(_require_text(args, "repo"), kind="repo")
    if not repo.is_dir():
        raise ValidationError(f"repo is not a directory: {repo}")
    base = _optional_text(args, "base", "main") or "main"
    head = _optional_text(args, "head", "HEAD") or "HEAD"
    return [engine.python_argv("change_risk", "--repo", str(repo), "--base", base, "--head", head, "--format", "json")]


def _build_security(args: Mapping[str, Any], engine: CopilotEngine) -> list[list[str]]:
    repo = engine.resolve_path(_require_text(args, "repo"), kind="repo")
    if not repo.is_dir():
        raise ValidationError(f"repo is not a directory: {repo}")
    return [
        engine.python_argv("supply_chain_guard", "--repo", str(repo), "--format", "json"),
        engine.python_argv("mcp_config_audit", "--repo", str(repo), "--format", "json"),
    ]


def _build_release(args: Mapping[str, Any], engine: CopilotEngine) -> list[list[str]]:
    repo = engine.resolve_path(_require_text(args, "repo"), kind="repo")
    if not repo.is_dir():
        raise ValidationError(f"repo is not a directory: {repo}")
    return [
        engine.python_argv(
            "release_intel",
            "--repo",
            str(repo),
            "--from-ref",
            _require_text(args, "from_ref"),
            "--current-version",
            _require_text(args, "current_version"),
            "--format",
            "json",
        )
    ]


def _build_maintain(args: Mapping[str, Any], engine: CopilotEngine) -> list[list[str]]:
    issue = engine.resolve_path(_require_text(args, "issue_json"), kind="issue_json")
    if not issue.is_file():
        raise ValidationError(f"issue_json is not a file: {issue}")
    return [engine.python_argv("maintainer_triage", "--issue-json", str(issue), "--format", "json")]


def _build_evals(_args: Mapping[str, Any], engine: CopilotEngine) -> list[list[str]]:
    return [engine.python_argv("run_skill_evals", "--format", "json")]


def invoke(engine: CopilotEngine, capability: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Adapter-safe entry point that handles the orchestrator pseudo-capability."""

    if capability == "copilot_run":
        return engine.orchestrate(dict(arguments or {}))
    return engine.execute(capability, arguments)
