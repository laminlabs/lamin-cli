import subprocess
from pathlib import Path

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


def test_load_transform():
    import lamindb_setup as ln_setup

    print(ln_setup.settings.instance.slug)
    result = subprocess.run(
        "lamin load"
        " 'https://lamin.ai/laminlabs/lamin-dev/transform/VFYCIuaw2GsX0000'"
        " --with-env",
        shell=True,
        capture_output=True,
    )
    print(result.stdout.decode())
    print(result.stderr.decode())
    assert result.returncode == 0

    print(ln_setup.settings.instance.slug)

    path1 = Path("run-track-and-finish.py")
    path2 = Path("run-track-and-finish__requirements.txt")
    assert path1.exists()
    assert path2.exists()

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
    result = subprocess.run(
        "lamin get"
        " 'https://lamin.ai/laminlabs/lamin-site-assets/artifact/e2G7k9EVul4JbfsEYAy5'",
        shell=True,
        capture_output=True,
    )
    assert result.returncode == 0

    result = subprocess.run(
        "lamin load"
        " 'https://lamin.ai/laminlabs/lamin-site-assets/artifact/e2G7k9EVul4JbfsEYAy5'",
        shell=True,
        capture_output=True,
    )
    assert result.returncode == 0

    # partial uid
    result = subprocess.run(
        "lamin load artifact --uid e2G7k9EVul4JbfsEYA",
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
