from lamin_cli._stage import decompose_url


def test_decompose_url():
    url = "https://lamin.ai/laminlabs/arrayloader-benchmarks/record/core/Transform?uid=1GCKs8zLtkc85zKv"  # noqa
    entity, uid = decompose_url(url)
    assert entity == "transform"
    assert uid == "1GCKs8zLtkc85zKv"
