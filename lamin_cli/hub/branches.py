from __future__ import annotations

from typing import Any

import pandas as pd

from ._client import module_model_path, request_json

_BRANCH_COLUMNS = ["uid", "name", "description", "created_at"]


def list_branches(limit: int = 100) -> pd.DataFrame:
    data = request_json(
        "post",
        path=module_model_path("core", "branch"),
        params={"limit_to_many": 10, "limit": limit, "offset": 0},
        body={
            "select": _BRANCH_COLUMNS,
            "order_by": [{"field": "id", "descending": True}],
        },
    )
    records = data if isinstance(data, list) else []
    return pd.DataFrame(records, columns=_BRANCH_COLUMNS)


def create_branch(name: str, description: str | None = None) -> dict[str, Any]:
    data = request_json(
        "post",
        path="branches",
        body={"branch_name": name, "description": description},
    )
    if isinstance(data, dict):
        body = data.get("body")
        if isinstance(body, dict):
            branch = body.get("branch")
            if isinstance(branch, dict):
                return branch
        return data
    return {"name": name}
