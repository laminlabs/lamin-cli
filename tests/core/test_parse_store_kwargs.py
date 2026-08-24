import click
import pytest
from lamin_cli._save import parse_store_kwargs


def test_parse_store_kwargs_none():
    assert parse_store_kwargs(None) is None


def test_parse_store_kwargs_dict_passthrough():
    kwargs = {"acl": "public-read"}
    assert parse_store_kwargs(kwargs) is kwargs


def test_parse_store_kwargs_json_object():
    assert parse_store_kwargs('{"acl": "public-read", "chunksize": 8}') == {
        "acl": "public-read",
        "chunksize": 8,
    }


def test_parse_store_kwargs_invalid_json():
    with pytest.raises(click.ClickException, match="must be valid JSON"):
        parse_store_kwargs("{acl: public-read}")


def test_parse_store_kwargs_not_object():
    with pytest.raises(click.ClickException, match="must be a JSON object"):
        parse_store_kwargs('["acl"]')
