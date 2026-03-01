"""MCP server entry point for the agent-native-speech Discord voice bot.

Exposes six tools following the CallMe pattern:
  - initiate_call   — join a voice channel and start a conversation
  - continue_call   — speak a message and listen for a reply
  - speak_to_user   — one-way TTS announcement (no STT)
  - end_call        — speak farewell and disconnect
  - add_correction  — register an STT word correction
  - list_corrections — retrieve all stored corrections

The MCP server runs in the main asyncio event loop (this module).
The Discord bot runs in a background thread via BotRunner.
CallManager bridges the two using BotRunner.run_coroutine().

Run with:
    python -m server.main
    python server/main.py

IMPORTANT (stdio transport): Never write to stdout.  All logging must go to
stderr or a file, otherwise JSON-RPC framing is corrupted.
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
from typing import Any

import mcp.server.stdio
from mcp import types
from mcp.server import InitializationOptions, NotificationOptions, Server

from server.call_manager import CallManager
from server.config import Config
from server.discord_bot import BotRunner
from server.stt_pipeline import STTPipeline
from server.tts_engine import TTSEngine

# ---------------------------------------------------------------------------
# Logging — stderr only; stdout is owned by the MCP stdio transport
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

_TOOLS: list[types.Tool] = [
    types.Tool(
        name="initiate_call",
        description=(
            "Join a Discord voice channel and initiate a conversation with the user. "
            "Speaks the opening message via TTS, then listens for the user's reply via STT. "
            "Returns a call_id used by subsequent tools and the user's first transcript."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "channel_id": {
                    "type": "string",
                    "description": (
                        "Discord voice channel ID to join. "
                        "If omitted, the server's default_channel_id is used."
                    ),
                },
                "message": {
                    "type": "string",
                    "description": "Opening message to speak to the user.",
                },
            },
            "required": ["message"],
        },
    ),
    types.Tool(
        name="continue_call",
        description=(
            "Speak a message to the user during an active call and listen for their reply. "
            "Returns the STT transcript of the user's response."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "call_id": {
                    "type": "string",
                    "description": "Active call session ID returned by initiate_call.",
                },
                "message": {
                    "type": "string",
                    "description": "Message to speak to the user.",
                },
            },
            "required": ["call_id", "message"],
        },
    ),
    types.Tool(
        name="speak_to_user",
        description=(
            "Speak a one-way message to the user without waiting for a response. "
            "Useful for status updates or notifications during an active call."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "call_id": {
                    "type": "string",
                    "description": "Active call session ID.",
                },
                "message": {
                    "type": "string",
                    "description": "Message to speak.",
                },
            },
            "required": ["call_id", "message"],
        },
    ),
    types.Tool(
        name="end_call",
        description=(
            "Speak a farewell message, leave the voice channel, and clean up the session. "
            "Returns the total duration of the call in seconds."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "call_id": {
                    "type": "string",
                    "description": "Active call session ID to terminate.",
                },
                "message": {
                    "type": "string",
                    "description": "Farewell message to speak before disconnecting.",
                },
            },
            "required": ["call_id", "message"],
        },
    ),
    types.Tool(
        name="add_correction",
        description=(
            "Register an STT word correction. "
            "When the speech-to-text engine consistently mishears a word "
            "(e.g. a name or technical term), this stores a replacement so future "
            "transcripts are automatically corrected."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "wrong": {
                    "type": "string",
                    "description": "The word as incorrectly transcribed by the STT engine.",
                },
                "right": {
                    "type": "string",
                    "description": "The correct word to substitute.",
                },
            },
            "required": ["wrong", "right"],
        },
    ),
    types.Tool(
        name="list_corrections",
        description="Return all stored STT word corrections as a JSON object.",
        inputSchema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ),
]


# ---------------------------------------------------------------------------
# Handler factories — built after config/manager are initialised
# ---------------------------------------------------------------------------

def _make_list_tools_handler():
    """Return the list_tools handler function."""

    async def handle_list_tools(
        ctx: Any,
        params: types.PaginatedRequestParams | None,
    ) -> types.ListToolsResult:
        """Enumerate all available tools."""
        return types.ListToolsResult(tools=_TOOLS)

    return handle_list_tools


def _make_call_tool_handler(manager: CallManager, config: Config):
    """Return the call_tool handler bound to *manager* and *config*."""

    async def handle_call_tool(
        ctx: Any,
        params: types.CallToolRequestParams,
    ) -> types.CallToolResult:
        """Dispatch incoming tool calls to the appropriate CallManager method."""
        args: dict[str, Any] = params.arguments or {}
        name = params.name

        try:
            result = await _dispatch(name, args, manager, config)
        except KeyError as exc:
            return _error_result(str(exc))
        except ValueError as exc:
            return _error_result(str(exc))
        except Exception as exc:
            log.exception("Unhandled error in tool %r", name)
            return _error_result(f"Internal error: {exc}")

        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text=json.dumps(result, indent=2),
                )
            ]
        )

    return handle_call_tool


async def _dispatch(
    name: str,
    args: dict[str, Any],
    manager: CallManager,
    config: Config,
) -> dict[str, Any]:
    """Route a tool call to the correct CallManager method.

    Raises:
        ValueError: For unknown tool names or missing required arguments.
        KeyError: Propagated from CallManager for invalid call IDs.
    """
    if name == "initiate_call":
        message = _require(args, "message")
        # channel_id is optional; fall back to configured default
        raw_channel = args.get("channel_id")
        if raw_channel is not None:
            channel_id = int(raw_channel)
        elif config.default_channel_id is not None:
            channel_id = config.default_channel_id
        else:
            raise ValueError(
                "channel_id is required when no default_channel_id is configured"
            )
        return await manager.initiate_call(channel_id=channel_id, message=message)

    if name == "continue_call":
        call_id = _require(args, "call_id")
        message = _require(args, "message")
        return await manager.continue_call(call_id=call_id, message=message)

    if name == "speak_to_user":
        call_id = _require(args, "call_id")
        message = _require(args, "message")
        return await manager.speak_to_user(call_id=call_id, message=message)

    if name == "end_call":
        call_id = _require(args, "call_id")
        message = _require(args, "message")
        return await manager.end_call(call_id=call_id, message=message)

    if name == "add_correction":
        wrong = _require(args, "wrong")
        right = _require(args, "right")
        # Synchronous — no await needed
        return manager.add_correction(wrong=wrong, right=right)

    if name == "list_corrections":
        return manager.list_corrections()

    raise ValueError(f"Unknown tool: {name!r}")


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _require(args: dict[str, Any], key: str) -> Any:
    """Return args[key] or raise ValueError with a helpful message."""
    if key not in args or args[key] is None:
        raise ValueError(f"Missing required argument: '{key}'")
    return args[key]


def _error_result(message: str) -> types.CallToolResult:
    """Wrap an error message in a CallToolResult with isError=True."""
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=message)],
        isError=True,
    )


# ---------------------------------------------------------------------------
# Bootstrap & main
# ---------------------------------------------------------------------------

def _load_and_validate_config() -> Config:
    """Load Config from environment variables and abort on validation errors."""
    config = Config.from_env()
    errors = config.validate()
    if errors:
        for error in errors:
            log.error("Configuration error: %s", error)
        sys.exit(1)
    return config


async def run() -> None:
    """Initialise all components and run the MCP server until shutdown."""
    config = _load_and_validate_config()
    log.info("Configuration loaded (token=***%s)", config.discord_token[-4:])

    # Start the Discord bot in its background thread
    bot_runner = BotRunner(config)
    bot_runner.start()
    log.info("Discord bot thread started")

    # Create shared STT pipeline (VAD + Whisper + LLM correction)
    stt_pipeline = STTPipeline(config)
    log.info("STT pipeline initialised")

    # Wire the CorrectionManager into the bot so slash commands can access it
    bot_runner.bot.set_correction_manager(stt_pipeline.correction_manager)
    log.info("CorrectionManager wired into Discord bot")

    # Create shared TTS engine (Qwen3-TTS, lazy-loaded on first use)
    tts_engine = TTSEngine(config.tts)
    log.info("TTS engine initialised")

    if config.preload_models:
        log.info("Pre-loading models (PRELOAD_MODELS=true)...")
        stt_pipeline.warmup()
        tts_engine.warmup()
        log.info("All models pre-loaded and warmed up")

    manager = CallManager(bot_runner, stt_pipeline, tts_engine)

    # Build the MCP server with pre-bound handlers
    server = Server(
        "agent-native-speech",
        on_list_tools=_make_list_tools_handler(),
        on_call_tool=_make_call_tool_handler(manager, config),
    )

    # Graceful shutdown on SIGINT / SIGTERM
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def _signal_handler(sig: signal.Signals) -> None:
        log.info("Received signal %s, initiating shutdown", sig.name)
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler, sig)

    log.info("Starting MCP stdio server")
    try:
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            serve_task = asyncio.create_task(
                server.run(
                    read_stream,
                    write_stream,
                    InitializationOptions(
                        server_name="agent-native-speech",
                        server_version="0.1.0",
                        capabilities=server.get_capabilities(
                            notification_options=NotificationOptions(),
                            experimental_capabilities={},
                        ),
                    ),
                ),
                name="mcp-serve",
            )
            shutdown_task = asyncio.create_task(
                shutdown_event.wait(), name="shutdown-sentinel"
            )

            done, pending = await asyncio.wait(
                {serve_task, shutdown_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            # Re-raise any unexpected exception from the serve task
            if serve_task in done and not serve_task.cancelled():
                exc = serve_task.exception()
                if exc is not None:
                    raise exc

    finally:
        log.info("Shutting down TTS engine")
        tts_engine.unload()
        log.info("Shutting down STT pipeline")
        stt_pipeline.unload()
        log.info("Shutting down Discord bot")
        bot_runner.shutdown()
        log.info("Shutdown complete")


def main() -> None:
    """Entry point — runs the async server loop."""
    asyncio.run(run())


if __name__ == "__main__":
    main()
