"""Voice Agent init CLI — one-time setup wizard."""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    """Entry point for the `voice-agent` CLI."""
    parser = argparse.ArgumentParser(
        prog="voice-agent",
        description="Voice Agent — Discord voice interface for AI coding agents",
    )
    sub = parser.add_subparsers(dest="command")

    # ---- init subcommand (default) ----
    init_parser = sub.add_parser("init", help="Run setup wizard")
    init_parser.add_argument("--discord-token", dest="discord_token", help="Discord bot token")
    init_parser.add_argument("--tts-backend", dest="tts_backend", choices=["local", "elevenlabs"])
    init_parser.add_argument("--tts-voice", dest="tts_voice", help="Default voice name")
    init_parser.add_argument("--elevenlabs-api-key", dest="elevenlabs_api_key", help="ElevenLabs API key")
    init_parser.add_argument("--elevenlabs-voice-id", dest="elevenlabs_voice_id", help="ElevenLabs voice ID")
    init_parser.add_argument("--whisper-model", dest="whisper_model", help="Whisper model size")
    init_parser.add_argument("--speech-mode", dest="speech_mode", choices=["pause", "stop_token"])
    init_parser.add_argument("--stop-word", dest="stop_word", help="Stop word for stop-token mode")
    init_parser.add_argument("--default-cli", dest="default_cli", choices=["claude", "codex"])
    init_parser.add_argument("--terminal", help="Terminal emulator override")
    init_parser.add_argument("--server-host", dest="server_host", default="127.0.0.1")
    init_parser.add_argument("--server-port", dest="server_port", type=int, default=8765)
    init_parser.add_argument("--skip-mcp", dest="skip_mcp", action="store_true")
    init_parser.add_argument("--skip-daemon", dest="skip_daemon", action="store_true")
    init_parser.add_argument("--non-interactive", dest="non_interactive", action="store_true")

    # ---- serve subcommand ----
    serve_parser = sub.add_parser("serve", help="Start the voice agent server")
    serve_parser.add_argument("--transport", choices=["stdio", "http"], default="http")
    serve_parser.add_argument("--host", default=None)
    serve_parser.add_argument("--port", type=int, default=None)

    args = parser.parse_args()

    if args.command == "serve":
        _run_serve(args)
    else:
        # Default to init wizard (when no subcommand or explicit "init")
        _run_init(args)


def _run_serve(args) -> None:
    """Delegate to the main server entry point."""
    from server.main import main as serve_main
    serve_main()


def _run_init(args) -> None:
    """Run the interactive setup wizard and optional post-setup steps."""
    from server.init.mcp_register import register_all
    from server.init.systemd import (
        check_status,
        enable_and_start,
        install_service,
        is_systemd_available,
    )
    from server.init.wizard import run_wizard, write_config

    try:
        # Step 1: Run wizard
        config = run_wizard(args)
        config_path = write_config(config)
        print(f"\n  Config saved to {config_path}")

        # Step 2: MCP registration
        if not getattr(args, "skip_mcp", False):
            print("\nStep 9/9: Register MCP Server")
            server_url = (
                f"http://{config.get('SERVER_HOST', '127.0.0.1')}"
                f":{config.get('SERVER_PORT', '8765')}/mcp"
            )
            interactive = not getattr(args, "non_interactive", False)
            results = register_all(server_url, interactive=interactive)
            for cli, success in results.items():
                if success:
                    print(f"  Registered in: {cli}")
                else:
                    print(f"  Failed to register in: {cli}")

        # Step 3: Daemon setup
        if not getattr(args, "skip_daemon", False) and is_systemd_available():
            interactive = not getattr(args, "non_interactive", False)
            if interactive:
                print("\nDaemon Setup")
                from server.init.wizard import _prompt_bool
                do_install = _prompt_bool("Install systemd service for auto-start?")
            else:
                do_install = True

            if do_install:
                import os
                project_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                unit_path = install_service(project_dir, str(config_path))
                print(f"  Created {unit_path}")

                if interactive:
                    do_start = _prompt_bool("Start the daemon now?")
                else:
                    do_start = True

                if do_start:
                    if enable_and_start():
                        print("  Service started")
                    else:
                        print("  Warning: Failed to start service", file=sys.stderr)

        # Step 4: Summary
        print("\n=== Setup Complete ===")
        print("Next steps:")
        print("  - Join a Discord voice channel")
        print("  - View logs: journalctl --user -u voice-agent -f")

    except KeyboardInterrupt:
        print("\n\nSetup cancelled.")
        sys.exit(130)
