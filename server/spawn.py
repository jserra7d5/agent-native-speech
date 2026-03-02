"""Spawn coding agent CLI instances in terminal emulators or headless mode.

Detects available terminal emulators and launches Claude Code or Codex CLI
sessions that connect back to the voice agent server via MCP HTTP transport.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from typing import Any

from server.config import Config

log = logging.getLogger(__name__)

# Per-emulator command-line flag for executing a command.
# Value is a list of flag tokens inserted before the command, or an empty list
# when the terminal takes the command directly as trailing arguments.
_TERMINAL_EXEC_FLAGS: dict[str, list[str]] = {
    "gnome-terminal": ["--"],
    "konsole": ["-e"],
    "kitty": [],
    "alacritty": ["-e"],
    "wezterm": ["start", "--"],
    "foot": [],
    "xterm": ["-e"],
}

# Ordered preference for terminal detection via PATH scan.
_TERMINAL_SCAN_ORDER = [
    "ghostty",
    "kitty",
    "alacritty",
    "wezterm",
    "gnome-terminal",
    "konsole",
    "xfce4-terminal",
    "foot",
    "xterm",
]

# The prompt injected into the spawned agent to make it call back.
_CALLBACK_PROMPT = (
    "You have been launched as a voice-enabled coding agent. "
    "Use the initiate_call tool immediately to call the user and "
    "introduce yourself. Tell them which project directory you're "
    "working in and ask what they'd like you to work on."
)


class TerminalDetector:
    """Detect an available terminal emulator on the host system.

    Detection chain (first match wins):
      1. Config override (``SpawnConfig.terminal_override``)
      2. ``$TERMINAL`` environment variable
      3. PATH scan for known emulators (curated order)

    Note: ``x-terminal-emulator`` (Debian/Ubuntu alternatives) is
    intentionally skipped — it can resolve to terminals that don't
    render modern TUIs (like Claude Code) correctly.
    """

    def __init__(self, override: str = "") -> None:
        self._override = override

    def detect(self) -> str | None:
        """Return the name of an available terminal emulator, or None."""
        # 1. Config override
        if self._override:
            if shutil.which(self._override):
                return self._override
            log.warning(
                "Configured terminal %r not found in PATH", self._override
            )

        # 2. $TERMINAL env var
        env_terminal = os.environ.get("TERMINAL", "")
        if env_terminal and shutil.which(env_terminal):
            return env_terminal

        # 3. PATH scan
        for term in _TERMINAL_SCAN_ORDER:
            if shutil.which(term):
                return term

        return None

    @staticmethod
    def get_exec_flags(terminal: str) -> list[str]:
        """Return the command-line flags for launching a command in *terminal*.

        Falls back to ``-e`` (the most common convention) for unknown terminals.
        """
        # Normalise: if the binary is a full path, use the basename
        base = os.path.basename(terminal)
        return list(_TERMINAL_EXEC_FLAGS.get(base, ["-e"]))


class SpawnManager:
    """Launch coding agent CLI sessions with voice callback.

    Validates inputs, builds the CLI launch command, spawns the process
    in either an interactive terminal or headless mode, and returns
    metadata for session registration.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._detector = TerminalDetector(config.spawn.terminal_override)
        self._server_url = config.spawn.server_url
        # Write MCP config to a temp file so --mcp-config (which is variadic)
        # doesn't consume subsequent positional arguments like the prompt.
        self._mcp_config_path = self._write_mcp_config_file()

    @property
    def default_cli(self) -> str:
        """Return the configured default CLI name."""
        return self._config.spawn.default_cli

    def spawn_session(
        self,
        directory: str,
        cli: str | None = None,
        voice: str | None = None,
        headless: bool = False,
        user_id: str = "",
        resume_session_id: str | None = None,
    ) -> dict[str, Any]:
        """Spawn a coding agent CLI session.

        Args:
            directory: Absolute path to the project working directory.
            cli: CLI tool to launch ("claude" or "codex"). Defaults to config.
            voice: Optional TTS voice name for session registration.
            headless: If True, run without a terminal window.
            user_id: Discord user ID of the spawner.
            resume_session_id: If set, resume this existing session instead
                of starting a new one.

        Returns:
            dict with keys: process_pid, terminal_pid, directory, cli,
            voice, headless, session_name.

        Raises:
            ValueError: If directory doesn't exist or CLI is not installed.
            RuntimeError: If no terminal emulator is available for
                interactive mode.
        """
        # Resolve defaults
        cli = cli or self._config.spawn.default_cli

        # Validate directory
        if not os.path.isdir(directory):
            raise ValueError(f"Directory not found: {directory}")

        # Validate CLI binary
        if not shutil.which(cli):
            raise ValueError(f"CLI not found: {cli} is not installed")

        # Build the CLI command
        cli_command = self._build_cli_command(cli, directory, resume_session_id, headless)

        # Clean environment: strip Claude Code nesting-detection vars so
        # the spawned agent doesn't refuse to start.
        clean_env = {
            k: v for k, v in os.environ.items()
            if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")
        }

        process_pid: int | None = None
        terminal_pid: int | None = None

        if headless:
            proc = subprocess.Popen(
                cli_command,
                cwd=directory,
                env=clean_env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            process_pid = proc.pid
            log.info(
                "Spawned headless %s session (pid=%d) in %s",
                cli, process_pid, directory,
            )
        else:
            terminal = self._detector.detect()
            if not terminal:
                raise RuntimeError(
                    "No terminal emulator available. "
                    "Use headless mode or configure TERMINAL_EMULATOR."
                )

            terminal_command = self._build_terminal_command(
                terminal, cli_command
            )
            proc = subprocess.Popen(
                terminal_command,
                cwd=directory,
                env=clean_env,
                start_new_session=True,
            )
            terminal_pid = proc.pid
            log.info(
                "Spawned %s in %s terminal (pid=%d) in %s",
                cli, terminal, terminal_pid, directory,
            )

        session_name = os.path.basename(directory)
        return {
            "process_pid": process_pid,
            "terminal_pid": terminal_pid,
            "directory": directory,
            "cli": cli,
            "voice": voice,
            "headless": headless,
            "session_name": session_name,
            "user_id": user_id,
        }

    def kill_session(
        self,
        process_pid: int | None = None,
        terminal_pid: int | None = None,
    ) -> bool:
        """Terminate a spawned session by sending SIGTERM.

        Args:
            process_pid: PID of the headless process.
            terminal_pid: PID of the terminal emulator.

        Returns:
            True if at least one process was signalled successfully.
        """
        import signal

        killed = False
        for pid in (process_pid, terminal_pid):
            if pid is None:
                continue
            try:
                os.kill(pid, signal.SIGTERM)
                killed = True
                log.info("Sent SIGTERM to pid %d", pid)
            except ProcessLookupError:
                log.debug("Process %d already exited", pid)
            except PermissionError:
                log.warning("Permission denied killing pid %d", pid)
        return killed

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_cli_command(
        self,
        cli: str,
        directory: str,
        resume_session_id: str | None = None,
        headless: bool = False,
    ) -> list[str]:
        """Build the shell command list for the coding agent CLI.

        Args:
            cli: CLI tool name ("claude" or "codex").
            directory: Working directory for the agent.
            resume_session_id: Optional session ID to resume.
            headless: If True, use non-interactive flags (``--print``
                for Claude).  Interactive terminal sessions pass the
                prompt as a positional argument instead.
        """
        if cli == "claude":
            # --mcp-config is variadic (<configs...>) and consumes all
            # subsequent non-flag arguments, so it MUST come last.
            # Prompt and other flags go before it.
            cmd = ["claude"]
            if resume_session_id:
                cmd.extend(["--resume", resume_session_id])
            if headless:
                cmd.extend(["--print", _CALLBACK_PROMPT])
            else:
                cmd.append(_CALLBACK_PROMPT)
            cmd.extend(["--mcp-config", self._mcp_config_path])
            return cmd
        elif cli == "codex":
            if resume_session_id:
                return [
                    "codex", "resume", resume_session_id,
                    "--mcp-config", f"voice-agent={self._server_url}",
                    _CALLBACK_PROMPT,
                ]
            return [
                "codex",
                "--mcp-config", f"voice-agent={self._server_url}",
                _CALLBACK_PROMPT,
            ]
        else:
            raise ValueError(f"Unsupported CLI: {cli!r}")

    def _write_mcp_config_file(self) -> str:
        """Write MCP server config to a temp file and return its path.

        Claude Code's ``--mcp-config`` flag is variadic, so passing inline
        JSON would cause it to consume subsequent positional arguments (like
        the prompt).  Writing to a file avoids this.
        """
        config_data = {
            "mcpServers": {
                "voice-agent": {
                    "url": self._server_url,
                }
            }
        }
        fd, path = tempfile.mkstemp(prefix="voice-agent-mcp-", suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(config_data, f)
        log.info("Wrote MCP config to %s", path)
        return path

    def _build_terminal_command(
        self,
        terminal: str,
        cli_command: list[str],
    ) -> list[str]:
        """Wrap a CLI command for execution inside a terminal emulator."""
        exec_flags = TerminalDetector.get_exec_flags(terminal)
        return [terminal] + exec_flags + cli_command
