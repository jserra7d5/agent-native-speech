"""Interactive setup wizard for voice-agent configuration."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "voice-agent"
CONFIG_PATH = CONFIG_DIR / "config.env"

VOICES = ["Ryan", "Aiden", "Vivian", "Serena", "Dylan", "Eric"]
WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3"]
TTS_BACKENDS = ["local", "elevenlabs"]
SPEECH_MODES = ["pause", "stop_token"]
CLI_CHOICES = ["claude", "codex"]

# Terminal emulators to try, in preference order
_TERMINALS = [
    "ghostty",
    "kitty",
    "alacritty",
    "wezterm",
    "gnome-terminal",
    "konsole",
    "xfce4-terminal",
    "xterm",
]


def _prompt(question: str, default: str = "", choices: list[str] | None = None) -> str:
    """Interactive prompt with optional choices and default."""
    if choices:
        for i, choice in enumerate(choices, 1):
            print(f"  [{i}] {choice}")
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"  > {question}{suffix}: ").strip()
    except EOFError:
        answer = ""
    if not answer:
        return default
    # If the user typed a number and we have choices, map it
    if choices and answer.isdigit():
        idx = int(answer) - 1
        if 0 <= idx < len(choices):
            return choices[idx]
    return answer


def _prompt_bool(question: str, default: bool = True) -> bool:
    """Yes/no prompt."""
    hint = "Y/n" if default else "y/N"
    try:
        answer = input(f"  {question} [{hint}]: ").strip().lower()
    except EOFError:
        answer = ""
    if not answer:
        return default
    return answer in ("y", "yes")


def _detect_terminal() -> str:
    """Auto-detect available terminal emulator."""
    for term in _TERMINALS:
        if shutil.which(term):
            return term
    return ""


def load_existing_config() -> dict[str, str]:
    """Load existing config.env if it exists for pre-filling defaults."""
    config: dict[str, str] = {}
    if not CONFIG_PATH.exists():
        return config
    for line in CONFIG_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            config[key.strip()] = value.strip()
    return config


def run_wizard(args) -> dict[str, str]:
    """Run the interactive setup wizard, return config dict.

    If args has non-None attribute values, use those instead of prompting.
    Pre-fills from existing config on re-run.
    """
    existing = load_existing_config()
    non_interactive = getattr(args, "non_interactive", False)
    config: dict[str, str] = {}

    print("\n=== Voice Agent Setup ===\n")

    # --- Step 1: Discord Bot Token ---
    print("Step 1/9: Discord Bot Token")
    token = getattr(args, "discord_token", None)
    if token:
        config["DISCORD_TOKEN"] = token
    elif non_interactive:
        if "DISCORD_TOKEN" not in existing:
            print("  Error: --discord-token is required in non-interactive mode", file=sys.stderr)
            sys.exit(2)
        config["DISCORD_TOKEN"] = existing["DISCORD_TOKEN"]
    else:
        default_token = existing.get("DISCORD_TOKEN", "")
        hint = " (leave blank to keep existing)" if default_token else ""
        print(f"  Enter your Discord bot token{hint}:")
        val = _prompt("Token", default=default_token)
        if not val:
            print("  Error: Discord token is required.", file=sys.stderr)
            sys.exit(1)
        config["DISCORD_TOKEN"] = val
    print()

    # --- Step 2: TTS Backend ---
    print("Step 2/9: TTS Backend")
    tts_backend = getattr(args, "tts_backend", None)
    if tts_backend:
        config["TTS_BACKEND"] = tts_backend
    elif non_interactive:
        config["TTS_BACKEND"] = existing.get("TTS_BACKEND", "local")
    else:
        print("  Choose your TTS backend:")
        default_backend = existing.get("TTS_BACKEND", "local")
        val = _prompt("Choice", default=default_backend, choices=TTS_BACKENDS)
        config["TTS_BACKEND"] = val if val in TTS_BACKENDS else default_backend
    print()

    # If elevenlabs, prompt for API key and voice ID
    if config["TTS_BACKEND"] == "elevenlabs":
        el_key = getattr(args, "elevenlabs_api_key", None)
        if el_key:
            config["ELEVENLABS_API_KEY"] = el_key
        elif non_interactive:
            if "ELEVENLABS_API_KEY" not in existing:
                print("  Error: --elevenlabs-api-key is required for elevenlabs backend in non-interactive mode", file=sys.stderr)
                sys.exit(2)
            config["ELEVENLABS_API_KEY"] = existing["ELEVENLABS_API_KEY"]
        else:
            default_el_key = existing.get("ELEVENLABS_API_KEY", "")
            print("  ElevenLabs API Key:")
            val = _prompt("API Key", default=default_el_key)
            if val:
                config["ELEVENLABS_API_KEY"] = val

        el_voice = getattr(args, "elevenlabs_voice_id", None)
        if el_voice:
            config["ELEVENLABS_VOICE_ID"] = el_voice
        elif non_interactive:
            if "ELEVENLABS_VOICE_ID" not in existing:
                print("  Error: --elevenlabs-voice-id is required for elevenlabs backend in non-interactive mode", file=sys.stderr)
                sys.exit(2)
            config["ELEVENLABS_VOICE_ID"] = existing["ELEVENLABS_VOICE_ID"]
        else:
            default_el_voice = existing.get("ELEVENLABS_VOICE_ID", "")
            print("  ElevenLabs Voice ID:")
            val = _prompt("Voice ID", default=default_el_voice)
            if val:
                config["ELEVENLABS_VOICE_ID"] = val
        print()

    # --- Step 3: Default Voice ---
    print("Step 3/9: Default Voice")
    tts_voice = getattr(args, "tts_voice", None)
    if tts_voice:
        config["TTS_VOICE"] = tts_voice
    elif non_interactive:
        config["TTS_VOICE"] = existing.get("TTS_VOICE", "Ryan")
    else:
        print(f"  Available voices: {', '.join(VOICES)}")
        default_voice = existing.get("TTS_VOICE", "Ryan")
        val = _prompt("Default voice", default=default_voice)
        config["TTS_VOICE"] = val
    print()

    # --- Step 4: Default CLI ---
    print("Step 4/9: Default CLI Client")
    default_cli = getattr(args, "default_cli", None)
    if default_cli:
        config["DEFAULT_CLI"] = default_cli
    elif non_interactive:
        config["DEFAULT_CLI"] = existing.get("DEFAULT_CLI", "claude")
    else:
        print("  Which CLI do you primarily use?")
        default_c = existing.get("DEFAULT_CLI", "claude")
        val = _prompt("Choice", default=default_c, choices=CLI_CHOICES)
        config["DEFAULT_CLI"] = val if val in CLI_CHOICES else default_c
    print()

    # --- Step 5: Speech Completion Mode ---
    print("Step 5/9: Speech Completion Mode")
    speech_mode = getattr(args, "speech_mode", None)
    if speech_mode:
        config["SPEECH_MODE"] = speech_mode
    elif non_interactive:
        config["SPEECH_MODE"] = existing.get("SPEECH_MODE", "pause")
    else:
        print("  Default speech mode:")
        default_sm = existing.get("SPEECH_MODE", "pause")
        print("  [1] pause - Silence-based turn detection (current behavior)")
        print("  [2] stop_token - Say a keyword to end your turn")
        try:
            answer = input(f"  > Choice [{default_sm}]: ").strip()
        except EOFError:
            answer = ""
        if answer == "1":
            config["SPEECH_MODE"] = "pause"
        elif answer == "2":
            config["SPEECH_MODE"] = "stop_token"
        elif answer in SPEECH_MODES:
            config["SPEECH_MODE"] = answer
        else:
            config["SPEECH_MODE"] = default_sm
    print()

    # --- Step 5b: Stop word (only if stop_token) ---
    if config["SPEECH_MODE"] == "stop_token":
        stop_word = getattr(args, "stop_word", None)
        if stop_word:
            config["STOP_WORD"] = stop_word
        elif non_interactive:
            config["STOP_WORD"] = existing.get("STOP_WORD", "over")
        else:
            default_sw = existing.get("STOP_WORD", "over")
            print("  Stop word for ending your turn:")
            val = _prompt("Stop word", default=default_sw)
            config["STOP_WORD"] = val
            print()
    else:
        config["STOP_WORD"] = existing.get("STOP_WORD", "over")

    # --- Step 6: Whisper Model ---
    print("Step 6/9: Whisper Model")
    whisper_model = getattr(args, "whisper_model", None)
    if whisper_model:
        config["WHISPER_MODEL"] = whisper_model
    elif non_interactive:
        config["WHISPER_MODEL"] = existing.get("WHISPER_MODEL", "base")
    else:
        print("  STT model size (larger = more accurate, slower):")
        default_wm = existing.get("WHISPER_MODEL", "base")
        for i, m in enumerate(WHISPER_MODELS, 1):
            marker = " (default)" if m == default_wm else ""
            print(f"  [{i}] {m}{marker}")
        try:
            answer = input(f"  > Choice [{default_wm}]: ").strip()
        except EOFError:
            answer = ""
        if answer.isdigit() and 1 <= int(answer) <= len(WHISPER_MODELS):
            config["WHISPER_MODEL"] = WHISPER_MODELS[int(answer) - 1]
        elif answer in WHISPER_MODELS:
            config["WHISPER_MODEL"] = answer
        else:
            config["WHISPER_MODEL"] = default_wm
    print()

    # --- Step 7: Terminal Emulator ---
    print("Step 7/9: Terminal Emulator")
    terminal = getattr(args, "terminal", None)
    if terminal:
        config["TERMINAL_EMULATOR"] = terminal
    elif non_interactive:
        config["TERMINAL_EMULATOR"] = existing.get("TERMINAL_EMULATOR", _detect_terminal())
    else:
        detected = existing.get("TERMINAL_EMULATOR", "") or _detect_terminal()
        if detected:
            print(f"  Detected: {detected}")
            print("  Override? [Enter to keep detected, or type emulator name]:")
        else:
            print("  No terminal emulator detected.")
            print("  Enter terminal emulator name (e.g., gnome-terminal):")
        try:
            answer = input("  > ").strip()
        except EOFError:
            answer = ""
        config["TERMINAL_EMULATOR"] = answer if answer else detected
    print()

    # --- Step 8: Server Host/Port ---
    print("Step 8/9: Server Host & Port")
    server_host = getattr(args, "server_host", None)
    if server_host:
        config["SERVER_HOST"] = server_host
    elif non_interactive:
        config["SERVER_HOST"] = existing.get("SERVER_HOST", "127.0.0.1")
    else:
        default_host = existing.get("SERVER_HOST", "127.0.0.1")
        val = _prompt("Server host", default=default_host)
        config["SERVER_HOST"] = val

    server_port = getattr(args, "server_port", None)
    if server_port is not None:
        config["SERVER_PORT"] = str(server_port)
    elif non_interactive:
        config["SERVER_PORT"] = existing.get("SERVER_PORT", "8765")
    else:
        default_port = existing.get("SERVER_PORT", "8765")
        val = _prompt("Server port", default=default_port)
        config["SERVER_PORT"] = val
    print()

    # --- Carry over hardware defaults ---
    config.setdefault("TTS_DEVICE", existing.get("TTS_DEVICE", "cuda"))
    config.setdefault("WHISPER_DEVICE", existing.get("WHISPER_DEVICE", "cuda"))
    config.setdefault("ROUTER_ENABLED", existing.get("ROUTER_ENABLED", "false"))

    # Carry forward any Anthropic API key from existing config
    if "ANTHROPIC_API_KEY" in existing:
        config.setdefault("ANTHROPIC_API_KEY", existing["ANTHROPIC_API_KEY"])

    return config


def write_config(config: dict[str, str]) -> Path:
    """Write config dict to ~/.config/voice-agent/config.env"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lines = ["# Voice Agent Configuration (generated by voice-agent init)"]
    for key, value in config.items():
        lines.append(f"{key}={value}")
    CONFIG_PATH.write_text("\n".join(lines) + "\n")
    return CONFIG_PATH
