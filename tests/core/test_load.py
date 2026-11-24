import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import click
import lamindb as ln
import lamindb_setup as ln_setup
import pytest
from lamin_cli._load import decompose_url, load


@pytest.fixture
def load_record(monkeypatch, tmp_path):
    record = SimpleNamespace(
        uid="testuid0000000000",
        key="script.py",
        name="note",
        type=None,
        source_code="# loaded content",
        latest_run=None,
        ablocks=MagicMock(),
    )
    record.ablocks.filter.return_value.order_by.return_value.first.return_value = (
        SimpleNamespace(content="# loaded content")
    )
    records = MagicMock()
    records.__len__.return_value = 1
    records.first.return_value = record
    records.filter.return_value = records
    registry = SimpleNamespace(objects=records)
    monkeypatch.setitem(
        sys.modules,
        "lamindb",
        SimpleNamespace(
            Transform=registry,
            Record=registry,
            Project=registry,
            models=SimpleNamespace(Block=record.ablocks),
            settings=SimpleNamespace(track_run_inputs=True),
        ),
    )
    monkeypatch.setattr(ln_setup, "connect", lambda instance: None)
    monkeypatch.setattr(
        ln_setup,
        "settings",
        SimpleNamespace(
            instance=SimpleNamespace(slug="test/instance"),
            dev_dir=None,
        ),
    )
    monkeypatch.setattr(
        "lamin_cli._load.get_current_run_file", lambda: tmp_path / "no-run"
    )
    monkeypatch.setattr(
        "builtins.input", lambda _: pytest.fail("Unexpected overwrite prompt")
    )
    return record


@pytest.mark.parametrize("use_dev_dir", [False, True])
@pytest.mark.parametrize(
    "entity, path, parent, allowed",
    [
        ("transform", "nested/output", None, True),
        ("transform", "{root}/nested/output", None, True),
        ("transform", "{outside}/new/output", None, False),
        ("transform", "../dev-other/new/output", None, False),
        ("transform", "outside-link/new/output", None, False),
        ("transform", "file-link", None, False),
        ("transform", "inside-link/new/output", None, True),
        ("record", "nested/output", None, True),
        ("record", "../dev-other/new/output", None, False),
        ("record", "{outside}/new/output", None, False),
        ("record", "outside-link/new/output", None, False),
        ("record", "output", "topic", True),
        ("record", "output", "../dev-other/new", False),
        ("project", "output", "topic", True),
        ("project", "output", "../../dev-other/new", False),
        ("project", "file-link", None, False),
        ("readme", "README", None, False),
    ],
)
def test_load_path_containment(
    tmp_path, monkeypatch, load_record, use_dev_dir, entity, path, parent, allowed
):
    root, outside = tmp_path / "dev", tmp_path / "dev-other"
    (root / "subdir").mkdir(parents=True)
    outside.mkdir()
    monkeypatch.chdir(root / "subdir" if use_dev_dir else root)
    ln_setup.settings.dev_dir = root if use_dev_dir else None
    sentinel = outside / "sentinel"
    sentinel.write_text("unchanged")
    (root / "outside-link").symlink_to(outside, target_is_directory=True)
    (root / "inside-link").symlink_to(root / "subdir", target_is_directory=True)
    for filename in (
        "file-link.py",
        "file-link.md",
        "README.md",
        "Projects/file-link.md",
    ):
        link = root / filename
        link.parent.mkdir(exist_ok=True)
        link.symlink_to(sentinel)
    name = path.format(root=root, outside=outside)
    key = name + (".py" if entity == "transform" else ".md")
    load_record.key, load_record.name = key, name
    if parent is not None:
        load_record.type = SimpleNamespace(name=parent, uid="parent", type=None)
        if not use_dev_dir:
            allowed = True  # Note hierarchy is flattened outside a dev-dir.
    kwargs = {"key": "README.md"} if entity == "readme" else {"uid": load_record.uid}
    before = set(tmp_path.rglob("*"))
    if not allowed:
        with pytest.raises(click.ClickException, match="outside"):
            load("record" if entity == "readme" else entity, **kwargs)
        assert set(tmp_path.rglob("*")) == before
        assert sentinel.read_text() == "unchanged"
    else:
        result = load(entity, **kwargs)
        expected = root / key
        if parent is not None and use_dev_dir:
            expected = root / ("Projects" if entity == "project" else "") / parent / key
        assert result.resolve() == expected.resolve()
        assert result.read_text().endswith("# loaded content")
        if entity == "transform" and not use_dev_dir:
            assert result.is_absolute() == Path(key).is_absolute()


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

    instance_slug, entity, uid = decompose_url(
        "https://lamin.ai/laminlabs/lamindata/record/UrcIKR8v0ywim0pE"
    )
    assert instance_slug == "laminlabs/lamindata"
    assert entity == "record"
    assert uid == "UrcIKR8v0ywim0pE"


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
