#!/usr/bin/env bash
# PostToolUse hook for Claude Code — checks for pending voice messages.
#
# The voice agent server writes a marker file when a user sends a voice
# message to this agent session.  This hook checks for that marker and
# injects additionalContext telling the agent to call check_messages.
#
# Zero overhead when no messages: just a single file-existence test.
#
# Expected environment:
#   VOICE_AGENT_SESSION_ID — set by the spawn command when launching the CLI
#
# Marker path: /tmp/voice-agent-queue-<session_id>

set -euo pipefail

SESSION_ID="${VOICE_AGENT_SESSION_ID:-}"

# No session ID → nothing to check
if [ -z "$SESSION_ID" ]; then
  echo '{}'
  exit 0
fi

MARKER="/tmp/voice-agent-queue-${SESSION_ID}"

if [ -f "$MARKER" ]; then
  # Read message count from marker (single integer, default 1)
  COUNT=$(cat "$MARKER" 2>/dev/null || echo "1")
  cat <<EOF
{
  "additionalContext": "You have ${COUNT} pending voice message(s) from the user. Call the check_messages tool NOW to retrieve them before continuing your work."
}
EOF
else
  echo '{}'
fi
