from __future__ import annotations

from ._click import hub_group
from ._client import instance_url, module_model_path, request_json
from ._mutations import delete, insert, update, upsert
from ._query import get_record, list_records
from ._schema import schema
from ._statistics import relation_counts, statistics
from .branches import create_branch, list_branches


@hub_group
def hub():
    """Query the hub API.

    This is an EXPERIMENTAL feature.
    """


hub.add_command(list_records)
hub.add_command(get_record)
hub.add_command(schema)
hub.add_command(statistics)
hub.add_command(relation_counts)
hub.add_command(insert)
hub.add_command(upsert)
hub.add_command(update)
hub.add_command(delete)

__all__ = [
    "request_json",
    "hub",
    "instance_url",
    "module_model_path",
    "list_branches",
    "create_branch",
]
