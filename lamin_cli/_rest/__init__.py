from __future__ import annotations

from ._click import rest_group
from ._client import request_json
from ._mutations import delete, insert, update, upsert
from ._query import get_record, list_records
from ._schema import schema
from ._statistics import relation_counts, statistics


@rest_group
def rest():
    """Query the LaminHub REST API."""


rest.add_command(list_records)
rest.add_command(get_record)
rest.add_command(schema)
rest.add_command(statistics)
rest.add_command(relation_counts)
rest.add_command(insert)
rest.add_command(upsert)
rest.add_command(update)
rest.add_command(delete)

__all__ = ["request_json", "rest"]
