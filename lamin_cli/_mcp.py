import sys
from contextlib import redirect_stdout

import click
from lamin_utils import logger

from .hub._client import _access_token, _current_instance


def _create_mcp_proxy():
    try:
        from fastmcp.server.providers.proxy import FastMCPProxy, ProxyClient
    except ModuleNotFoundError as error:
        raise click.ClickException(
            "MCP support is not installed. Run `pip install 'lamindb[mcp]'`."
        ) from error

    _, api_url = _current_instance()
    access_token, _ = _access_token()
    if access_token is None:
        raise click.ClickException("No access token found. Run `lamin login`.")

    def create_upstream_client() -> ProxyClient:
        return ProxyClient(
            f"{api_url}/mcp",
            auth=access_token,
        )

    return FastMCPProxy(
        client_factory=create_upstream_client,
        provider_error_strategy="raise",
    )


def run_mcp_proxy() -> None:
    # MCP's stdio transport reserves stdout for protocol messages. Rebind Lamin's
    # root logger before settings lookup, authentication, or FastMCP startup.
    with redirect_stdout(sys.stderr):
        logger.set_handler()
        proxy = _create_mcp_proxy()

    proxy.run(transport="stdio", show_banner=False)
