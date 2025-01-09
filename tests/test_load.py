import subprocess

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
    result = subprocess.run(
        "lamin load"
        " 'https://lamin.ai/laminlabs/arrayloader-benchmarks/transform/1GCKs8zLtkc85zKv'"
        " --with-env",
        shell=True,
        capture_output=True,
    )
    assert result.returncode == 0

    # partial uid
    result = subprocess.run(
        "lamin load transform --uid 1GCKs8zLtkc85z --with-env",
        shell=True,
        capture_output=True,
    )
    assert result.returncode == 0


def test_load_artifact():
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
