"""Bounded local experiments with captured logs, Git snapshots and artifacts.

LocalProcessBackend is intentionally *not* a security sandbox. Its caller must
approve execute_host. Docker limits filesystem access to the experiment code
directory and disables networking unless that separate capability is approved.
"""

import asyncio
import hashlib
import io
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import time
import tarfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4


class ExecutionUnavailable(RuntimeError):
    pass


class WorkspaceViolation(ValueError):
    pass


def contained_path(root: Path, relative: str | Path) -> Path:
    root = root.resolve()
    candidate = (root / relative).resolve()
    if candidate == root or not candidate.is_relative_to(root):
        raise WorkspaceViolation("The path must identify a file or directory inside the experiment workspace.")
    # Windows alternate data streams and reserved device names are not files in
    # the expected sense. Reject them on every platform for portable manifests.
    parts = Path(relative).parts
    if any(":" in part or part.rstrip(". ") != part or part.split(".", 1)[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))} for part in parts):
        raise WorkspaceViolation("The path contains a reserved or nonportable filename.")
    return candidate


def _identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,100}", value):
        raise WorkspaceViolation("Workspace identifiers may contain letters, digits, underscores and hyphens.")
    return value


def sanitized_environment(workspace: Path) -> dict[str, str]:
    """Inherit OS launch essentials only; never provider keys or cloud config."""
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE"}
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    home, temp = workspace / ".home", workspace / ".tmp"
    home.mkdir(exist_ok=True)
    temp.mkdir(exist_ok=True)
    env.update({"HOME": str(home), "USERPROFILE": str(home), "APPDATA": str(home), "LOCALAPPDATA": str(home), "TEMP": str(temp), "TMP": str(temp), "TMPDIR": str(temp), "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1", "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "LANG": "C.UTF-8"})
    return env


@dataclass(frozen=True)
class Workspace:
    project_id: str
    experiment_id: str
    path: Path
    metadata_path: Path
    parent_commit: str | None = None
    source_experiment_id: str | None = None
    source_commit: str | None = None

    def write_file(self, relative_path: str, content: str) -> Path:
        path = contained_path(self.path, relative_path)
        if ".git" in Path(relative_path).parts:
            raise WorkspaceViolation("Experiment edits cannot modify Git metadata.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path


@dataclass(frozen=True)
class CollectedArtifact:
    path: str
    relative_path: str
    sha256: str
    byte_size: int


@dataclass(frozen=True)
class ExecutionResult:
    run_id: str
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool
    cancelled: bool
    git_commit: str | None
    stdout_path: str
    stderr_path: str
    manifest_path: str
    artifacts: list[CollectedArtifact]
    backend: str


class ExecutionBackend(Protocol):
    async def create_workspace(self, project_id: str, experiment_id: str, source_dir: str | Path | None = None) -> Workspace: ...
    async def run(self, workspace: Workspace, command: list[str], timeout_seconds: float = 300, run_id: str | None = None) -> ExecutionResult: ...
    async def cancel(self, run_id: str) -> bool: ...
    async def collect_artifacts(self, workspace: Workspace, patterns: list[str] | None = None) -> list[CollectedArtifact]: ...


class _WindowsJob:
    """Kill remaining descendants when the job handle closes, including success."""

    def __init__(self, pid: int) -> None:
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.SetInformationJobObject.restype = wintypes.BOOL
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "Could not create experiment process job")
        info = ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.close()
            raise OSError(ctypes.get_last_error(), "Could not configure process cleanup")
        process = self.kernel.OpenProcess(0x0100 | 0x0001, False, pid)
        try:
            if not process or not self.kernel.AssignProcessToJobObject(self.handle, process):
                self.close()
                raise OSError(ctypes.get_last_error(), "Could not attach experiment process cleanup")
        finally:
            if process:
                self.kernel.CloseHandle(process)

    def close(self) -> None:
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


@dataclass
class _Running:
    process: asyncio.subprocess.Process
    cancelled: bool = False
    job: _WindowsJob | None = None


class LocalProcessBackend:
    backend_name = "local_process"

    def __init__(self, root: str | Path, *, max_log_bytes: int = 2_000_000, max_artifact_bytes: int = 100_000_000, max_artifact_files: int = 2000) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_log_bytes = max_log_bytes
        self.max_artifact_bytes = max_artifact_bytes
        self.max_artifact_files = max_artifact_files
        self._running: dict[str, _Running] = {}

    def _validate(self, workspace: Workspace) -> None:
        expected = self.root / _identifier(workspace.project_id) / _identifier(workspace.experiment_id)
        if workspace.path.resolve() != (expected / "code").resolve() or workspace.metadata_path.resolve() != (expected / "records").resolve():
            raise WorkspaceViolation("Workspace does not belong to this execution backend.")
        if not workspace.path.resolve().is_relative_to(self.root) or not workspace.metadata_path.resolve().is_relative_to(self.root):
            raise WorkspaceViolation("Workspace cannot escape the execution root.")

    async def _git(self, workspace: Workspace, *arguments: str, check: bool = True, binary: bool = False) -> str | bytes:
        if not shutil.which("git"):
            raise ExecutionUnavailable("Git must be installed to record reproducible experiment snapshots.")
        process = await asyncio.create_subprocess_exec("git", f"--git-dir={workspace.metadata_path / 'repository.git'}", f"--work-tree={workspace.path}", "-c", "core.longpaths=true", "-c", "core.hooksPath=", "-c", "commit.gpgsign=false", *arguments, cwd=workspace.path, env=sanitized_environment(workspace.path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if check and process.returncode:
            raise ExecutionUnavailable(f"Git snapshot failed: {stderr.decode(errors='replace')[:1000]}")
        return stdout if binary else stdout.decode(errors="replace").strip()

    async def create_workspace(self, project_id: str, experiment_id: str, source_dir: str | Path | None = None) -> Workspace:
        base = contained_path(self.root, Path(_identifier(project_id)) / _identifier(experiment_id))
        base.mkdir(parents=True, exist_ok=False)
        code, records = base / "code", base / "records"
        code.mkdir()
        records.mkdir()
        workspace = Workspace(project_id, experiment_id, code, records)
        if source_dir is not None:
            source = Path(source_dir).resolve()
            if not source.is_dir() or self.root.is_relative_to(source):
                raise WorkspaceViolation("The source must be a directory outside the execution root's ancestry.")
            def copy_source() -> None:
                total = 0
                for item in source.rglob("*"):
                    relative = item.relative_to(source)
                    if any(part in {".git", ".venv", "node_modules", "__pycache__", ".home", ".tmp"} for part in relative.parts) or any(part == ".env" or part.startswith(".env.") for part in relative.parts):
                        continue
                    if item.is_symlink() or not item.resolve().is_relative_to(source):
                        raise WorkspaceViolation("Source repositories containing symbolic links cannot be imported automatically.")
                    target = contained_path(code, relative)
                    if item.is_file():
                        total += item.stat().st_size
                        if total > self.max_artifact_bytes:
                            raise WorkspaceViolation("The source repository exceeds the workspace copy limit.")
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(item, target)
            await asyncio.to_thread(copy_source)
        (code / ".gitignore").write_text((code / ".gitignore").read_text(encoding="utf-8") + "\n.home/\n.tmp/\n" if (code / ".gitignore").exists() else ".home/\n.tmp/\n", encoding="utf-8")
        await self._git(workspace, "init", "--quiet")
        commit = await self.snapshot(workspace, "Initial experiment workspace")
        return Workspace(project_id, experiment_id, code, records, commit)

    async def fork_workspace(self, project_id: str, experiment_id: str,
                             parent_experiment_id: str, parent_commit: str) -> Workspace:
        """Materialize an exact recorded input snapshot, never mutable outputs."""
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", parent_commit):
            raise WorkspaceViolation("A parent snapshot must be an exact Git commit identifier.")
        parent_base = contained_path(self.root, Path(_identifier(project_id)) / _identifier(parent_experiment_id))
        parent = Workspace(project_id, parent_experiment_id, parent_base / "code", parent_base / "records")
        self._validate(parent)
        if not (parent.metadata_path / "repository.git").is_dir():
            raise WorkspaceViolation("The parent experiment snapshot does not exist in this project.")
        archive = await self._git(parent, "archive", "--format=tar", parent_commit, binary=True)
        if len(archive) > self.max_artifact_bytes + self.max_artifact_files * 2048:
            raise WorkspaceViolation("Parent snapshot exceeds the workspace size limit.")
        workspace = await self.create_workspace(project_id, experiment_id)
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
            entries, size = 0, 0
            for member in bundle:
                if not member.isfile() and not member.isdir():
                    raise WorkspaceViolation("Parent snapshots cannot contain links or special files.")
                target = contained_path(workspace.path, member.name)
                if ".git" in Path(member.name).parts:
                    raise WorkspaceViolation("Parent snapshots cannot overwrite Git metadata.")
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                entries += 1
                size += member.size
                if entries > self.max_artifact_files or size > self.max_artifact_bytes:
                    raise WorkspaceViolation("Parent snapshot exceeds workspace limits.")
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.extractfile(member) as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
        commit = await self.snapshot(workspace, f"Derived from {parent_experiment_id}@{parent_commit}")
        lineage = {"parent_experiment_id": parent_experiment_id, "parent_commit": parent_commit}
        (workspace.metadata_path / "lineage.json").write_text(json.dumps(lineage), encoding="utf-8")
        return Workspace(project_id, experiment_id, workspace.path, workspace.metadata_path,
                         commit, parent_experiment_id, parent_commit)

    async def snapshot(self, workspace: Workspace, message: str = "Experiment inputs") -> str:
        self._validate(workspace)
        await self.collect_artifacts(workspace)
        await self._git(workspace, "add", "--all")
        await self._git(workspace, "-c", "user.name=Sapling", "-c", "user.email=sapling@localhost", "commit", "--allow-empty", "--quiet", "-m", message)
        return await self._git(workspace, "rev-parse", "HEAD")

    async def evaluate(self, candidate: Workspace, files: dict[str, str], command: list[str],
                       *, timeout_seconds: float = 300, config: dict | None = None,
                       expected_version: str | None = None, run_id: str | None = None) -> dict:
        """Run a versioned evaluator separately from the candidate workspace.

        The evaluator reads copied outputs under candidate/. This is provenance
        and an integrity check, not a claim that agent-authored metrics are true
        or that native processes are an adversarial security boundary.
        """
        self._validate(candidate)
        if not files or len(files) > 100 or any(not isinstance(k, str) or not isinstance(v, str)
                                               for k, v in files.items()):
            raise WorkspaceViolation("Evaluator files must be a nonempty text-file map.")
        if sum(len(v.encode()) for v in files.values()) > 2_000_000:
            raise WorkspaceViolation("Evaluator source exceeds the size limit.")
        configuration = {"files": files, "command": command, "config": config or {}}
        encoded = json.dumps(configuration, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
        version = hashlib.sha256(encoded).hexdigest()
        if expected_version is not None and version != expected_version:
            raise WorkspaceViolation("Evaluator content differs from the requested evaluation version.")
        evaluation_id = _identifier(run_id or f"eval-{uuid4()}")
        evaluation = await self.create_workspace(candidate.project_id, evaluation_id)
        for name, content in files.items():
            if Path(name).parts[0] == "candidate" or name == "evaluation-config.json":
                raise WorkspaceViolation("Evaluator source cannot replace candidate inputs or configuration.")
            evaluation.write_file(name, content)
        evaluation.write_file("evaluation-config.json", json.dumps(config or {}, allow_nan=False))
        original = await self.collect_artifacts(candidate)
        for artifact in original:
            destination = contained_path(evaluation.path, Path("candidate") / artifact.relative_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(artifact.path, destination)
        expected = {item.relative_path: item.sha256 for item in await self.collect_artifacts(evaluation)}
        result = await self.run(evaluation, command, timeout_seconds, run_id=evaluation_id)
        actual = {item.relative_path: item.sha256 for item in await self.collect_artifacts(evaluation)}
        intact = all(actual.get(name) == digest for name, digest in expected.items())
        metrics = None
        if result.exit_code == 0 and not result.cancelled and not result.timed_out and intact:
            try:
                parsed = json.loads(result.stdout, parse_constant=lambda _: None)
                metrics = parsed if isinstance(parsed, dict) else None
            except ValueError:
                pass
        record = {
            "evaluator_version": version, "candidate_experiment_id": candidate.experiment_id,
            "input_hashes": {item.relative_path: item.sha256 for item in original},
            "configuration": configuration, "result": asdict(result), "metrics": metrics,
            "inputs_unchanged": intact, "metrics_are_untrusted": True,
            "verification": "versioned_evaluator" if intact else "evaluation_inputs_modified",
            "isolation": "container" if self.backend_name == "local_docker" else "native_process",
        }
        (candidate.metadata_path / f"{evaluation_id}.evaluation.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return record

    def _command(self, workspace: Workspace, command: list[str], run_id: str) -> list[str]:
        return command

    async def _terminate(self, running: _Running) -> None:
        if running.job:
            running.job.close()
        process = running.process
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif process.returncode is None and not running.job:
            killer = await asyncio.create_subprocess_exec("taskkill", "/PID", str(process.pid), "/T", "/F", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await killer.wait()
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await process.wait()

    async def _cleanup_external(self, run_id: str) -> None:
        pass

    async def cancel(self, run_id: str) -> bool:
        running = self._running.get(run_id)
        if running is None:
            return False
        running.cancelled = True
        await self._cleanup_external(run_id)
        await self._terminate(running)
        return True

    async def run(self, workspace: Workspace, command: list[str], timeout_seconds: float = 300, run_id: str | None = None) -> ExecutionResult:
        self._validate(workspace)
        if not command or not all(isinstance(arg, str) and "\0" not in arg for arg in command) or not command[0]:
            raise ValueError("Commands must be nonempty argument arrays without NUL bytes.")
        if not 0 < timeout_seconds <= 86_400:
            raise ValueError("Experiment timeout must be between 0 and 86400 seconds.")
        run_id = _identifier(run_id or str(uuid4()))
        if run_id in self._running or (workspace.metadata_path / f"{run_id}.json").exists():
            raise ValueError("Run identifiers must be unique.")
        commit = await self.snapshot(workspace)
        input_artifacts = [asdict(item) for item in await self.collect_artifacts(workspace)]
        actual_command = self._command(workspace, command, run_id)
        output_paths = [workspace.metadata_path / f"{run_id}.{stream}.log" for stream in ("stdout", "stderr")]
        start = time.monotonic()
        options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        try:
            process = await asyncio.create_subprocess_exec(*actual_command, cwd=workspace.path, env=sanitized_environment(workspace.path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **options)
        except FileNotFoundError as exc:
            raise ExecutionUnavailable(f"Experiment executable was not found: {command[0]}") from exc
        running = _Running(process)
        self._running[run_id] = running
        readers: list[asyncio.Task] = []
        timed_out = False

        async def capture(stream: asyncio.StreamReader, path: Path) -> str:
            captured = bytearray()
            truncated = False
            with path.open("wb") as output:
                while chunk := await stream.read(65_536):
                    remaining = max(0, self.max_log_bytes - len(captured))
                    kept = chunk[:remaining]
                    output.write(kept)
                    captured.extend(kept)
                    truncated = truncated or len(kept) < len(chunk)
                if truncated:
                    output.write(b"\n[Sapling: log capture limit reached]\n")
            return captured.decode("utf-8", errors="replace") + ("\n[Sapling: log capture limit reached]" if truncated else "")

        try:
            if os.name == "nt":
                try:
                    running.job = _WindowsJob(process.pid)
                except OSError as exc:
                    if process.returncode is None:
                        raise ExecutionUnavailable("Windows could not isolate the experiment process tree for cancellation.") from exc
            readers = [asyncio.create_task(capture(process.stdout, output_paths[0])), asyncio.create_task(capture(process.stderr, output_paths[1]))]
            async def wait_for_parent_exit() -> None:
                # asyncio Process.wait also waits for inherited pipe handles to
                # close. Descendants can keep those handles open after the parent
                # exits, so watch the process exit status before reclaiming them.
                while process.returncode is None:
                    await asyncio.sleep(0.025)
            try:
                await asyncio.wait_for(wait_for_parent_exit(), timeout_seconds)
            except TimeoutError:
                timed_out = True
            finally:
                # Always reclaim descendants, including children left behind by
                # a parent that exits successfully before they finish.
                await self._cleanup_external(run_id)
                await self._terminate(running)
            stdout, stderr = await asyncio.gather(*readers)
        except BaseException:
            await self._cleanup_external(run_id)
            await self._terminate(running)
            for reader in readers:
                reader.cancel()
            await asyncio.gather(*readers, return_exceptions=True)
            raise
        finally:
            self._running.pop(run_id, None)
        artifacts = await self.collect_artifacts(workspace)
        manifest_path = workspace.metadata_path / f"{run_id}.json"
        result = ExecutionResult(run_id, process.returncode if process.returncode is not None else -1, stdout, stderr, time.monotonic() - start, timed_out, running.cancelled, commit, str(output_paths[0]), str(output_paths[1]), str(manifest_path), artifacts, self.backend_name)
        manifest = asdict(result)
        manifest.update({"command": command, "timeout_seconds": timeout_seconds,
                         "parent_commit": workspace.parent_commit,
                         "source_experiment_id": workspace.source_experiment_id,
                         "source_commit": workspace.source_commit,
                         "inputs": input_artifacts,
                         "environment": {"backend": self.backend_name, "credentials_inherited": False,
                                         "platform": platform.platform(), "runtime_python": platform.python_version()}})
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    async def collect_artifacts(self, workspace: Workspace, patterns: list[str] | None = None) -> list[CollectedArtifact]:
        self._validate(workspace)
        if patterns and len(patterns) > 32:
            raise WorkspaceViolation("At most 32 artifact patterns may be collected.")
        for pattern in patterns or []:
            if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
                raise WorkspaceViolation("Artifact patterns must stay inside the workspace.")
        def collect() -> list[CollectedArtifact]:
            results, seen, total, scanned = [], set(), 0, 0
            for pattern in patterns or ["**/*"]:
                for path in workspace.path.glob(pattern):
                    scanned += 1
                    if scanned > self.max_artifact_files * 10:
                        raise WorkspaceViolation("The workspace contains too many entries to collect safely.")
                    relative = path.relative_to(workspace.path)
                    if path in seen or any(part in {".git", ".home", ".tmp", "__pycache__", ".venv", "node_modules"} for part in relative.parts):
                        continue
                    seen.add(path)
                    if path.is_symlink() or not path.resolve().is_relative_to(workspace.path.resolve()):
                        raise WorkspaceViolation("Artifacts cannot be links outside the workspace.")
                    if not path.is_file():
                        continue
                    if len(results) >= self.max_artifact_files:
                        raise WorkspaceViolation("The workspace exceeds the artifact file-count limit.")
                    size = path.stat().st_size
                    total += size
                    if total > self.max_artifact_bytes:
                        raise WorkspaceViolation("Collected artifacts exceed the configured size limit.")
                    digest = hashlib.sha256()
                    with path.open("rb") as stream:
                        while chunk := stream.read(1_048_576):
                            digest.update(chunk)
                    results.append(CollectedArtifact(str(path), relative.as_posix(), digest.hexdigest(), size))
            return results
        return await asyncio.to_thread(collect)


class LocalDockerBackend(LocalProcessBackend):
    backend_name = "local_docker"

    def __init__(self, root: str | Path, image: str = "python:3.12-slim", *, allow_network: bool = False, memory_mb: int = 2048, cpus: float = 2, gpus: str | None = None, **kwargs: object) -> None:
        super().__init__(root, **kwargs)
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.:/@-]{0,250}", image):
            raise ValueError("Invalid container image name.")
        if memory_mb < 64 or cpus <= 0:
            raise ValueError("Container CPU and memory limits must be positive.")
        if gpus is not None and not re.fullmatch(r"all|[1-9][0-9]*|device=[A-Za-z0-9,.-]+", gpus):
            raise ValueError("GPU selection must be all, a device count, or device= followed by device identifiers.")
        self.gpus = gpus
        self.image, self.allow_network, self.memory_mb, self.cpus = image, allow_network, memory_mb, cpus

    def _command(self, workspace: Workspace, command: list[str], run_id: str) -> list[str]:
        if not shutil.which("docker"):
            raise ExecutionUnavailable("Docker is not installed. Install Docker Desktop or explicitly enable host execution.")
        name = f"sapling-{run_id}".lower()
        return ["docker", "run", "--rm", "--pull=never", "--name", name, "--init", "--network", "bridge" if self.allow_network else "none", "--memory", f"{self.memory_mb}m", "--cpus", str(self.cpus), "--pids-limit", "128", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--read-only", "--tmpfs", "/tmp:rw,nosuid,size=256m", "--mount", f"type=bind,source={workspace.path},target=/workspace", "--workdir", "/workspace", "--env", "HOME=/tmp", "--env", "PYTHONUNBUFFERED=1", *(["--gpus", self.gpus] if self.gpus else []), self.image, *command]

    async def _cleanup_external(self, run_id: str) -> None:
        if not shutil.which("docker"):
            return
        process = await asyncio.create_subprocess_exec("docker", "rm", "--force", f"sapling-{run_id}".lower(), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        try:
            await asyncio.wait_for(process.wait(), 15)
        except TimeoutError:
            process.kill()
            await process.wait()
