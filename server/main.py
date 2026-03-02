"""MCP server entry point for the agent-native-speech Discord voice bot.

Exposes eight tools following the CallMe pattern:
  - initiate_call    -- join a voice channel and start a conversation
  - continue_call    -- speak a message and listen for a reply
  - speak_to_user    -- one-way TTS announcement (no STT)
  - end_call         -- speak farewell and disconnect
  - add_correction   -- register an STT word correction
  - list_corrections -- retrieve all stored corrections
  - set_speech_mode  -- switch between pause and stop_token listening modes
  - list_sessions    -- list all active agent sessions

Supports two transports:
  - HTTP (default): Starlette + StreamableHTTPSessionManager via uvicorn
  - stdio: legacy single-client MCP stdio transport

The MCP server runs in the main asyncio event loop (this module).
The Discord bot runs in a background thread via BotRunner.
SessionManager bridges the two using BotRunner.run_coroutine().

Run with:
    python -m server.main                    # HTTP transport (default)
    python -m server.main --transport stdio   # stdio transport

IMPORTANT (stdio transport): Never write to stdout.  All logging must go to
stderr or a file, otherwise JSON-RPC framing is corrupted.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
from typing import Any

import mcp.server.stdio
from mcp.server import Server
from mcp.types import Tool

from server.config import Config
from server.discord_bot import BotRunner
from server.session_manager import SessionManager
from server.speech_mode import SpeechModeManager
from server.stt_pipeline import STTPipeline
from server.tts_backend import TTSBackend

# ---------------------------------------------------------------------------
# Logging -- stderr only; stdout is owned by the MCP stdio transport
# ---------------------------------------------------------------------------
_log_file = open("/tmp/voice-agent.log", "a")
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stderr),
        logging.StreamHandler(_log_file),
    ],
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

_TOOLS: list[dict[str, Any]] = [
    {
        "name": "initiate_call",
        "description": (
            "Join a Discord voice channel and initiate a conversation with the user. "
            "Speaks the opening message via TTS, then listens for the user's reply via STT. "
            "Returns a call_id used by subsequent tools and the user's first transcript. "
            "Auto-detects the user's voice channel if no channel_id is provided."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "channel_id": {
                    "type": "string",
                    "description": (
                        "Discord voice channel ID to join. "
                        "If omitted, auto-detects the user's current voice channel."
                    ),
                },
                "message": {
                    "type": "string",
                    "description": "Opening message to speak to the user.",
                },
            },
            "required": ["message"],
        },
    },
    {
        "name": "continue_call",
        "description": (
            "Speak a message to the user during an active call and listen for their reply. "
            "Returns the STT transcript of the user's response."
        ),
        "inputSchema": {
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
    },
    {
        "name": "speak_to_user",
        "description": (
            "Speak a one-way message to the user without waiting for a response. "
            "Useful for status updates or notifications during an active call."
        ),
        "inputSchema": {
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
    },
    {
        "name": "end_call",
        "description": (
            "Speak a farewell message, leave the voice channel, and clean up the session. "
            "Returns the total duration of the call in seconds."
        ),
        "inputSchema": {
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
    },
    {
        "name": "add_correction",
        "description": (
            "Register an STT word correction. "
            "When the speech-to-text engine consistently mishears a word "
            "(e.g. a name or technical term), this stores a replacement so future "
            "transcripts are automatically corrected."
        ),
        "inputSchema": {
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
    },
    {
        "name": "list_corrections",
        "description": "Return all stored STT word corrections as a JSON object.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "set_speech_mode",
        "description": (
            "Set the speech completion mode. 'pause' uses silence detection, "
            "'stop_token' waits for a spoken keyword."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["pause", "stop_token"],
                    "description": "Speech completion mode.",
                },
                "stop_word": {
                    "type": "string",
                    "description": "Stop word for stop_token mode.",
                },
            },
            "required": ["mode"],
        },
    },
    {
        "name": "list_sessions",
        "description": "List all active agent sessions connected to the voice server.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "check_messages",
        "description": (
            "Check for queued voice messages from the user. Returns any pending "
            "messages that were sent while you were working. Call this when "
            "notified of pending voice messages."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]


# ---------------------------------------------------------------------------
# Handler registration -- uses decorator-based MCP Server API
# ---------------------------------------------------------------------------

def _register_handlers(
    server: Server,
    manager: SessionManager,
    config: Config,
    speech_mode_manager: SpeechModeManager,
) -> None:
    """Register list_tools and call_tool handlers on the MCP server."""

    @server.list_tools()
    async def handle_list_tools() -> list[Tool]:
        """Enumerate all available tools."""
        return [Tool(**t) for t in _TOOLS]

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict[str, Any] | None) -> list[dict[str, Any]]:
        """Dispatch incoming tool calls to the appropriate SessionManager method."""
        args = arguments or {}

        try:
            result = await _dispatch(name, args, manager, config, speech_mode_manager)
        except KeyError as exc:
            return [{"type": "text", "text": str(exc)}]
        except ValueError as exc:
            return [{"type": "text", "text": str(exc)}]
        except Exception as exc:
            log.exception("Unhandled error in tool %r", name)
            return [{"type": "text", "text": f"Internal error: {exc}"}]

        return [{"type": "text", "text": json.dumps(result, indent=2)}]


async def _dispatch(
    name: str,
    args: dict[str, Any],
    manager: SessionManager,
    config: Config,
    speech_mode_manager: SpeechModeManager,
) -> dict[str, Any]:
    """Route a tool call to the correct SessionManager method.

    Raises:
        ValueError: For unknown tool names or missing required arguments.
        KeyError: Propagated from SessionManager for invalid call IDs.
    """
    if name == "initiate_call":
        message = _require(args, "message")
        # channel_id resolution: explicit arg > config default > auto-detect
        raw_channel = args.get("channel_id")
        if raw_channel is not None:
            channel_id = int(raw_channel)
        elif config.default_channel_id is not None:
            channel_id = config.default_channel_id
        else:
            # Auto-detect: find the first voice channel with a non-bot user
            detected = manager._runner.run_coroutine(
                manager._runner.bot.find_user_voice_channel_any()
            )
            if detected is None:
                raise ValueError(
                    "No channel_id provided and no user found in any voice channel. "
                    "Join a voice channel first, or provide a channel_id."
                )
            channel_id = detected
        # Auto-register an agent session for this call
        session_name = args.get("session_name")
        agent_session = manager.register_session(session_name=session_name)
        return await manager.initiate_call(
            channel_id=channel_id, message=message,
            session_id=agent_session.session_id,
        )

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
        # Find and unregister the agent session linked to this call
        agent_session = manager._find_session_by_call_id(call_id)
        result = await manager.end_call(call_id=call_id, message=message)
        if agent_session:
            manager.unregister_session(agent_session.session_id)
        return result

    if name == "add_correction":
        wrong = _require(args, "wrong")
        right = _require(args, "right")
        return manager.add_correction(wrong=wrong, right=right)

    if name == "list_corrections":
        return manager.list_corrections()

    if name == "set_speech_mode":
        mode = _require(args, "mode")
        stop_word = args.get("stop_word")
        return speech_mode_manager.set_mode(mode, stop_word=stop_word)

    if name == "list_sessions":
        return {"sessions": manager.list_active_sessions()}

    if name == "check_messages":
        from server.check_messages import check_messages as _check_messages  # noqa: PLC0415
        # TODO: resolve session_id from MCP transport session context
        # For now, use the first active session as fallback
        session_id = args.get("session_id", "")
        if not session_id:
            sessions = manager.list_active_sessions()
            if sessions:
                session_id = sessions[0]["session_id"]
        return _check_messages(manager.switchboard, session_id)

    raise ValueError(f"Unknown tool: {name!r}")


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _require(args: dict[str, Any], key: str) -> Any:
    """Return args[key] or raise ValueError with a helpful message."""
    if key not in args or args[key] is None:
        raise ValueError(f"Missing required argument: '{key}'")
    return args[key]


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments for transport and config selection."""
    parser = argparse.ArgumentParser(
        description="agent-native-speech MCP voice server",
    )
    parser.add_argument(
        "--transport",
        choices=["http", "stdio"],
        default=None,
        help="Transport mode: 'http' (Streamable HTTP, default) or 'stdio' (legacy single-client)",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to config file (JSON or .env). Auto-detects if not specified.",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Bootstrap & main
# ---------------------------------------------------------------------------

def _load_and_validate_config(config_path: str | None = None) -> Config:
    """Load Config and abort on validation errors."""
    config = Config.load(config_path)
    errors = config.validate()
    if errors:
        for error in errors:
            log.error("Configuration error: %s", error)
        sys.exit(1)
    return config


def _create_tts_engine(config: Config) -> TTSBackend:
    """Factory: create the appropriate TTS backend based on config."""
    if config.tts.backend == "elevenlabs":
        from server.elevenlabs_tts import ElevenLabsTTSEngine  # noqa: PLC0415

        return ElevenLabsTTSEngine(
            api_key=config.elevenlabs_api_key,
            voice_id=config.tts.elevenlabs_voice_id,
            model_id=config.tts.elevenlabs_model_id,
            voices=config.tts.elevenlabs_voices or None,
        )

    # Default: local Qwen3-TTS
    from server.tts_engine import TTSEngine  # noqa: PLC0415
    from server.voice_profile import VoiceProfileRegistry  # noqa: PLC0415

    registry = VoiceProfileRegistry(config.tts)
    log.info(
        "Voice profile registry: %d profiles (%s)",
        len(registry.list_profiles()),
        ", ".join(p.name for p in registry.list_profiles()),
    )
    return TTSEngine(config.tts, registry)


def _init_components(config: Config) -> tuple[
    BotRunner, STTPipeline, TTSBackend, SpeechModeManager, SessionManager, Server
]:
    """Create and wire all shared components.

    Returns:
        Tuple of (bot_runner, stt_pipeline, tts_engine, speech_mode_manager,
        session_manager, mcp_server).
    """
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

    # Create speech mode manager and wire into the bot for slash commands
    speech_mode_manager = SpeechModeManager(config.speech_mode)
    bot_runner.bot.set_speech_mode_manager(speech_mode_manager)
    log.info("SpeechModeManager wired into Discord bot (mode=%s)", config.speech_mode.mode)

    # Create TTS engine (local Qwen3-TTS or ElevenLabs cloud)
    tts_engine = _create_tts_engine(config)
    log.info("TTS engine initialised (backend=%s)", config.tts.backend)

    if config.preload_models:
        log.info("Pre-loading models (PRELOAD_MODELS=true)...")
        stt_pipeline.warmup()
        tts_engine.warmup()
        log.info("All models pre-loaded and warmed up")

    # Create the session manager (wraps CallManager + VoicePool)
    session_manager = SessionManager(
        bot_runner, stt_pipeline, tts_engine, speech_mode_manager, config=config
    )

    # Create the spawn manager and wire into bot for /spawn command
    from server.spawn import SpawnManager  # noqa: PLC0415
    spawn_manager = SpawnManager(config)
    bot_runner.bot.set_spawn_manager(spawn_manager)
    bot_runner.bot.set_session_manager(session_manager)
    log.info("SpawnManager wired into Discord bot")

    # Build the MCP server and register tool handlers
    mcp_server = Server("agent-native-speech")
    _register_handlers(mcp_server, session_manager, config, speech_mode_manager)

    return (
        bot_runner, stt_pipeline, tts_engine,
        speech_mode_manager, session_manager, mcp_server,
    )


async def _run_stdio(
    mcp_server: Server,
    shutdown_event: asyncio.Event,
) -> None:
    """Run the MCP server over stdio transport."""
    log.info("Starting MCP stdio server")
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        serve_task = asyncio.create_task(
            mcp_server.run(
                read_stream,
                write_stream,
                mcp_server.create_initialization_options(),
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


async def _run_http(
    mcp_server: Server,
    config: Config,
    shutdown_event: asyncio.Event,
) -> None:
    """Run the MCP server over Streamable HTTP transport via uvicorn."""
    import uvicorn  # noqa: PLC0415

    from server.http_app import create_app  # noqa: PLC0415

    app = create_app(mcp_server)
    host = config.server.host
    port = config.server.port

    log.info("Starting MCP HTTP server on %s:%d", host, port)

    uv_config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(uv_config)

    serve_task = asyncio.create_task(server.serve(), name="uvicorn-serve")
    shutdown_task = asyncio.create_task(
        shutdown_event.wait(), name="shutdown-sentinel"
    )

    done, pending = await asyncio.wait(
        {serve_task, shutdown_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    # If shutdown was signalled, tell uvicorn to stop
    if shutdown_task in done:
        server.should_exit = True
        try:
            await serve_task
        except Exception:
            log.exception("Error during uvicorn shutdown")

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


async def run(transport: str | None = None, config_path: str | None = None) -> None:
    """Initialise all components and run the MCP server until shutdown."""
    config = _load_and_validate_config(config_path)
    log.info("Configuration loaded (token=***%s)", config.discord_token[-4:])

    # Determine transport: CLI flag > config > default
    if transport is None:
        transport = config.server.transport
    log.info("Transport mode: %s", transport)

    (
        bot_runner, stt_pipeline, tts_engine,
        speech_mode_manager, session_manager, mcp_server,
    ) = _init_components(config)

    # Graceful shutdown on SIGINT / SIGTERM
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def _signal_handler(sig: signal.Signals) -> None:
        log.info("Received signal %s, initiating shutdown", sig.name)
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler, sig)

    try:
        if transport == "stdio":
            await _run_stdio(mcp_server, shutdown_event)
        else:
            await _run_http(mcp_server, config, shutdown_event)
    finally:
        log.info("Shutting down TTS engine")
        tts_engine.unload()
        log.info("Shutting down STT pipeline")
        stt_pipeline.unload()
        log.info("Shutting down Discord bot")
        bot_runner.shutdown()
        log.info("Shutdown complete")


def main() -> None:
    """Entry point -- runs the async server loop."""
    cli_args = _parse_args()
    asyncio.run(run(transport=cli_args.transport, config_path=cli_args.config))


if __name__ == "__main__":
    main()
