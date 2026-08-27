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

# SEC-007: redirect log to ~/.claude/logs/ (user-private) instead of world-readable
# /tmp/session_stop_hook.log to prevent other users from reading session metadata.
# SEC-203: umask 077 from here on so the log dir/file and anything the detached
# child creates land owner-only (0644/0755 at default umask would expose
# cwd/transcript paths until the first Python hook repairs them).
umask 077
LOG_DIR="$HOME/.claude/logs"
mkdir -p "$LOG_DIR"
chmod 700 "$LOG_DIR"  # SEC-203: also repairs a pre-existing dir from before umask 077
LOG_FILE="$LOG_DIR/session_stop_hook.log"

# SEC-003: restrict temp file permissions to owner-only (mode 0600) by setting
# umask 077 before mktemp so no other user on the system can read cwd/transcript
# paths written to the file.
# SEC-003: prefer $TMPDIR (user-specific on macOS) over the world-accessible /tmp.
old_umask=$(umask)
umask 077
TMPFILE=$(mktemp "${TMPDIR:-/tmp}/session_stop_hook_XXXXXX.json") || TMPFILE=""
umask "$old_umask"
# An unchecked mktemp failure would silently drop the session's stdin JSON.
# Still acknowledge Claude Code with {} (never break the host session), but
# leave a diagnostic line in the log before exiting.
if [ -z "$TMPFILE" ]; then
  printf '{}'
  echo "$(date '+%Y-%m-%d %H:%M:%S') session_stop_wrapper: mktemp failed; SessionEnd payload dropped" >> "$LOG_FILE" 2>/dev/null
  exit 0
fi
# QA-015: Do NOT trap EXIT here — a trap 'rm -f "$TMPFILE"' EXIT would fire
# when the foreground wrapper exits, which races with the background subshell
# that reads the file.  The background subshell does its own 'rm -f "$TMPFILE"'
# after the real hook completes, so cleanup is handled there.
cat > "$TMPFILE"

# Acknowledge to Claude Code immediately
printf '{}'

# Run the real hook detached — immune to SIGHUP and process-group exit
# stdout/stderr go to a log file for debugging; temp file is cleaned up after.
(
  unset CLAUDECODE
  nohup uv run --no-project "$REAL_HOOK" < "$TMPFILE" \
    > /dev/null 2>> "$LOG_FILE"
  rm -f "$TMPFILE"
) &
