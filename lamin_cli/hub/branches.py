from __future__ import annotations

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
