from lamin_cli._stage import decompose_url
import subprocess


def test_decompose_url():
    url = "https://lamin.ai/laminlabs/arrayloader-benchmarks/record/core/Transform?uid=1GCKs8zLtkc85zKv"  # noqa
    entity, uid = decompose_url(url)
    assert entity == "transform"
    assert uid == "1GCKs8zLtkc85zKv"


def test_stage():
    result = subprocess.run(
        "lamin stage"
        " 'https://lamin.ai/laminlabs/arrayloader-benchmarks/record/core/Transform?uid=1GCKs8zLtkc85zKv'",  # noqa
        shell=True,
        capture_output=True,
    )
    print(result)
    assert result.returncode == 0
