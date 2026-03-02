"""Register voice-agent as an MCP server in CLI tool configurations."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


def detect_clis() -> list[str]:
    """Detect installed CLI tools (claude, codex)."""
    found = []
    if shutil.which("claude"):
        found.append("claude")
    if shutil.which("codex"):
        found.append("codex")
    return found


def register_claude(server_url: str = "http://127.0.0.1:8765/mcp") -> bool:
    """Register in Claude Code.

    Try `claude mcp add` first. Fall back to writing ~/.claude.json directly.
    Check for existing entries to avoid duplicates.
    """
    # Try the CLI approach first
    try:
        result = subprocess.run(
            [
                "claude", "mcp", "add",
                "--transport", "http",
                "--scope", "user",
                "voice-agent",
                server_url,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            return True
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    # Fallback: write to ~/.claude.json directly
    claude_config = Path.home() / ".claude.json"
    try:
        if claude_config.exists():
            data = json.loads(claude_config.read_text())
        else:
            data = {}

        mcp_servers = data.setdefault("mcpServers", {})

        # Check for existing entry
        if "voice-agent" in mcp_servers:
            # Update the URL in case it changed
            mcp_servers["voice-agent"]["url"] = server_url
        else:
            mcp_servers["voice-agent"] = {
                "type": "http",
                "url": server_url,
            }

        claude_config.write_text(json.dumps(data, indent=2) + "\n")
        return True
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  Warning: Could not write to {claude_config}: {exc}", file=sys.stderr)
        return False


def register_codex(server_url: str = "http://127.0.0.1:8765/mcp") -> bool:
    """Register in Codex CLI.

    Append [mcp_servers.voice-agent] section to ~/.codex/config.toml.
    Check for existing entries to avoid duplicates.
    """
    codex_config = Path.home() / ".codex" / "config.toml"
    section_header = "[mcp_servers.voice-agent]"
    new_section = f'{section_header}\ntype = "http"\nurl = "{server_url}"\n'

    try:
        codex_config.parent.mkdir(parents=True, exist_ok=True)

        if codex_config.exists():
            content = codex_config.read_text()
            if section_header in content:
                # Already registered — replace the existing section
                lines = content.splitlines(keepends=True)
                new_lines: list[str] = []
                skip = False
                for line in lines:
                    if line.strip() == section_header:
                        skip = True
                        new_lines.append(new_section + "\n")
                        continue
                    if skip:
                        # Stop skipping when we hit a new section or blank line after content
                        if line.startswith("[") and line.strip() != section_header:
                            skip = False
                            new_lines.append(line)
                        # Skip lines belonging to the old section
                        continue
                    new_lines.append(line)
                codex_config.write_text("".join(new_lines))
                return True
            else:
                # Append the new section
                if not content.endswith("\n"):
                    content += "\n"
                content += "\n" + new_section
                codex_config.write_text(content)
                return True
        else:
            codex_config.write_text(new_section)
            return True
    except OSError as exc:
        print(f"  Warning: Could not write to {codex_config}: {exc}", file=sys.stderr)
        return False


def _prompt_bool(question: str, default: bool = True) -> bool:
    """Yes/no prompt for MCP registration."""
    hint = "Y/n" if default else "y/N"
    try:
        answer = input(f"  {question} [{hint}]: ").strip().lower()
    except EOFError:
        answer = ""
    if not answer:
        return default
    return answer in ("y", "yes")


def register_all(server_url: str, interactive: bool = True) -> dict[str, bool]:
    """Detect CLIs and register in each (with optional confirmation)."""
    results: dict[str, bool] = {}
    clis = detect_clis()

    if not clis:
        if interactive:
            print("  No supported CLI tools detected (claude, codex).")
        return results

    for cli in clis:
        if cli == "claude":
            label = "Claude Code"
            register_fn = register_claude
        elif cli == "codex":
            label = "Codex CLI"
            register_fn = register_codex
        else:
            continue

        if interactive:
            print(f"  Found: {cli} ({label})")
            if not _prompt_bool(f"Register voice-agent as MCP server in {label}?"):
                results[cli] = False
                continue

        success = register_fn(server_url)
        results[cli] = success

    return results
