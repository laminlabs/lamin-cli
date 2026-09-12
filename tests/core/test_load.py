import os
import shutil
import subprocess
import time
from pathlib import Path

import click
import lamindb as ln
import lamindb_setup as ln_setup
from lamin_cli._load import decompose_url


def test_decompose_url():
    urls = [
        "https://lamin.ai/laminlabs/arrayloader-benchmarks/transform/1GCKs8zLtkc85zKv",
        "https://lamin.company.com/laminlabs/arrayloader-benchmarks/transform/1GCKs8zLtkc85zKv",
    ]
    for url in urls:
        result = decompose_url(url)
        instance_slug, entity, uid = result
        assert instance_slug == "laminlabs/arrayloader-benchmarks"
        assert entity == "transform"
        assert uid == "1GCKs8zLtkc85zKv"

    # run URL (run must be checked after transform so ".../transform/..." is not parsed as run)
    instance_slug, entity, uid = decompose_url(
        "https://lamin.ai/laminlabs/benchmarks/run/Abc123XyZ"
    )
    assert instance_slug == "laminlabs/benchmarks"
    assert entity == "run"
    assert uid == "Abc123XyZ"

    # project and ulabel URLs
    instance_slug, entity, uid = decompose_url(
        "https://lamin.ai/laminlabs/benchmarks/project/ProjUid123"
    )
    assert instance_slug == "laminlabs/benchmarks"
    assert entity == "project"
    assert uid == "ProjUid123"

    instance_slug, entity, uid = decompose_url(
        "https://lamin.ai/laminlabs/benchmarks/ulabel/ULabelUid456"
    )
    assert instance_slug == "laminlabs/benchmarks"
    assert entity == "ulabel"
    assert uid == "ULabelUid456"

    # branch URL (name or uid after branch/)
    instance_slug, entity, uid = decompose_url(
        "https://lamin.ai/laminlabs/benchmarks/branch/main"
    )
    assert instance_slug == "laminlabs/benchmarks"
    assert entity == "branch"
    assert uid == "main"


def test_load_transform():
    ln_setup.settings.dev_dir = None

    # check via a renamed instance
    result = subprocess.run(
        "lamin load"
        " 'https://lamin.ai/laminlabs/lamin-dev1072025/transform/EWKgIa9dJB0n'"
        " --with-env",
        shell=True,
        capture_output=True,
    )
    assert result.returncode == 0

    result = subprocess.run(
        "lamin load"
        " 'https://lamin.ai/laminlabs/lamin-dev/transform/VFYCIuaw2GsX0000'"
        " --with-env",
        shell=True,
        capture_output=True,
    )
    assert result.returncode == 0

    path1 = Path("run-track-and-finish.py")
    path2 = Path("run-track-and-finish__requirements.txt")
    assert path1.exists()
    assert path2.exists()

    subprocess.run("lamin connect laminlabs/lamin-dev", shell=True)

    # below will fail because it will say "these files already exist"
    result = subprocess.run(
        "lamin load transform --uid VFYCIuaw2GsX --with-env",
        shell=True,
        capture_output=True,
    )
    assert result.returncode == 1
    path1.unlink()
    path2.unlink()

    # partial uid
    result = subprocess.run(
        "lamin load transform --uid VFYCIuaw2GsX --with-env",
        shell=True,
        capture_output=True,
    )
    assert result.returncode == 0
    path1.unlink()
    path2.unlink()


def test_get_load_artifact():
    # test get
    result = subprocess.run(
        "lamin get"
        " 'https://lamin.ai/laminlabs/lamin-site-assets/artifact/e2G7k9EVul4JbfsEYAy5'",
        shell=True,
        capture_output=True,
    )
    assert result.returncode == 0

    # test load
    result = subprocess.run(
        "lamin load"
        " 'https://lamin.ai/laminlabs/lamin-site-assets/artifact/e2G7k9EVul4JbfsEYAy5'",
        shell=True,
        capture_output=True,
    )
    assert result.returncode == 0

    # connect to instance
    subprocess.run("lamin connect laminlabs/lamin-site-assets", shell=True)

    # partial uid
    result = subprocess.run(
        "lamin load artifact --uid e2G7k9EVul4JbfsEYA",
        shell=True,
        capture_output=True,
    )
    assert result.returncode == 0

    # by key
    result = subprocess.run(
        "lamin load --key blog/nbproject/elyra-completed-tutorial-pipeline.png",
        shell=True,
        capture_output=True,
    )
    assert result.returncode == 0


def test_load_collection():
    result = subprocess.run(
        "lamin load 'https://lamin.ai/laminlabs/lamindata/collection/2wUs6V1OuGzp5Ll4'",
        shell=True,
        capture_output=True,
    )
    assert result.returncode == 0


def test_load_errors_outside_branch_dir_in_worktree_mode(tmp_path: Path):
    previous_dev_dir = ln_setup.settings.dev_dir
    previous_worktree = ln_setup.settings.worktree
    worktree_parent = tmp_path / "worktrees"
    worktree_parent.mkdir(parents=True, exist_ok=True)
    try:
        ln_setup.settings.dev_dir = worktree_parent
        ln_setup.settings.worktree = True
        result = subprocess.run(
            "lamin load README.md",
            shell=True,
            capture_output=True,
            text=True,
            cwd=worktree_parent,
        )
        assert result.returncode != 0
        output = click.unstyle(result.stderr + result.stdout)
        for char in ("│", "╭", "╮", "╰", "╯", "─"):
            output = output.replace(char, " ")
        normalized = " ".join(output.lower().split())
        assert (
            "in worktree mode, branch is only defined inside a branch directory in"
            " your dev-dir" in normalized
        )
        assert "to run `lamin load`, please cd into a branch directory" in normalized
    finally:
        ln_setup.settings.worktree = previous_worktree
        ln_setup.settings.dev_dir = previous_dev_dir


def test_load_transform_uses_effective_dev_dir_in_worktree_mode(tmp_path: Path):
    previous_dev_dir = ln_setup.settings.dev_dir
    previous_worktree = ln_setup.settings.worktree
    previous_cwd = Path.cwd()
    worktree_parent = tmp_path / "worktrees"
    child_main = worktree_parent / "main"
    unique = time.time_ns()
    transform_key = f"imports/load-worktree-{unique}.py"
    branch_file = child_main / transform_key
    root_file = worktree_parent / transform_key
    worktree_parent.mkdir(parents=True, exist_ok=True)
    transform = None
    try:
        ln_setup.settings.dev_dir = worktree_parent
        ln_setup.settings.worktree = True
        child_main.mkdir(parents=True, exist_ok=True)
        os.chdir(child_main)
        transform = ln.Transform(
            key=transform_key,
            source_code="print('load worktree test')",
            kind="pipeline",
        ).save()
        result = subprocess.run(
            f"yes | lamin load transform --uid {transform.uid[:12]}",
            shell=True,
            capture_output=True,
            text=True,
            cwd=child_main,
        )
        assert result.returncode == 0, (
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert branch_file.exists()
        assert not root_file.exists()
    finally:
        os.chdir(previous_cwd)
        if branch_file.exists():
            branch_file.unlink()
        if root_file.exists():
            root_file.unlink()
        if transform is not None:
            transform.delete(permanent=True)
        if child_main.exists():
            shutil.rmtree(child_main)
        if worktree_parent.exists():
            shutil.rmtree(worktree_parent)
        ln_setup.settings.worktree = previous_worktree
        ln_setup.settings.dev_dir = previous_dev_dir
