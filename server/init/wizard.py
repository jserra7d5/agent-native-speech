"""Interactive setup wizard for voice-agent configuration."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "voice-agent"
CONFIG_PATH = CONFIG_DIR / "config.json"
LEGACY_ENV_PATH = CONFIG_DIR / "config.env"

VOICES = ["Ryan", "Aiden", "Vivian", "Serena", "Dylan", "Eric"]
WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3"]
TTS_BACKENDS = ["local", "elevenlabs"]
STT_BACKENDS = ["local", "elevenlabs"]
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


def _env_to_nested_dict(env_config: dict[str, str]) -> dict:
    """Convert a flat .env config dict to a nested JSON-compatible dict."""
    host = env_config.get("SERVER_HOST", "127.0.0.1")
    port = env_config.get("SERVER_PORT", "8765")

    result: dict = {
        "discord_token": env_config.get("DISCORD_TOKEN", ""),
        "anthropic_api_key": env_config.get("ANTHROPIC_API_KEY", ""),
        "elevenlabs_api_key": env_config.get("ELEVENLABS_API_KEY", ""),
        "preload_models": env_config.get("PRELOAD_MODELS", "false").lower() in ("true", "1", "yes"),
        "stt": {
            "backend": env_config.get("STT_BACKEND", "local"),
            "model": env_config.get("WHISPER_MODEL", "base"),
            "device": env_config.get("WHISPER_DEVICE", "cuda"),
            "compute_type": env_config.get("WHISPER_COMPUTE_TYPE", "float16"),
        },
        "tts": {
            "backend": env_config.get("TTS_BACKEND", "local"),
            "default_voice": env_config.get("TTS_VOICE", "Ryan"),
            "device": env_config.get("TTS_DEVICE", "cuda"),
            "voices_dir": env_config.get("TTS_VOICES_DIR", "voices"),
            "elevenlabs": {
                "model_id": env_config.get("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5"),
                "default_voice_id": env_config.get("ELEVENLABS_VOICE_ID", ""),
                "voices": {},
            },
        },
        "vad": {
            "silence_duration_ms": int(env_config.get("SILENCE_DURATION_MS", "1500")),
            "threshold": float(env_config.get("VAD_THRESHOLD", "0.5")),
        },
        "correction": {"model": env_config.get("CORRECTION_MODEL", "")},
        "speech_mode": {
            "mode": env_config.get("SPEECH_MODE", "pause"),
            "stop_word": env_config.get("STOP_WORD", "over"),
            "max_timeout_s": float(env_config.get("SPEECH_MAX_TIMEOUT_S", "60.0")),
        },
        "spawn": {
            "default_cli": env_config.get("DEFAULT_CLI", "claude"),
            "terminal_override": env_config.get("TERMINAL_EMULATOR", ""),
        },
        "llm": {
            "backend": env_config.get("ROUTER_BACKEND", ""),
            "model": env_config.get("ROUTER_MODEL", ""),
            "api_key": env_config.get("ROUTER_API_KEY", ""),
            "api_base_url": env_config.get("ROUTER_API_BASE_URL", ""),
        },
        "router": {
            "enabled": env_config.get("ROUTER_ENABLED", "false").lower() in ("true", "1", "yes"),
        },
        "server": {
            "host": host,
            "port": int(port),
            "transport": env_config.get("SERVER_TRANSPORT", "http"),
        },
    }

    # Parse voice pool
    pool_raw = env_config.get("VOICE_POOL", "")
    if pool_raw:
        result["voice_pool"] = [v.strip() for v in pool_raw.split(",") if v.strip()]

    system_voice = env_config.get("SYSTEM_VOICE", "")
    if system_voice:
        result["system_voice"] = system_voice

    channel_id = env_config.get("DISCORD_CHANNEL_ID")
    if channel_id:
        result["default_channel_id"] = int(channel_id)

    return result


def load_existing_config() -> dict:
    """Load existing config if it exists for pre-filling defaults.

    Reads JSON first, falls back to .env via _env_to_nested_dict().
    """
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    if LEGACY_ENV_PATH.exists():
        env_config: dict[str, str] = {}
        for line in LEGACY_ENV_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                env_config[key.strip()] = value.strip()
        return _env_to_nested_dict(env_config)

    return {}


def run_wizard(args) -> dict:
    """Run the interactive setup wizard, return config dict.

    If args has non-None attribute values, use those instead of prompting.
    Pre-fills from existing config on re-run.
    """
    existing = load_existing_config()
    non_interactive = getattr(args, "non_interactive", False)
    config: dict = {}

    print("\n=== Voice Agent Setup ===\n")

    # --- Step 1: Discord Bot Token ---
    print("Step 1/10: Discord Bot Token")
    token = getattr(args, "discord_token", None)
    if token:
        config["discord_token"] = token
    elif non_interactive:
        if not existing.get("discord_token"):
            print("  Error: --discord-token is required in non-interactive mode", file=sys.stderr)
            sys.exit(2)
        config["discord_token"] = existing["discord_token"]
    else:
        default_token = existing.get("discord_token", "")
        hint = " (leave blank to keep existing)" if default_token else ""
        print(f"  Enter your Discord bot token{hint}:")
        val = _prompt("Token", default=default_token)
        if not val:
            print("  Error: Discord token is required.", file=sys.stderr)
            sys.exit(1)
        config["discord_token"] = val
    print()

    # --- Step 2: TTS Backend ---
    print("Step 2/10: TTS Backend")
    tts_config = existing.get("tts", {})
    tts_backend = getattr(args, "tts_backend", None)
    if tts_backend:
        tts_config["backend"] = tts_backend
    elif non_interactive:
        tts_config.setdefault("backend", "local")
    else:
        print("  Choose your TTS backend:")
        default_backend = tts_config.get("backend", "local")
        val = _prompt("Choice", default=default_backend, choices=TTS_BACKENDS)
        tts_config["backend"] = val if val in TTS_BACKENDS else default_backend
    print()

    # If elevenlabs, prompt for API key and voice aliases
    if tts_config.get("backend") == "elevenlabs":
        el_key = getattr(args, "elevenlabs_api_key", None)
        if el_key:
            config["elevenlabs_api_key"] = el_key
        elif non_interactive:
            if not existing.get("elevenlabs_api_key"):
                print("  Error: --elevenlabs-api-key is required for elevenlabs backend", file=sys.stderr)
                sys.exit(2)
            config["elevenlabs_api_key"] = existing["elevenlabs_api_key"]
        else:
            default_el_key = existing.get("elevenlabs_api_key", "")
            print("  ElevenLabs API Key:")
            val = _prompt("API Key", default=default_el_key)
            if val:
                config["elevenlabs_api_key"] = val

        # Voice alias setup
        el_tts = tts_config.get("elevenlabs", {})
        if not non_interactive:
            print("\n  ElevenLabs Voice Setup")
            print("  Add voice aliases (name=voiceId). Enter blank to finish.")
            voices = dict(el_tts.get("voices", {}))
            while True:
                try:
                    entry = input("  > name=voiceId: ").strip()
                except EOFError:
                    break
                if not entry:
                    break
                if "=" in entry:
                    name, _, vid = entry.partition("=")
                    voices[name.strip()] = vid.strip()
            el_tts["voices"] = voices

            if not el_tts.get("default_voice_id") and voices:
                first_name = next(iter(voices))
                el_tts["default_voice_id"] = voices[first_name]
                print(f"  Default voice ID set to {first_name}: {el_tts['default_voice_id']}")
        tts_config["elevenlabs"] = el_tts
        print()

    config["tts"] = tts_config

    # --- Step 3: Default Voice ---
    print("Step 3/10: Default Voice")
    tts_voice = getattr(args, "tts_voice", None)
    if tts_voice:
        config["tts"]["default_voice"] = tts_voice
    elif non_interactive:
        config["tts"].setdefault("default_voice", "Ryan")
    else:
        print(f"  Available voices: {', '.join(VOICES)}")
        default_voice = tts_config.get("default_voice", "Ryan")
        val = _prompt("Default voice", default=default_voice)
        config["tts"]["default_voice"] = val
    print()

    # --- Step 4: STT Backend ---
    print("Step 4/10: STT Backend")
    stt_config = existing.get("stt", {})
    stt_backend = getattr(args, "stt_backend", None)
    if stt_backend:
        stt_config["backend"] = stt_backend
    elif non_interactive:
        stt_config.setdefault("backend", "local")
    else:
        print("  Choose your STT backend:")
        default_stt = stt_config.get("backend", "local")
        val = _prompt("Choice", default=default_stt, choices=STT_BACKENDS)
        stt_config["backend"] = val if val in STT_BACKENDS else default_stt

    if stt_config.get("backend") == "elevenlabs" and "elevenlabs_api_key" not in config:
        el_key = existing.get("elevenlabs_api_key", "")
        if not non_interactive and not el_key:
            print("  ElevenLabs API Key (shared with TTS):")
            el_key = _prompt("API Key", default=el_key)
        if el_key:
            config["elevenlabs_api_key"] = el_key
    print()

    # --- Step 5: Default CLI ---
    print("Step 5/10: Default CLI Client")
    spawn_config = existing.get("spawn", {})
    default_cli = getattr(args, "default_cli", None)
    if default_cli:
        spawn_config["default_cli"] = default_cli
    elif non_interactive:
        spawn_config.setdefault("default_cli", "claude")
    else:
        print("  Which CLI do you primarily use?")
        default_c = spawn_config.get("default_cli", "claude")
        val = _prompt("Choice", default=default_c, choices=CLI_CHOICES)
        spawn_config["default_cli"] = val if val in CLI_CHOICES else default_c
    config["spawn"] = spawn_config
    print()

    # --- Step 6: Speech Completion Mode ---
    print("Step 6/10: Speech Completion Mode")
    sm_config = existing.get("speech_mode", {})
    speech_mode = getattr(args, "speech_mode", None)
    if speech_mode:
        sm_config["mode"] = speech_mode
    elif non_interactive:
        sm_config.setdefault("mode", "pause")
    else:
        print("  Default speech mode:")
        default_sm = sm_config.get("mode", "pause")
        print("  [1] pause - Silence-based turn detection (current behavior)")
        print("  [2] stop_token - Say a keyword to end your turn")
        try:
            answer = input(f"  > Choice [{default_sm}]: ").strip()
        except EOFError:
            answer = ""
        if answer == "1":
            sm_config["mode"] = "pause"
        elif answer == "2":
            sm_config["mode"] = "stop_token"
        elif answer in SPEECH_MODES:
            sm_config["mode"] = answer
        else:
            sm_config.setdefault("mode", default_sm)
    print()

    # Stop word (only if stop_token)
    if sm_config.get("mode") == "stop_token":
        stop_word = getattr(args, "stop_word", None)
        if stop_word:
            sm_config["stop_word"] = stop_word
        elif non_interactive:
            sm_config.setdefault("stop_word", "over")
        else:
            default_sw = sm_config.get("stop_word", "over")
            print("  Stop word for ending your turn:")
            val = _prompt("Stop word", default=default_sw)
            sm_config["stop_word"] = val
            print()
    config["speech_mode"] = sm_config

    # --- Step 7: Whisper Model ---
    print("Step 7/10: Whisper Model")
    whisper_model = getattr(args, "whisper_model", None)
    if whisper_model:
        stt_config["model"] = whisper_model
    elif non_interactive:
        stt_config.setdefault("model", "base")
    else:
        print("  STT model size (larger = more accurate, slower):")
        default_wm = stt_config.get("model", "base")
        for i, m in enumerate(WHISPER_MODELS, 1):
            marker = " (default)" if m == default_wm else ""
            print(f"  [{i}] {m}{marker}")
        try:
            answer = input(f"  > Choice [{default_wm}]: ").strip()
        except EOFError:
            answer = ""
        if answer.isdigit() and 1 <= int(answer) <= len(WHISPER_MODELS):
            stt_config["model"] = WHISPER_MODELS[int(answer) - 1]
        elif answer in WHISPER_MODELS:
            stt_config["model"] = answer
        else:
            stt_config.setdefault("model", default_wm)
    config["stt"] = stt_config
    print()

    # --- Step 8: Terminal Emulator ---
    print("Step 8/10: Terminal Emulator")
    terminal = getattr(args, "terminal", None)
    if terminal:
        config["spawn"]["terminal_override"] = terminal
    elif non_interactive:
        config["spawn"].setdefault("terminal_override", _detect_terminal())
    else:
        detected = spawn_config.get("terminal_override", "") or _detect_terminal()
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
        config["spawn"]["terminal_override"] = answer if answer else detected
    print()

    # --- Step 9: Server Host/Port ---
    print("Step 9/10: Server Host & Port")
    server_config = existing.get("server", {})
    server_host = getattr(args, "server_host", None)
    if server_host:
        server_config["host"] = server_host
    elif non_interactive:
        server_config.setdefault("host", "127.0.0.1")
    else:
        default_host = server_config.get("host", "127.0.0.1")
        val = _prompt("Server host", default=default_host)
        server_config["host"] = val

    server_port = getattr(args, "server_port", None)
    if server_port is not None:
        server_config["port"] = int(server_port)
    elif non_interactive:
        server_config.setdefault("port", 8765)
    else:
        default_port = str(server_config.get("port", 8765))
        val = _prompt("Server port", default=default_port)
        server_config["port"] = int(val)
    config["server"] = server_config
    print()

    # --- Step 10: Summary ---
    print("Step 10/10: Review")

    # Carry over hardware defaults from existing config
    config["stt"].setdefault("device", existing.get("stt", {}).get("device", "cuda"))
    config["tts"].setdefault("device", existing.get("tts", {}).get("device", "cuda"))
    config.setdefault("router", existing.get("router", {"enabled": False}))

    # Carry forward Anthropic API key from existing config
    if existing.get("anthropic_api_key"):
        config.setdefault("anthropic_api_key", existing["anthropic_api_key"])

    # Carry forward LLM config
    if existing.get("llm"):
        config.setdefault("llm", existing["llm"])

    return config


def write_config(config: dict) -> Path:
    """Write config dict to ~/.config/voice-agent/config.json"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return CONFIG_PATH
