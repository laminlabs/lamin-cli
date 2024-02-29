from lamin_cli._stage import decompose_url
import subprocess


def test_decompose_url():
    url = "https://lamin.ai/laminlabs/arrayloader-benchmarks/transform/1GCKs8zLtkc85zKv"  # noqa
    instance_identifier, entity, uid = decompose_url(url)
    assert instance_identifier == "laminlabs/arrayloader-benchmarks"
    assert entity == "transform"
    assert uid == "1GCKs8zLtkc85zKv"


def test_stage():
    result = subprocess.run(
        "lamin stage"
        " 'https://lamin.ai/laminlabs/arrayloader-benchmarks/core/transform/1GCKs8zLtkc85zKv'",  # noqa
        shell=True,
        capture_output=True,
    )
    print(result)
    assert result.returncode == 0
