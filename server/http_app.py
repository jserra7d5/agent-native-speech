"""Starlette ASGI application for Streamable HTTP MCP transport.

Mounts the MCP server on ``/mcp`` and provides a ``/health`` endpoint.
The ``StreamableHTTPSessionManager`` handles multi-client session tracking
and transport lifecycle automatically.

Usage (from ``server/main.py``)::

    app = create_app(mcp_server)
    uvicorn.run(app, host=config.server.host, port=config.server.port)
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from mcp.server import Server as MCPServer
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

log = logging.getLogger(__name__)


def create_app(mcp_server: MCPServer) -> Starlette:
    """Build and return the Starlette ASGI application.

    Args:
        mcp_server: The MCP ``Server`` instance with tool handlers
            already registered.

    Returns:
        A Starlette ``Application`` ready for ``uvicorn.run()``.
    """
    session_manager = StreamableHTTPSessionManager(app=mcp_server)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        log.info("HTTP app lifespan starting")
        async with session_manager.run():
            yield
        log.info("HTTP app lifespan ended")

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    app = Starlette(
        lifespan=lifespan,
        routes=[
            Route("/health", health, methods=["GET"]),
            Mount("/mcp", app=session_manager.handle_request),
        ],
    )

    return app
