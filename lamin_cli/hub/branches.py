from __future__ import annotations

from typing import Any

from ._client import module_model_path, request_json

BRANCH_SELECT = ["name", "created_at", "_status_code", "created_by(handle)"]
BRANCH_COLUMNS = ["name", "created_at", "change request", "created_by"]
BRANCH_CODE_TO_STATUS: dict[int, str] = {
    -2: "closed",
    -1: "merged",
    0: "standalone",
    1: "draft",
    2: "review",
}


def list_branches(limit: int = 100) -> list[dict[str, Any]]:
    data = request_json(
        "post",
        path=module_model_path("core", "branch"),
        params={"limit_to_many": 10, "limit": limit, "offset": 0},
        body={
            "select": BRANCH_SELECT,
            "order_by": [{"field": "id", "descending": True}],
        },
    )
    if not isinstance(data, list):
        return []
    records: list[dict[str, Any]] = []
    for record in data:
        if not isinstance(record, dict):
            continue
        status_code = record.get("_status_code")
        normalized_status = (
            BRANCH_CODE_TO_STATUS.get(status_code, "standalone")
            if isinstance(status_code, int)
            else "standalone"
        )
        created_by = record.get("created_by")
        records.append(
            {
                "name": record.get("name"),
                "created_at": record.get("created_at"),
                "change request": (
                    "" if normalized_status == "standalone" else normalized_status
                ),
                "created_by": (
                    created_by.get("handle")
                    if isinstance(created_by, dict)
                    else created_by
                ),
            }
        )
    return records


def create_branch(name: str, description: str | None = None) -> dict[str, Any]:
    data = request_json(
        "put",
        path=module_model_path("core", "branch"),
        body={"name": name, "description": description},
    )
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    if isinstance(data, dict):
        return data
    return {"name": name}
