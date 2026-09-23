#!/usr/bin/env bash
#
# Start everything: Postgres, Ollama, the API and the UI.
#
# Safe to run any time. On a fresh clone it also does the one-off setup
# (.env, dependencies, migrations); on later runs those steps are skipped.
#
#   ./start.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# Homebrew's bin is not always on PATH in a non-login shell.
export PATH="/opt/homebrew/bin:$PATH"

LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"

bold() { printf "\n\033[1m==> %s\033[0m\n" "$1"; }
ok()   { printf "    \033[32m✓\033[0m %s\n" "$1"; }
info() { printf "    · %s\n" "$1"; }
warn() { printf "    \033[33m!\033[0m %s\n" "$1"; }
die()  { printf "    \033[31m✗\033[0m %s\n" "$1" >&2; exit 1; }

port_is_up() { curl -sf -o /dev/null --max-time 2 "http://localhost:$1$2" 2>/dev/null; }

# ---------------------------------------------------------------- prerequisites
bold "Checking prerequisites"
for cmd in docker uv npm ollama; do
  command -v "$cmd" >/dev/null 2>&1 || die "$cmd not found. See the README prerequisites."
done
ok "docker, uv, npm, ollama found"

if ! docker info >/dev/null 2>&1; then
  die "Docker Desktop is not running. Start it, then re-run ./start.sh"
fi
ok "Docker daemon responding"

# ------------------------------------------------------------------ environment
if [ ! -f .env ]; then
  cp .env.example .env
  ok "created .env from .env.example"
else
  info ".env already present"
fi

# -------------------------------------------------------------------- Postgres
bold "Postgres"
docker compose up -d >/dev/null 2>&1
for _ in $(seq 1 30); do
  status="$(docker inspect --format='{{.State.Health.Status}}' vish_ai_postgres 2>/dev/null || echo starting)"
  [ "$status" = "healthy" ] && break
  sleep 2
done
[ "${status:-}" = "healthy" ] || die "Postgres did not become healthy. Check: docker compose logs postgres"
ok "healthy on port 5432"

# ---------------------------------------------------------------------- Ollama
bold "Ollama"
if ! port_is_up 11434 /api/version; then
  brew services start ollama >/dev/null 2>&1 || true
  for _ in $(seq 1 15); do port_is_up 11434 /api/version && break; sleep 1; done
fi
port_is_up 11434 /api/version || die "Ollama is not responding. Try: brew services restart ollama"
ok "responding on port 11434"

# Required models. Pulling is gigabytes, so tell the user rather than deciding.
missing=""
for model in qwen3:8b nomic-embed-text; do
  ollama list 2>/dev/null | awk '{print $1}' | sed 's/:latest$//' | grep -qx "${model%:latest}" \
    || missing="$missing $model"
done
if [ -n "$missing" ]; then
  warn "missing model(s):$missing"
  for model in $missing; do info "run: ollama pull $model"; done
else
  ok "qwen3:8b and nomic-embed-text present"
fi

# --------------------------------------------------------------------- backend
bold "Backend"
if [ ! -d backend/.venv ]; then
  info "installing Python dependencies (first run, this takes a minute)"
  (cd backend && uv sync --extra cloud >/dev/null 2>&1) || die "uv sync failed"
  ok "dependencies installed"
fi

(cd backend && uv run alembic upgrade head >/dev/null 2>&1) || die "migrations failed. Check: cd backend && uv run alembic upgrade head"
ok "database schema up to date"

if port_is_up "$BACKEND_PORT" /api/health; then
  info "already running on port $BACKEND_PORT"
else
  (cd backend && nohup uv run uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT" --reload \
    > "$LOG_DIR/backend.log" 2>&1 & echo $! > "$LOG_DIR/backend.pid")
  for _ in $(seq 1 40); do port_is_up "$BACKEND_PORT" /api/health && break; sleep 1; done
  port_is_up "$BACKEND_PORT" /api/health || die "backend failed to start. Check: tail logs/backend.log"
  ok "started on port $BACKEND_PORT (logs/backend.log)"
fi

# -------------------------------------------------------------------- frontend
bold "Frontend"
if [ ! -d frontend/node_modules ]; then
  info "installing Node dependencies (first run, this takes a minute)"
  (cd frontend && npm install >/dev/null 2>&1) || die "npm install failed"
  ok "dependencies installed"
fi

if port_is_up "$FRONTEND_PORT" /; then
  info "already running on port $FRONTEND_PORT"
else
  (cd frontend && nohup npm run dev -- --port "$FRONTEND_PORT" \
    > "$LOG_DIR/frontend.log" 2>&1 & echo $! > "$LOG_DIR/frontend.pid")
  for _ in $(seq 1 60); do port_is_up "$FRONTEND_PORT" /chat && break; sleep 1; done
  port_is_up "$FRONTEND_PORT" /chat || die "frontend failed to start. Check: tail logs/frontend.log"
  ok "started on port $FRONTEND_PORT (logs/frontend.log)"
fi

# --------------------------------------------------------------------- summary
bold "Ready"
health="$(curl -s "http://localhost:$BACKEND_PORT/api/health" 2>/dev/null || echo '{}')"
status="$(printf '%s' "$health" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("status","unknown"))' 2>/dev/null || echo unknown)"

if [ "$status" = "ok" ]; then
  ok "all dependencies reachable"
else
  warn "health reports '$status' — details: curl -s localhost:$BACKEND_PORT/api/health"
fi

printf "\n    Open \033[1mhttp://localhost:%s\033[0m\n" "$FRONTEND_PORT"
printf "    Logs: tail -f logs/backend.log logs/frontend.log\n"
printf "    Stop: ./stop.sh\n\n"
