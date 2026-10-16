# Lamin CLI

A CLI to work with LaminDB instances.

The Lamin CLI provides the `lamin` command. It is installed as part of the central `lamindb` package, which is developed here: https://github.com/laminlabs/lamindb

## Installation

```bash
pip install lamindb
```

## Usage

Save a file to your current LaminDB instance:

```bash
lamin save my_table.csv --key my_tables/my_table.csv
```

## MCP

Install LaminDB with MCP support:

```bash
pip install "lamindb[mcp]"
```

Log in and connect to the LaminHub instance you want to use:

```bash
lamin login
lamin connect account/instance
```

The instance must be managed by LaminHub. Then configure your MCP client to
start Lamin over stdio (the exact settings screen varies by client):

```json
{
  "mcpServers": {
    "lamin": {
      "command": "lamin",
      "args": ["mcp"]
    }
  }
}
```

Codex uses `config.toml` instead of the JSON format above. Configure Codex from
the terminal:

```bash
codex mcp add lamin -- lamin mcp
```

To use staging Lamin settings, add the environment variable:

```bash
codex mcp add lamin --env LAMIN_ENV=staging -- lamin mcp
```

See the [`lamindb` README](https://github.com/laminlabs/lamindb) for more examples.

## Docs

Read the [docs](https://docs.lamin.ai/cli).
