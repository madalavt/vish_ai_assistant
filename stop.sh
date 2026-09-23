#!/usr/bin/env bash
#
# Stop everything started by ./start.sh
#
#   ./stop.sh          stop the UI, the API and Postgres; unload the model
#   ./stop.sh --all    also stop the Ollama service itself
#
# Your data is never touched. Only `docker compose down -v` destroys it, and
# nothing here runs that.
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

export PATH="/opt/homebrew/bin:$PATH"

LOG_DIR="$ROOT/logs"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"

STOP_OLLAMA=false
[ "${1:-}" = "--all" ] && STOP_OLLAMA=true

bold() { printf "\n\033[1m==> %s\033[0m\n" "$1"; }
ok()   { printf "    \033[32m✓\033[0m %s\n" "$1"; }
info() { printf "    · %s\n" "$1"; }

# Stop a service by its recorded pid, falling back to whatever holds the port.
# The fallback matters when a server was started by hand rather than by start.sh.
stop_service() {
  local name="$1" port="$2" pidfile="$LOG_DIR/$3.pid"
  local stopped=false

  if [ -f "$pidfile" ]; then
    local pid
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      # Kill the process group: uvicorn --reload and next dev both fork children
      # that survive a bare kill and keep holding the port.
      kill -TERM -- "-$(ps -o pgid= "$pid" | tr -d ' ')" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
      stopped=true
    fi
    rm -f "$pidfile"
  fi

  local held
  held="$(lsof -ti:"$port" 2>/dev/null || true)"
  if [ -n "$held" ]; then
    # shellcheck disable=SC2086
    kill -TERM $held 2>/dev/null || true
    sleep 1
    held="$(lsof -ti:"$port" 2>/dev/null || true)"
    # shellcheck disable=SC2086
    [ -n "$held" ] && kill -9 $held 2>/dev/null || true
    stopped=true
  fi

  if $stopped; then ok "$name stopped"; else info "$name was not running"; fi
}

bold "Application servers"
stop_service "frontend (port $FRONTEND_PORT)" "$FRONTEND_PORT" frontend
stop_service "backend (port $BACKEND_PORT)" "$BACKEND_PORT" backend

bold "Postgres"
if docker info >/dev/null 2>&1; then
  if [ -n "$(docker ps -q --filter name=vish_ai_postgres 2>/dev/null)" ]; then
    docker compose stop >/dev/null 2>&1
    ok "stopped (data kept)"
  else
    info "was not running"
  fi
else
  info "Docker is not running, nothing to stop"
fi

bold "Ollama"
loaded="$(ollama ps 2>/dev/null | tail -n +2 | awk '{print $1}' || true)"
if [ -n "$loaded" ]; then
  for model in $loaded; do
    ollama stop "$model" >/dev/null 2>&1 || true
    ok "unloaded $model (freed its memory)"
  done
else
  info "no model was loaded"
fi

if $STOP_OLLAMA; then
  brew services stop ollama >/dev/null 2>&1 || true
  ok "service stopped"
else
  info "service left running (idles at ~15 MB; use ./stop.sh --all to stop it)"
fi

printf "\n    Start again with \033[1m./start.sh\033[0m\n\n"
