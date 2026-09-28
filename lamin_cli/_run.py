"""Implementation of `lamin run`: where to run, and running tracked locally."""

from __future__ import annotations

import codecs
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from lamin_utils import logger

if TYPE_CHECKING:
    from collections.abc import Callable

WHERE_ENV = "LAMIN_RUN_WHERE"
DEFAULT_WHERE = "local"
SCRIPT_SUFFIXES = {".py", ".pyw", ".sh", ".bash", ".zsh", ".r", ".R", ".Rmd", ".qmd"}

# lamindb run status codes, see `Run.status`
STATUS_COMPLETED = 0
STATUS_ERRORED = 1
STATUS_ABORTED = 2


class RunError(Exception):
    """Raised when a run cannot be started."""


@dataclass
class RunRequest:
    target: str
    args: list[str]
    project: str | None = None
    register_outputs: tuple[str, ...] = ()
    remount: bool = False
    image_url: str | None = None
    packages: str | None = None
    cpu: float | None = None
    gpu: str | None = None
    branch: str | None = None
    space: str | None = None
    upload_outputs: bool = False
    dry_run: bool = False


@dataclass(frozen=True)
class Executor:
    """A place where `lamin run` can execute a target."""

    name: str
    description: str
    run: Callable[[RunRequest], int]


# -- where ---------------------------------------------------------------------


def _where_setting_path() -> Path:
    from lamindb_setup.core._settings_store import settings_dir

    return Path(settings_dir) / "run-where.txt"


def read_where_setting() -> str | None:
    path = _where_setting_path()
    if not path.exists():
        return None
    value = path.read_text().strip()
    return value or None


def write_where_setting(value: str | None) -> None:
    path = _where_setting_path()
    if value is None:
        path.unlink(missing_ok=True)
        return
    validate_where(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value + "\n")


def validate_where(value: str) -> str:
    if value not in EXECUTORS:
        raise RunError(
            f"Unknown place to run {value!r}. Choose one of: {', '.join(EXECUTORS)}."
        )
    return value


def resolve_where(flag: str | None) -> tuple[str, str]:
    """Pick where to run: flag, then environment, then setting, then the default."""
    for value, source in (
        (flag, "--where"),
        (os.environ.get(WHERE_ENV) or None, WHERE_ENV),
        (read_where_setting(), "lamin settings run-where"),
    ):
        if value is not None:
            try:
                return validate_where(value), source
            except RunError as error:
                raise RunError(f"{error} (from {source})") from None
    return DEFAULT_WHERE, "default"


def dispatch(where: str, request: RunRequest) -> int:
    return EXECUTORS[validate_where(where)].run(request)


# -- argument translation ------------------------------------------------------


@dataclass
class Translation:
    """How a `lamin://` argument was turned into a local path."""

    uri: str
    local_path: Path
    via: Literal["mount", "local", "cache"]
    artifact: object = field(repr=False, default=None)
    in_current_instance: bool = True


def resolve_uri_to_local_path(
    uri: str, remount: bool = False, note: Callable[[str], None] | None = None
) -> Translation:
    """Resolve a URI to a readable local path, preferring mounts over the cache.

    Uses the same resolution as `lamin settings mount path`, so a mounted storage
    location is read in place, staleness is detected and refreshed, and only when the
    storage location is not mounted at all does it fall back to the cache.
    """
    from lamin_cli._uri import resolve_lamin_uri
    from lamin_cli.mount._lookup import (
        NotMounted,
        location_from_artifact,
        resolve_local_path,
    )

    resolved = resolve_lamin_uri(uri)
    location = location_from_artifact(resolved.artifact)
    try:
        local_path = resolve_local_path(location, remount=remount, note=note)
        via: Literal["mount", "local", "cache"] = (
            "local" if location.mount is None else "mount"
        )
    except NotMounted:
        # inputs are linked explicitly once the run exists, see _link_inputs
        local_path = Path(str(resolved.artifact.cache(is_run_input=False)))
        via = "cache"
    if resolved.subpath is not None:
        local_path = local_path / resolved.subpath
        if not local_path.exists():
            raise RunError(f"{resolved.subpath} does not exist in {uri}.")
    return Translation(
        uri=uri,
        local_path=local_path,
        via=via,
        artifact=resolved.artifact,
        in_current_instance=resolved.in_current_instance,
    )


def translate_argv(
    argv: list[str], remount: bool = False, note: Callable[[str], None] | None = None
) -> tuple[list[str], list[Translation]]:
    """Replace `lamin://` URIs in an argument vector with local paths.

    Both bare arguments and `--flag=lamin://...` are translated. Everything else is
    passed through untouched.
    """
    from lamin_cli._uri import is_lamin_uri

    translated: list[str] = []
    translations: list[Translation] = []
    cache: dict[str, Translation] = {}

    def resolve(uri: str) -> str:
        if uri not in cache:
            cache[uri] = resolve_uri_to_local_path(uri, remount=remount, note=note)
            translations.append(cache[uri])
        return str(cache[uri].local_path)

    for arg in argv:
        if is_lamin_uri(arg):
            translated.append(resolve(arg))
            continue
        flag, separator, value = arg.partition("=")
        if separator and flag.startswith("-") and is_lamin_uri(value):
            translated.append(f"{flag}={resolve(value)}")
            continue
        translated.append(arg)
    return translated, translations


def child_environment(run_uid: str, project: str | None, translations) -> dict:
    """Environment variables handed to the child process."""
    from lamin_cli.mount import _registry

    env = {
        # lamindb's `track()` links a script's own run to this one as its parent
        "LAMIN_INITIATED_BY_RUN_UID": run_uid,
        # the mapping lets a script translate URIs it reads from config files
        "LAMIN_INPUT_PATHS": json.dumps(
            {t.uri: str(t.local_path) for t in translations}
        ),
        "LAMIN_MOUNTS": json.dumps(
            {record.storage_root: record.mountpoint for record in _registry.prune()}
        ),
    }
    if project is not None:
        env["LAMIN_CURRENT_PROJECT"] = project
    return env


# -- local executor ------------------------------------------------------------


def classify_target(target: str) -> Literal["script", "executable"]:
    return "script" if Path(target).suffix in SCRIPT_SUFFIXES else "executable"


# how to run a script that is not executable itself, mirroring what users would type
INTERPRETERS: dict[str, tuple[str, ...]] = {
    ".py": ("python",),
    ".pyw": ("python",),
    ".sh": ("bash",),
    ".bash": ("bash",),
    ".zsh": ("zsh",),
    ".R": ("Rscript",),
    ".r": ("Rscript",),
    ".qmd": ("quarto", "render"),
    ".Rmd": ("quarto", "render"),
}


def _active_python_executable() -> str:
    """The python lamin resolves scripts to, i.e. `which python`, not lamin's own.

    Lamin may be installed in an isolated tool environment (pipx, uv tool, ...) that
    lacks a script's dependencies, so scripts and their environment are always
    resolved against the active environment on PATH instead.
    """
    from shutil import which

    return which("python") or which("python3") or sys.executable


def command_for(target: str) -> list[str]:
    """The argv prefix that runs a target, as the user would run it by hand.

    An executable file runs directly so that its shebang is respected. Otherwise a
    script is handed to the interpreter for its suffix: `lamin run train.py` behaves
    like `python train.py`. Python comes from PATH, i.e. the active environment,
    because lamin may be installed in an isolated tool environment that lacks the
    script's dependencies.
    """
    path = Path(target)
    if path.is_file() and os.access(path, os.X_OK):
        return [target]
    interpreter = INTERPRETERS.get(path.suffix)
    if interpreter is None:
        return [target]
    executable = interpreter[0]
    if executable == "python":
        executable = _active_python_executable()
    return [executable, *interpreter[1:], target]


def _probe_version(executable: str) -> str | None:
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    output = (result.stdout or result.stderr).strip()
    return output.splitlines()[0] if output else None


def _prepare_transform(
    target: str, kind: Literal["script", "executable"], branch, space
):
    """Create or reuse the transform, and probe the version of whatever runs it.

    The version is deliberately not stored on the transform: it's reused across
    runs (by content hash for a script, by key alone for an executable, which
    lamindb never auto-versions), so a later run with a different version would
    silently rewrite earlier runs' history. `_prepare_run` records it per run
    instead, in `run.params["tool_version"]`.
    """
    import lamindb as ln

    path = Path(target)
    if kind == "script":
        interpreter = command_for(target)[0]
        version = _probe_version(interpreter)
        transform = ln.Transform(
            key=path.name, source_code=path.read_text(), kind="script"
        )
    else:
        version = _probe_version(target)
        transform = ln.Transform(key=path.name, kind="pipeline")
    transform.branch = branch
    transform.space = space
    return transform.save(), version


def _track_child_python_environment(run) -> None:
    """Snapshot `pip freeze` of the interpreter a Python script runs under.

    Links it to the run as `run.environment`, mirroring what `ln.track()` does for
    its own process (see `lamindb.core._track_environment`), but for the child
    interpreter `lamin run` resolves the script to rather than lamin's own.
    """
    import lamindb as ln
    import lamindb_setup as ln_setup
    from lamindb_setup.core.hashing import hash_file

    executable = _active_python_executable()
    try:
        result = subprocess.run(
            [executable, "-m", "pip", "freeze"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        _note(f"could not track the Python environment: {error}")
        return
    if result.returncode != 0 or not result.stdout.strip():
        _note("could not track the Python environment: `pip freeze` returned nothing")
        return
    env_dir = ln_setup.settings.cache_dir / "environments" / f"run_{run.uid}"
    env_dir.mkdir(parents=True, exist_ok=True)
    env_path = env_dir / "run_env_pip.txt"
    env_path.write_text(result.stdout)

    # reuse an identical, previously uploaded environment rather than duplicating it
    _, env_hash, _ = hash_file(env_path)
    artifact = (
        ln.Artifact.filter(hash=env_hash, kind="__lamindb_run__")
        .exclude(size=0)
        .one_or_none()
    )
    if artifact is None:
        artifact = ln.Artifact(
            env_path,
            description="requirements.txt",
            kind="__lamindb_run__",
            run=False,
        )
        artifact.save(upload=True, print_progress=False)
    run.environment = artifact
    _note(f"tracked the Python environment ({executable})")


def status_code_for(returncode: int) -> int:
    """Map a process exit code onto lamindb's run status codes.

    Exit codes are not status codes: 2 is a usage error, not "aborted", and 127 has
    no status at all.
    """
    if returncode == 0:
        return STATUS_COMPLETED
    interrupted = {-signal.SIGINT, -signal.SIGTERM, 128 + signal.SIGINT}
    if returncode in interrupted:
        return STATUS_ABORTED
    return STATUS_ERRORED


def collect_output_paths(child_argv: list[str], register_outputs) -> list[Path]:
    """Outputs named explicitly or via the common `--out`/`--output` flags."""
    paths = [Path(output) for output in register_outputs]
    args = child_argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in {"--out", "--output"} and i + 1 < len(args):
            if not args[i + 1].startswith("-"):
                paths.append(Path(args[i + 1]))
                i += 2
                continue
        for prefix in ("--out=", "--output="):
            if arg.startswith(prefix):
                paths.append(Path(arg.removeprefix(prefix)))
        i += 1
    return paths


def _register_outputs(
    run, paths: list[Path], branch, space, *, upload_outputs: bool
) -> None:
    import lamindb as ln
    import lamindb_setup as ln_setup

    keep_local = ln_setup.settings.instance.keep_artifacts_local
    cwd = Path.cwd()
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen or not path.exists():
            continue
        seen.add(resolved)
        try:
            # preserve the directory structure the target wrote into, e.g.
            # "results/summary.csv" rather than just "summary.csv"
            key = str(resolved.relative_to(cwd))
        except ValueError:
            # outside cwd (e.g. an absolute path elsewhere): fall back to the name
            key = path.name
        artifact = ln.Artifact(path, key=key, run=run, branch=branch, space=space)
        artifact.save(upload=True if upload_outputs else None, print_progress=False)
        if keep_local and not upload_outputs:
            _note(
                f"{key!r} registered but kept local (instance policy"
                " keep_artifacts_local); pass --upload-outputs to force upload for"
                " this run"
            )


def _resolve_project(request: RunRequest):
    import lamindb as ln

    if request.project is None:
        return None
    from django.db.models import Q

    project_record = ln.Project.filter(
        Q(name=request.project) | Q(uid=request.project)
    ).one_or_none()
    if project_record is None:
        raise RunError(f"Project {request.project!r} not found.")
    return project_record


def _resolve_branch_and_space(request: RunRequest):
    """Resolve --branch/--space, falling back to the ambient local settings.

    Unlike inputs, which are fully explicit via a URI's own `?branch=&space=`
    query params, the transform/run/outputs otherwise silently inherit whatever
    branch/space the local machine happens to be pointed at.
    """
    import lamindb as ln
    import lamindb_setup as ln_setup

    branch = ln_setup.settings.branch
    if request.branch is not None:
        from django.db.models import Q

        branch = ln.Branch.filter(
            Q(name=request.branch) | Q(uid=request.branch)
        ).one_or_none()
        if branch is None:
            raise RunError(f"Branch {request.branch!r} not found.")
    space = ln_setup.settings.space
    if request.space is not None:
        from django.db.models import Q

        space = ln.Space.filter(
            Q(name=request.space) | Q(uid=request.space)
        ).one_or_none()
        if space is None:
            raise RunError(f"Space {request.space!r} not found.")
    return branch, space


def _link_inputs(run, translations: list[Translation]) -> None:
    for translation in translations:
        if not translation.in_current_instance:
            # linking would require transferring the record into this instance
            logger.warning(
                f"not linking {translation.uri} as a run input: it lives in another"
                " instance"
            )
            continue
        run.input_artifacts.add(translation.artifact)


def _note(message: str) -> None:
    print(message, file=sys.stderr)


def _pump(pipe, stream) -> None:
    """Copy a child's output into a stream, decoding chunks without splitting chars."""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    for chunk in iter(lambda: pipe.read1(65536), b""):
        stream.write(decoder.decode(chunk))
        stream.flush()
    tail = decoder.decode(b"", final=True)
    if tail:
        stream.write(tail)
        stream.flush()
    pipe.close()


def run_teed(argv: list[str], env: dict[str, str]) -> int:
    """Run a command, teeing its stdout and stderr into `sys.stdout`/`sys.stderr`.

    While a run is tracked these are lamindb's log handlers, which write to the
    terminal and the run logs. A subprocess writes to file descriptors directly and
    would bypass them, so its output is piped through here, keeping the two streams
    separate so that `lamin run tool -- ... > out.txt` still captures only stdout.
    """
    process = subprocess.Popen(
        argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    pumps = [
        threading.Thread(target=_pump, args=(process.stdout, sys.stdout), daemon=True),
        threading.Thread(target=_pump, args=(process.stderr, sys.stderr), daemon=True),
    ]
    for pump in pumps:
        pump.start()
    try:
        returncode = process.wait()
    except KeyboardInterrupt:
        # the terminal sent SIGINT to the child as well; give it time to clean up
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        returncode = 128 + signal.SIGINT
    for pump in pumps:
        pump.join(timeout=5)
    return returncode


def _lamin_logs_to_stderr() -> None:
    """Keep lamin's own messages off stdout, which belongs to the target."""
    import logging

    from lamin_utils import logger as lamin_logger

    for handler in lamin_logger.handlers:
        if isinstance(handler, logging.StreamHandler) and handler.stream is sys.stdout:
            handler.stream = sys.stderr


def _prepare_run(request: RunRequest):
    """Resolve inputs and record the run, before anything is executed."""
    import lamindb as ln

    from lamin_cli._uri import is_lamin_uri

    target = request.target
    if is_lamin_uri(target):
        target = str(
            resolve_uri_to_local_path(target, request.remount, _note).local_path
        )
    kind = classify_target(target)
    if kind == "script" and not Path(target).is_file():
        raise RunError(f"Script {target!r} does not exist.")

    project_record = _resolve_project(request)
    branch, space = _resolve_branch_and_space(request)

    target_args, translations = translate_argv(request.args, request.remount, _note)
    for translation in translations:
        _note(f"{translation.uri} -> {translation.local_path} (via {translation.via})")
    child_argv = [*command_for(target), *target_args]

    transform, tool_version = _prepare_transform(target, kind, branch, space)
    run = ln.Run(transform=transform)
    run.branch = branch
    run.space = space
    run.started_at = datetime.now(timezone.utc)
    run._status_code = -1
    # like ln.track(), record only the arguments, and keep lamin:// URIs rather than
    # machine-specific local paths so the call can be reproduced elsewhere
    run.cli_args = shlex.join(request.args)
    params: dict = {}
    if request.args:
        # a generic target's argv has no known flag/value structure (e.g. `wc -l`), so
        # unlike `ln.track(params=...)` we can't infer typed, named params; expose the
        # raw argv as a single param so it still shows up in the Hub's params table
        params["argv"] = list(request.args)
    if tool_version is not None:
        # per run, not on the transform: the same transform is reused across runs
        # (by key for an executable, which lamindb never auto-versions; by content
        # hash for a script whose text is unchanged), so a later run with a
        # different version would otherwise silently rewrite earlier runs' history
        params["tool_version"] = tool_version
    if params:
        run.params = params
    run.save()
    if project_record is not None:
        run.projects.add(project_record)
    _link_inputs(run, translations)
    return run, target, target_args, child_argv, translations, branch, space


def run_local(request: RunRequest) -> int:
    """Run a script or executable here, tracked as a lamindb run."""
    from lamin_cli.mount._lookup import stdout_to_stderr

    # everything lamin prints goes to stderr; stdout is the target's alone
    with stdout_to_stderr():
        import lamindb as ln
        from lamindb.core._finish import save_run_logs

        run, target, target_args, child_argv, translations, branch, space = (
            _prepare_run(request)
        )
        if Path(target).suffix in {".py", ".pyw"}:
            _track_child_python_environment(run)

    env = {
        **os.environ,
        **child_environment(run.uid, request.project, translations),
    }
    env.setdefault("PYTHONUNBUFFERED", "1")

    previous_run = ln.context.run
    ln.context._run = run
    # started outside the redirection, so that it tees the real stdout
    ln.context._stream_tracker.start(run)
    _lamin_logs_to_stderr()
    returncode = STATUS_ERRORED
    try:
        try:
            returncode = run_teed(child_argv, env)
        except FileNotFoundError:
            _note(f"error: {child_argv[0]!r} was not found")
            returncode = 127
        except PermissionError:
            _note(f"error: {child_argv[0]!r} is not executable")
            returncode = 126
        if returncode == 0:
            _register_outputs(
                run,
                collect_output_paths([target, *target_args], request.register_outputs),
                branch,
                space,
                upload_outputs=request.upload_outputs,
            )
        else:
            _note(f"{Path(target).name} exited with code {returncode}")
    finally:
        run._status_code = status_code_for(returncode)
        run.finished_at = datetime.now(timezone.utc)
        ln.context._stream_tracker.finish()
        ln.context._run = previous_run
        with stdout_to_stderr():
            run.save()
            save_run_logs(run, save_run=True)
    return returncode


def run_dry(request: RunRequest) -> int:
    """Report what `run_local` would do, without executing the target or saving.

    Useful before trusting a new mapping of inputs/outputs with a real, possibly
    shared instance: nothing here writes to the database or uploads anything.
    """
    import lamindb as ln

    from lamin_cli._uri import is_lamin_uri

    target = request.target
    if is_lamin_uri(target):
        translation = resolve_uri_to_local_path(target, request.remount, _note)
        target = str(translation.local_path)
    kind = classify_target(target)
    if kind == "script" and not Path(target).is_file():
        raise RunError(f"Script {target!r} does not exist.")

    project_record = _resolve_project(request)
    branch, space = _resolve_branch_and_space(request)

    target_args, translations = translate_argv(request.args, request.remount, _note)
    child_argv = [*command_for(target), *target_args]

    _note(f"would run: {shlex.join(child_argv)}")
    _note(f"branch: {branch.name!r} | space: {space.name!r}")
    if project_record is not None:
        _note(f"project: {project_record.name!r}")

    # constructing (not saving) a Transform still runs its own reuse-by-key /
    # reuse-by-content-hash lookup, so this reports the real outcome without a write
    path = Path(target)
    if kind == "script":
        interpreter = command_for(target)[0]
        version = _probe_version(interpreter)
        transform = ln.Transform(
            key=path.name, source_code=path.read_text(), kind="script"
        )
    else:
        version = _probe_version(target)
        transform = ln.Transform(key=path.name, kind="pipeline")
    outcome = "would create a new" if transform._state.adding else "would reuse the"
    _note(f"transform: {outcome} transform {path.name!r} (uid={transform.uid})")
    if version is not None:
        _note(f"tool_version: {version}")
    if kind == "script" and Path(target).suffix in {".py", ".pyw"}:
        _note(f"would snapshot the Python environment ({_active_python_executable()})")

    for translation in translations:
        if translation.in_current_instance:
            _note(
                f"input: {translation.uri} -> {translation.local_path}"
                f" (via {translation.via})"
            )
        else:
            _note(
                f"input: {translation.uri} -> {translation.local_path}"
                f" (via {translation.via}, in another instance, would NOT be linked)"
            )

    output_paths = collect_output_paths(
        [target, *target_args], request.register_outputs
    )
    cwd = Path.cwd()
    seen: set[Path] = set()
    for path in output_paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            key = str(resolved.relative_to(cwd))
        except ValueError:
            key = path.name
        if path.exists():
            _note(f"output: {path} -> key {key!r} (exists now; would be registered)")
        else:
            _note(
                f"output: {path} -> key {key!r} (does not exist yet; would only be"
                " registered if the run creates it)"
            )

    _note("dry run: nothing was executed or saved")
    return 0


# -- modal executor ------------------------------------------------------------


def run_modal(request: RunRequest) -> int:
    """Run a script on Modal, as `lamin run` did before `--where` existed."""
    import shutil

    from lamin_cli._uri import is_lamin_uri

    if request.project is None:
        raise RunError("--where modal needs --project, which names the Modal app.")
    if request.args:
        raise RunError("Passing arguments to the script is not supported on Modal yet.")
    if is_lamin_uri(request.target):
        raise RunError("Running a lamin:// target is not supported on Modal yet.")

    # imported only once the request is valid, as modal is an optional dependency
    from lamin_cli.compute.modal import Runner

    mount_dir = Path("./modal_mount_dir")
    mount_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(request.target, mount_dir)
    packages = (
        [package.strip() for package in request.packages.split(",")]
        if request.packages
        else []
    )
    Runner(
        local_mount_dir=mount_dir,
        app_name=request.project,
        packages=packages,
        image_url=request.image_url,
        cpu=request.cpu,
        gpu=request.gpu,
    ).run(mount_dir / Path(request.target).name)
    return 0


# adding a place to run, e.g. AWS Lambda, is one entry here
EXECUTORS: dict[str, Executor] = {
    "local": Executor("local", "this machine, tracked as a lamindb run", run_local),
    "modal": Executor("modal", "the Modal cloud (experimental)", run_modal),
}
