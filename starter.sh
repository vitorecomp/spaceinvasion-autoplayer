#!/usr/bin/env bash
# Auto-restarting supervisor wrapper (`starter.sh`) for `bin/run_stack.py`.
#
# Behavior:
# - Runs `.venv/bin/python3 bin/run_stack.py` (defaults to `--live` when called with no args;
#   pass `--dry-run` for dry-run mode, or pass any `run_stack.py` flags directly).
# - Automatically restarts the Python stack with exponential backoff (5s -> 60s max)
#   whenever `run_stack.py` exits with a crash/error code.
# - Resets the backoff counter once the stack stays healthy for >= 60s.
# - NEVER auto-restarts on exit code 2 (CAPTCHA / Cloudflare Turnstile / anti-bot gate)
#   or exit code 0 / Ctrl+C / SIGTERM, preserving Chrome on port 9222.

set -u

REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$REPO_DIR/bin"
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

STACK_SCRIPT="${STACK_SCRIPT:-$BIN_DIR/run_stack.py}"
WATCHDOG_LOG="${WATCHDOG_LOG:-}"
WATCHDOG_PID_FILE="${WATCHDOG_PID_FILE:-$REPO_DIR/state/run_stack_watchdog.pid}"
export SPACEINVASION_DB_BACKEND="${SPACEINVASION_DB_BACKEND:-postgres}"
export SPACEINVASION_DISK_LOGS="${SPACEINVASION_DISK_LOGS:-0}"
INITIAL_BACKOFF_SEC="${INITIAL_BACKOFF_SEC:-5}"
MAX_BACKOFF_SEC="${MAX_BACKOFF_SEC:-60}"
HEALTHY_UPTIME_RESET_SEC="${HEALTHY_UPTIME_RESET_SEC:-60}"
MAX_RAPID_RESTARTS="${MAX_RAPID_RESTARTS:-0}"

print_usage() {
  cat <<'EOF'
Usage: ./starter.sh [--dry-run | run_stack.py options...]

Supervises `bin/run_stack.py` and automatically restarts it if Python crashes or exits unexpectedly.

Defaults & Options:
  (no arguments)            Starts `bin/run_stack.py --live` using PostgreSQL (`SPACEINVASION_DB_URL` or Podman :5433)
  --dry-run                 Starts `bin/run_stack.py` in dry-run mode (without `--live`)
  --check-ui-build          Rebuild frontend bundle if missing or stale, then exit 0
  -h, --help                Show this help message and `bin/run_stack.py --help`

Note:
  - Both host runs (`./starter.sh`) and container runs (`podman-compose up -d`) use PostgreSQL exclusively.

Environment Overrides:
  SPACEINVASION_DB_BACKEND  Database engine (`postgres` only)
  SPACEINVASION_DB_URL      Explicit PostgreSQL DSN override
  SPACEINVASION_PG_PORT     Host port bound by Podman PostgreSQL container (default: 5433)
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
CHECK_UI_BUILD_ONLY=0
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
      --check-ui-build)
        CHECK_UI_BUILD_ONLY=1
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

mkdir -p "$(dirname "$WATCHDOG_PID_FILE")"
if [[ -n "$WATCHDOG_LOG" ]]; then
  mkdir -p "$(dirname "$WATCHDOG_LOG")"
fi

log_msg() {
  local ts
  ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  local line="[$ts] [WATCHDOG] $*"
  echo "$line"
  if [[ -n "$WATCHDOG_LOG" ]]; then
    echo "$line" >>"$WATCHDOG_LOG" 2>/dev/null || true
  fi
}

# Prevent duplicate watchdog supervisors from racing each other.
if [[ -f "$WATCHDOG_PID_FILE" ]]; then
  existing_pid="$(cat "$WATCHDOG_PID_FILE" 2>/dev/null || true)"
  if [[ "$existing_pid" =~ ^[0-9]+$ ]] && [[ "$existing_pid" != "$$" ]] && kill -0 "$existing_pid" 2>/dev/null; then
    existing_cmd="$(tr '\0' ' ' <"/proc/$existing_pid/cmdline" 2>/dev/null || true)"
    if [[ "$existing_cmd" == *"starter.sh"* ]]; then
      echo "[WATCHDOG] Another starter.sh instance is already active (PID $existing_pid)." >&2
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
  # Only clean up orphaned repo game_api/commander processes when supervising the real bin/run_stack.py
  # (never in isolated unit tests and never touching Chrome on port 9222).
  if [[ "$STACK_SCRIPT" != "$BIN_DIR/run_stack.py" ]]; then
    return
  fi
  pkill -TERM -f "$BIN_DIR/commander.py --api-url http://127.0.0.1:${API_PORT}" 2>/dev/null || true
  pkill -TERM -f "$BIN_DIR/game_api.py --port ${API_PORT}" 2>/dev/null || true
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

frontend_bundle_needs_build() {
  local frontend_dir="${SPACEINVASION_FRONTEND_DIR:-$REPO_DIR/frontend}"
  local ui_dir="${SPACEINVASION_UI_DIR:-$REPO_DIR/spaceinvasion/ui}"
  local bundle="$ui_dir/app.js"
  if [[ ! -f "$frontend_dir/package.json" ]]; then
    return 1
  fi
  if [[ ! -f "$bundle" ]]; then
    return 0
  fi
  local watch_file
  for watch_file in \
    "$frontend_dir/index.html" \
    "$frontend_dir/package.json" \
    "$frontend_dir/tsconfig.json" \
    "$frontend_dir/vite.config.ts"; do
    if [[ -f "$watch_file" ]] && [[ "$watch_file" -nt "$bundle" ]]; then
      return 0
    fi
  done
  local watch_dir
  for watch_dir in "$frontend_dir/src" "$frontend_dir/scripts"; do
    if [[ -d "$watch_dir" ]] && [[ -n "$(find "$watch_dir" -type f -newer "$bundle" -print -quit 2>/dev/null)" ]]; then
      return 0
    fi
  done
  return 1
}

ensure_frontend_bundle() {
  local frontend_dir="${SPACEINVASION_FRONTEND_DIR:-$REPO_DIR/frontend}"
  local npm_bin="${NPM_BIN:-npm}"
  if ! frontend_bundle_needs_build; then
    return 0
  fi
  if ! command -v "$npm_bin" >/dev/null 2>&1; then
    log_msg "Warning: $npm_bin not found; skipping frontend UI bundle build."
    return 0
  fi
  log_msg "Building frontend UI bundle into ${SPACEINVASION_UI_DIR:-spaceinvasion/ui/}..."
  "$npm_bin" --prefix "$frontend_dir" run build
}

if [[ "$CHECK_UI_BUILD_ONLY" -eq 1 ]]; then
  ensure_frontend_bundle
  exit $?
fi

if [[ "$STACK_SCRIPT" == "$BIN_DIR/run_stack.py" ]] && [[ "${STARTER_SKIP_UI_BUILD:-0}" != "1" ]]; then
  ensure_frontend_bundle
fi

# Ensure Podman PostgreSQL container is running when supervising the real stack in postgres mode.
if [[ "$STACK_SCRIPT" == "$BIN_DIR/run_stack.py" ]] && [[ -f "$REPO_DIR/docker-compose.yml" ]]; then
  "$PY_BIN" -c '
import sys
from pathlib import Path
from spaceinvasion.db.backend import ensure_postgres_container_ready, resolve_database_config

argv = sys.argv[1:]
exp_b = None
exp_u = None
for i, a in enumerate(argv):
    if a == "--db-backend" and i + 1 < len(argv):
        exp_b = argv[i + 1]
    elif a.startswith("--db-backend="):
        exp_b = a.split("=", 1)[1]
    elif a == "--db-url" and i + 1 < len(argv):
        exp_u = argv[i + 1]
    elif a.startswith("--db-url="):
        exp_u = a.split("=", 1)[1]
cfg = resolve_database_config(explicit_backend=exp_b, explicit_db_url=exp_u, root_dir=Path("."))
if cfg.backend == "postgres" and cfg.auto_start_podman:
    res = ensure_postgres_container_ready(cfg.postgres_url, root_dir=Path("."), auto_start=True)
    print(f"[DB] Active backend: POSTGRES ({cfg.display_target}) | ready={res.get(\"ok\")}")
else:
    print(f"[DB] Active backend: {cfg.backend.upper()} ({cfg.display_target})")
' "${STACK_ARGS[@]}" || true
fi

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
    log_msg "Anti-bot / CAPTCHA challenge detected (exit code 2). Not restarting automatically — complete verification in Chrome (port 9222), then re-run ./starter.sh."
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
