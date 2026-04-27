#!/usr/bin/env bash
# SessionEnd hook wrapper — reads stdin, acknowledges immediately, then runs
# the real session_stop_hook.py detached so Claude Code's exit sequence
# cannot cancel it before it completes.
#
# Claude Code fires SessionEnd and waits for the hook to output JSON and exit.
# If the hook is slow to start (e.g. uv startup overhead) Claude Code may
# cancel it during its own shutdown.  This wrapper solves that by:
#   1. Saving stdin to a temp file before outputting anything.
#   2. Writing {} to stdout immediately so Claude Code sees a clean exit.
#   3. Spawning the real Python hook in a detached background process.

SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"
REAL_HOOK="$SCRIPTS_DIR/session_stop_hook.py"

# Parsidion-launched CLI agents set PARSIDION_INTERNAL=1. Acknowledge and skip
# immediately so internal sessions do not enqueue more summarization work or
# spawn detached hook processes.
if [ -n "${PARSIDION_INTERNAL:-}" ]; then
  printf '{}'
  exit 0
fi

# SEC-003: restrict temp file permissions to owner-only (mode 0600) by setting
# umask 077 before mktemp so no other user on the system can read cwd/transcript
# paths written to the file.
# SEC-003: prefer $TMPDIR (user-specific on macOS) over the world-accessible /tmp.
old_umask=$(umask)
umask 077
TMPFILE=$(mktemp "${TMPDIR:-/tmp}/session_stop_hook_XXXXXX.json")
umask "$old_umask"
# ARC-014: remove the temp file if the wrapper exits before the background
# subshell has a chance to run (e.g. REAL_HOOK missing, unexpected signal).
# The background subshell removes TMPFILE after the real hook completes.
trap 'rm -f "$TMPFILE"' EXIT
cat > "$TMPFILE"

# Acknowledge to Claude Code immediately
printf '{}'

# SEC-007: redirect log to ~/.claude/logs/ (user-private) instead of world-readable
# /tmp/session_stop_hook.log to prevent other users from reading session metadata.
LOG_DIR="$HOME/.claude/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/session_stop_hook.log"

# Run the real hook detached — immune to SIGHUP and process-group exit
# stdout/stderr go to a log file for debugging; temp file is cleaned up after.
(
  unset CLAUDECODE
  nohup uv run --no-project "$REAL_HOOK" < "$TMPFILE" \
    > /dev/null 2>> "$LOG_FILE"
  rm -f "$TMPFILE"
) &
