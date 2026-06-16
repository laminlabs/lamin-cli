# REST CLI

`lamin rest` exposes thin shell wrappers around the LaminDB REST API for the
currently connected instance.

Use it when you want to inspect an instance, query records, or issue a small
mutation from a terminal or script without importing `lamindb`.

```bash
lamin connect laminlabs/lamin-ops
lamin rest --help
```

## Metadata

Read instance-level metadata.

```bash
lamin rest schema
lamin rest schema core artifact --compact

lamin rest statistics
lamin rest statistics --model core.ULabel --model core.Artifact

lamin rest relation-counts core artifact 123
```

## Reads

List objects or get one object by numeric id or uid.

```bash
lamin rest list core artifact --select uid --select key --limit 10

lamin rest list core artifact \
  --select uid \
  --select key \
  --search training \
  --limit 10

lamin rest get core artifact j2qX8G9a --select uid --select key
```

Selects can traverse relations.

```bash
lamin rest list core artifact \
  --select uid \
  --select key \
  --select 'run(transform(uid,key))'
```

Search fields can also traverse relations. Relation paths use dot notation.

```bash
lamin rest list core artifact \
  --search training \
  --search-in ulabels.name \
  --limit 10
```

Filters are JSON objects passed through to the REST API.

```bash
lamin rest list core record \
  --filter '{"and":[{"is_type":{"eq":true}},{"name":{"contains":"dataset"}}]}' \
  --select uid \
  --select name
```

## Mutations

Mutations write to the currently connected instance.

```bash
lamin rest insert core ulabel --records '{"name":"treated"}'

lamin rest upsert core ulabel \
  --conflict-column name \
  --records '[{"name":"treated"}]'

lamin rest update core ulabel abc12345 \
  --values '{"description":"updated"}'

lamin rest delete core ulabel abc12345
```

Batch updates identify rows by one or more index columns.

```bash
lamin rest update core project \
  --index-column uid \
  --records '[{"uid":"abc12345","description":"updated"}]'
```

Batch deletes accept a list of identifier objects.

```bash
lamin rest delete core recordrecord \
  --records '[{"record_id":1,"feature_id":2,"value_id":3}]'
```

## JSON input

Options that accept JSON also accept `@path` for a file and `-` for stdin.

```bash
lamin rest insert core project --records @projects.json
cat body.json | lamin rest list core artifact --body -
```

Several options can be repeated or passed as a JSON list.

```bash
lamin rest list core artifact --select uid --select key
lamin rest list core artifact --select '["uid","key"]'
```

## Design

The commands are intentionally low-level wrappers. They build the REST path and
request body, authenticate through the existing LaminDB setup client, and print
JSON. The REST API remains responsible for model semantics and validation.
