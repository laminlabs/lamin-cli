import asyncio
import os
import subprocess
import sys
from pathlib import Path

import click
import lamin_cli._mcp as mcp_module
import pytest
from fastmcp import Client, FastMCP


def test_proxy_mirrors_upstream_tools_and_calls(monkeypatch, capsys):
    import fastmcp.server.providers.proxy as proxy_module

    upstream = FastMCP("upstream")

    @upstream.tool()
    def echo(value: str) -> str:
        """Echo a value."""
        return value

    original_proxy_client = proxy_module.ProxyClient
    client_arguments = []
    token = "private-token-sentinel"

    def create_test_client(transport, **kwargs):
        client_arguments.append((transport, kwargs))
        return original_proxy_client(upstream)

    monkeypatch.setattr(proxy_module, "ProxyClient", create_test_client)
    monkeypatch.setattr(
        mcp_module,
        "_current_instance",
        lambda: ("instance-id", "https://hub.example/api"),
    )
    monkeypatch.setattr(mcp_module, "_access_token", lambda: (token, True))

    proxy = mcp_module._create_mcp_proxy()

    async def exercise_proxy():
        async with Client(upstream) as client:
            upstream_tools = await client.list_tools()
        async with Client(proxy) as client:
            tools = await client.list_tools()
            result = await client.call_tool("echo", {"value": "hello"})
        return upstream_tools, tools, result

    upstream_tools, tools, result = asyncio.run(exercise_proxy())

    assert proxy.provider_error_strategy == "raise"
    assert tools == upstream_tools
    assert result.data == "hello"
    assert all(kwargs["auth"] == token for _, kwargs in client_arguments)
    assert all(
        transport == "https://hub.example/api/mcp" for transport, _ in client_arguments
    )
    captured = capsys.readouterr()
    assert token not in captured.out
    assert token not in captured.err


def test_proxy_requires_login(monkeypatch):
    monkeypatch.setattr(
        mcp_module,
        "_current_instance",
        lambda: ("instance-id", "https://hub.example/api"),
    )
    monkeypatch.setattr(mcp_module, "_access_token", lambda: (None, False))

    with pytest.raises(click.ClickException, match="lamin login"):
        mcp_module._create_mcp_proxy()


def test_mcp_import_and_runtime_keep_stdout_clean_in_production():
    cli_root = Path(__file__).resolve().parents[2]
    setup_root = cli_root.parent / "lamindb-setup"
    env = os.environ.copy()
    python_path = os.pathsep.join((str(cli_root), str(setup_root)))
    if env.get("PYTHONPATH"):
        python_path = os.pathsep.join((python_path, env["PYTHONPATH"]))
    env.update({"LAMIN_ENV": "prod", "PYTHONPATH": python_path})
    script = """
import sys
from lamin_utils import logger
import lamin_cli._mcp as mcp_module

assert "lamindb" not in sys.modules

class Proxy:
    def run(self, *, transport, show_banner):
        assert transport == "stdio"
        assert show_banner is False
        logger.warning("runtime warning")

mcp_module._create_mcp_proxy = lambda: Proxy()
mcp_module.run_mcp_proxy()
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert "runtime warning" in result.stderr
