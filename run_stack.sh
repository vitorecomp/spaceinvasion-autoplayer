#!/usr/bin/env bash
# Auto-restarting supervisor wrapper for `run_stack.py`.
#
# Behavior:
# - Runs `.venv/bin/python3 run_stack.py` (defaults to `--live` when called with no args;
#   pass `--dry-run` for dry-run mode, or pass any `run_stack.py` flags directly).
# - Automatically restarts the Python stack with exponential backoff (5s -> 60s max)
#   whenever `run_stack.py` exits with a crash/error code.
# - Resets the backoff counter once the stack stays healthy for >= 60s.
# - NEVER auto-restarts on exit code 2 (CAPTCHA / Cloudflare Turnstile / anti-bot gate)
#   or exit code 0 / Ctrl+C / SIGTERM, preserving Chrome on port 9222.

set -u

REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR" || exit 1

if [[ -n "${PYTHON_BIN:-}" ]]; then
  PY_BIN="$PYTHON_BIN"
elif [[ -x "$REPO_DIR/.venv/bin/python3" ]]; then
  PY_BIN="$REPO_DIR/.venv/bin/python3"
else
  PY_BIN="$(command -v python3 || true)"
fi

if [[ -z "$PY_BIN" ]]; then
  echo "[WATCHDOG] Error: python3 not found." >&2
  exit 1
fi

STACK_SCRIPT="${STACK_SCRIPT:-$REPO_DIR/run_stack.py}"
WATCHDOG_LOG="${WATCHDOG_LOG:-$REPO_DIR/logs/watchdog.log}"
WATCHDOG_PID_FILE="${WATCHDOG_PID_FILE:-$REPO_DIR/state/run_stack_watchdog.pid}"
INITIAL_BACKOFF_SEC="${INITIAL_BACKOFF_SEC:-5}"
MAX_BACKOFF_SEC="${MAX_BACKOFF_SEC:-60}"
HEALTHY_UPTIME_RESET_SEC="${HEALTHY_UPTIME_RESET_SEC:-60}"
MAX_RAPID_RESTARTS="${MAX_RAPID_RESTARTS:-0}"

print_usage() {
  cat <<'EOF'
Usage: ./run_stack.sh [--dry-run | run_stack.py options...]

Supervises `run_stack.py` and automatically restarts it if Python crashes or exits unexpectedly.

Defaults & Options:
  (no arguments)   Starts `run_stack.py --live`
  --dry-run        Starts `run_stack.py` in dry-run mode (without `--live`)
  -h, --help       Show this help message and `run_stack.py --help`

Environment Overrides:
  INITIAL_BACKOFF_SEC       Initial restart delay in seconds (default: 5)
  MAX_BACKOFF_SEC           Maximum restart backoff in seconds (default: 60)
  HEALTHY_UPTIME_RESET_SEC  Uptime seconds that reset consecutive crash count (default: 60)
  MAX_RAPID_RESTARTS        Stop after N consecutive rapid crashes; 0 = unlimited (default: 0)
  WATCHDOG_LOG              Log file path (default: logs/watchdog.log)
  WATCHDOG_PID_FILE         Lockfile path (default: state/run_stack_watchdog.pid)
EOF
}

STACK_ARGS=()
API_PORT=8787
if [[ $# -eq 0 ]]; then
  STACK_ARGS=("--live")
else
  prev_was_port=0
  for arg in "$@"; do
    if [[ $prev_was_port -eq 1 ]]; then
      API_PORT="$arg"
      prev_was_port=0
    fi
    case "$arg" in
      -h|--help)
        print_usage
        echo ""
        "$PY_BIN" "$STACK_SCRIPT" --help
        exit 0
        ;;
      --dry-run)
        # Strip wrapper-only `--dry-run` flag so `run_stack.py` runs without `--live`.
        ;;
      --api-port)
        prev_was_port=1
        STACK_ARGS+=("$arg")
        ;;
      --api-port=*)
        API_PORT="${arg#--api-port=}"
        STACK_ARGS+=("$arg")
        ;;
      *)
        STACK_ARGS+=("$arg")
        ;;
    esac
  done
fi

mkdir -p "$(dirname "$WATCHDOG_LOG")" "$(dirname "$WATCHDOG_PID_FILE")"

log_msg() {
  local ts
  ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  local line="[$ts] [WATCHDOG] $*"
  echo "$line"
  echo "$line" >>"$WATCHDOG_LOG" 2>/dev/null || true
}

# Prevent duplicate watchdog supervisors from racing each other.
if [[ -f "$WATCHDOG_PID_FILE" ]]; then
  existing_pid="$(cat "$WATCHDOG_PID_FILE" 2>/dev/null || true)"
  if [[ "$existing_pid" =~ ^[0-9]+$ ]] && [[ "$existing_pid" != "$$" ]] && kill -0 "$existing_pid" 2>/dev/null; then
    existing_cmd="$(tr '\0' ' ' <"/proc/$existing_pid/cmdline" 2>/dev/null || true)"
    if [[ "$existing_cmd" == *"run_stack.sh"* ]]; then
      echo "[WATCHDOG] Another run_stack.sh instance is already active (PID $existing_pid)." >&2
      exit 1
    fi
  fi
fi
echo "$$" >"$WATCHDOG_PID_FILE"

STOP_REQUESTED=0
CHILD_PID=""
SLEEP_PID=""

cleanup_pid_file() {
  if [[ -f "$WATCHDOG_PID_FILE" ]]; then
    local owner_pid
    owner_pid="$(cat "$WATCHDOG_PID_FILE" 2>/dev/null || true)"
    if [[ "$owner_pid" == "$$" ]]; then
      rm -f "$WATCHDOG_PID_FILE"
    fi
  fi
}

terminate_child_tree() {
  local pid="$1"
  if [[ -z "$pid" ]]; then
    return
  fi
  if kill -0 "$pid" 2>/dev/null; then
    kill -TERM "$pid" 2>/dev/null || true
    local waited=0
    while kill -0 "$pid" 2>/dev/null && [[ $waited -lt 80 ]]; do
      sleep 0.1
      waited=$((waited + 1))
    done
    if kill -0 "$pid" 2>/dev/null; then
      kill -KILL "$pid" 2>/dev/null || true
    fi
  fi
}

cleanup_orphaned_stack_procs() {
  # Only clean up orphaned repo game_api/commander processes when supervising the real run_stack.py
  # (never in isolated unit tests and never touching Chrome on port 9222).
  if [[ "$STACK_SCRIPT" != "$REPO_DIR/run_stack.py" ]]; then
    return
  fi
  pkill -TERM -f "$REPO_DIR/commander.py --api-url http://127.0.0.1:${API_PORT}" 2>/dev/null || true
  pkill -TERM -f "$REPO_DIR/game_api.py --port ${API_PORT}" 2>/dev/null || true
}

on_stop_signal() {
  STOP_REQUESTED=1
  log_msg "Stop signal received; shutting down Python stack cleanly (Chrome stays open)..."
  if [[ -n "$SLEEP_PID" ]] && kill -0 "$SLEEP_PID" 2>/dev/null; then
    kill -TERM "$SLEEP_PID" 2>/dev/null || true
  fi
  terminate_child_tree "$CHILD_PID"
  cleanup_orphaned_stack_procs
  cleanup_pid_file
  exit 0
}

trap on_stop_signal INT TERM
trap cleanup_pid_file EXIT

consecutive_crashes=0
log_msg "Supervisor started (pid=$$, python=$PY_BIN, script=$STACK_SCRIPT, args=${STACK_ARGS[*]:-(dry-run)})"

while [[ $STOP_REQUESTED -eq 0 ]]; do
  start_epoch="$(date +%s)"
  log_msg "Launching stack: $PY_BIN $STACK_SCRIPT ${STACK_ARGS[*]:-}"

  "$PY_BIN" "$STACK_SCRIPT" "${STACK_ARGS[@]}" &
  CHILD_PID=$!
  wait "$CHILD_PID"
  rc=$?
  CHILD_PID=""

  if [[ $STOP_REQUESTED -ne 0 ]]; then
    break
  fi

  end_epoch="$(date +%s)"
  uptime_sec=$((end_epoch - start_epoch))

  # Exit 0 = clean exit; do not restart.
  if [[ $rc -eq 0 ]]; then
    log_msg "Stack exited cleanly (code 0 after ${uptime_sec}s). Stopping watchdog."
    exit 0
  fi

  # Exit 2 = CAPTCHA / Turnstile / anti-bot challenge; NEVER auto-restart into an anti-bot gate.
  if [[ $rc -eq 2 ]]; then
    log_msg "Anti-bot / CAPTCHA challenge detected (exit code 2). Not restarting automatically — complete verification in Chrome (port 9222), then re-run ./run_stack.sh."
    exit 2
  fi

  # Reset rapid-crash counter if the stack ran long enough to be considered healthy.
  if [[ $uptime_sec -ge $HEALTHY_UPTIME_RESET_SEC ]]; then
    consecutive_crashes=1
  else
    consecutive_crashes=$((consecutive_crashes + 1))
  fi

  if [[ "$MAX_RAPID_RESTARTS" -gt 0 ]] && [[ $consecutive_crashes -gt "$MAX_RAPID_RESTARTS" ]]; then
    log_msg "Exceeded MAX_RAPID_RESTARTS ($MAX_RAPID_RESTARTS) after crash exit code $rc (uptime ${uptime_sec}s). Aborting."
    exit "$rc"
  fi

  # Compute exponential backoff: INITIAL_BACKOFF_SEC * 2^(consecutive_crashes - 1), capped at MAX_BACKOFF_SEC.
  backoff_sec="$("$PY_BIN" -c '
import sys
init_s = max(0.05, float(sys.argv[1]))
max_s = max(init_s, float(sys.argv[2]))
n = max(1, int(sys.argv[3]))
delay = min(max_s, init_s * (2 ** min(n - 1, 10)))
print(f"{delay:g}")
' "$INITIAL_BACKOFF_SEC" "$MAX_BACKOFF_SEC" "$consecutive_crashes")"

  log_msg "Stack crashed with exit code $rc after ${uptime_sec}s (crash #$consecutive_crashes). Restarting in ${backoff_sec}s..."
  cleanup_orphaned_stack_procs

  "$PY_BIN" -c 'import sys, time; time.sleep(float(sys.argv[1]))' "$backoff_sec" &
  SLEEP_PID=$!
  wait "$SLEEP_PID" 2>/dev/null || true
  SLEEP_PID=""
done

exit 0
