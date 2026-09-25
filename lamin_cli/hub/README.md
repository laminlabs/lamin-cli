## REST commands (high signal)

`lamin rest` provides low-level access to LaminHub instance REST endpoints for the
currently connected instance.

### Design principles

- Keep commands thin wrappers around endpoint paths and JSON payloads.
- Preserve endpoint semantics; avoid CLI-specific magic.
- Favor explicit, composable flags (`--select`, `--filter`, `--order-by`, etc.).
- Return JSON by default with optional markdown summaries where useful.

### Current scope

- `schema`: inspect schema metadata
- `statistics`: table/relation counts and instance size
- `relation-counts`: hidden convenience alias for object relation counts
- `list`: query multiple objects
- `get`: query one object
- `insert`: insert one or many objects
- `upsert`: insert-or-update by conflict columns
- `update`: partial update (single or batch)
- `delete`: delete (single or batch)

### Typical endpoint mapping

- `schema` -> `GET /instances/{id}/schema`
- `statistics` -> `GET /instances/{id}/statistics`
- `list` -> `POST /instances/{id}/modules/{module}/{model}`
- `get` -> `POST /instances/{id}/modules/{module}/{model}/{id_or_uid}`
- `insert` -> `PUT /instances/{id}/modules/{module}/{model}`
- `upsert` -> `PUT /instances/{id}/modules/{module}/{model}/upsert`
- `update` -> `PATCH /instances/{id}/modules/{module}/{model}/{uid}`
  or `PATCH /instances/{id}/modules/{module}/{model}/batch-update`
- `delete` -> `DELETE /instances/{id}/modules/{module}/{model}/{uid}`
  or `POST /instances/{id}/modules/{module}/{model}/batch-delete`

### Notes

- Auth is delegated to `lamindb_setup.core._hub_client.request_with_auth`.
- `schema` supports local caching keyed by `(instance_id, schema_id)`.
- These commands are intentionally low-level and can evolve with endpoint contracts.
