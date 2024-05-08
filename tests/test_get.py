from lamin_cli._get import decompose_url
import subprocess


def test_decompose_url():
    url = "https://lamin.ai/laminlabs/arrayloader-benchmarks/transform/1GCKs8zLtkc85zKv"  # noqa
    result = decompose_url(url)
    instance_slug, entity, uid = result
    assert instance_slug == "laminlabs/arrayloader-benchmarks"
    assert entity == "transform"
    assert uid == "1GCKs8zLtkc85zKv"


def test_get_transform():
    result = subprocess.run(
        "lamin get"
        " 'https://lamin.ai/laminlabs/arrayloader-benchmarks/transform/1GCKs8zLtkc85zKv'",  # noqa
        shell=True,
        capture_output=True,
    )
    assert result.returncode == 0


def test_get_artifact():
    result = subprocess.run(
        "lamin get"
        " 'https://lamin.ai/laminlabs/lamindb-dev-datasets/artifact/c3MdAYmlMj7FT39KD0KV'",  # noqa
        shell=True,
        capture_output=True,
    )
    assert result.returncode == 0
