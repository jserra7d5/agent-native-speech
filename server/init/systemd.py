"""Systemd user service creation for voice-agent daemon."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

UNIT_TEMPLATE = """\
[Unit]
Description=Voice Agent MCP Server (Discord)
After=network.target

[Service]
Type=simple
WorkingDirectory={project_dir}
ExecStart={venv_python} -m server.main --transport http --config {config_path}
Restart=always
RestartSec=10
StartLimitIntervalSec=300
StartLimitBurst=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""

SERVICE_NAME = "voice-agent.service"


def is_systemd_available() -> bool:
    """Check if systemd is available on this system."""
    return os.path.exists("/run/systemd/system")


def get_unit_path() -> Path:
    """Return the path where the user service unit file will be installed."""
    return Path.home() / ".config" / "systemd" / "user" / SERVICE_NAME


def install_service(project_dir: str, config_path: str) -> Path:
    """Write the systemd unit file."""
    venv_python = sys.executable
    unit_content = UNIT_TEMPLATE.format(
        project_dir=project_dir,
        venv_python=venv_python,
        config_path=config_path,
    )
    unit_path = get_unit_path()
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(unit_content)
    return unit_path


def enable_and_start() -> bool:
    """Run systemctl --user daemon-reload, enable, and start."""
    try:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", SERVICE_NAME], check=True)
        subprocess.run(["systemctl", "--user", "start", SERVICE_NAME], check=True)
        return True
    except subprocess.CalledProcessError:
        return False


def check_status() -> str:
    """Check if the service is running."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", SERVICE_NAME],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"
