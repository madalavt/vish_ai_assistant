# vish_ai_assistant

A self-hosted personal AI assistant that runs local models, with three tabs:

- **Chat** — streaming conversation, switchable between local models and Claude
- **Notebooks** — NotebookLM-style: upload sources, ask grounded questions, get cited answers
- **Agents** — visual workflow builder for AI automation

Built to run on a MacBook Pro (Apple M5, 16 GB). See [docs/PLAN.md](docs/PLAN.md)
for the full build plan and [docs/decisions/](docs/decisions/) for why the
architecture is what it is.

## Stack

| Layer | Choice |
|---|---|
| Frontend | Next.js 16 (App Router), TypeScript, Tailwind v4, shadcn/ui |
| Backend | Python 3.12, FastAPI, SQLAlchemy (async) |
| Database | Postgres 17 + pgvector |
| Models | Ollama — `qwen3:8b` daily, `qwen3:14b` deep mode, `nomic-embed-text` for RAG |
| Cloud fallback | Claude via the Anthropic API (optional) |

## Prerequisites

- Docker Desktop
- Ollama (`brew install ollama`)
- `uv` (`brew install uv`)
- Node 20+

## Setup

```bash
cp .env.example .env          # adjust if needed

# 1. Database
docker compose up -d

# 2. Models (once; ~5.5 GB)
brew services start ollama
ollama pull qwen3:8b
ollama pull nomic-embed-text

# 3. Backend
cd backend && uv sync && uv run uvicorn app.main:app --reload

# 4. Frontend
cd frontend && npm install && npm run dev
```

Open http://localhost:3000.

## Memory notes

The 16 GB budget is the real constraint, not disk. Two settings matter:

- `OLLAMA_MAX_LOADED_MODELS=1` — never hold the 8B and 14B models at once.
- Keep Docker Desktop's memory allocation low (~3 GB). It only runs Postgres.

## Working conventions

- One branch per milestone (`m2-chat-tab`), squash-merged and tagged on completion.
- `docs/PLAN.md` carries `[ ]` / `[~]` / `[x]` status per milestone; flip the
  marker in the same commit that finishes the work.
- Decisions go in `docs/decisions/` as append-only ADRs. Supersede, never rewrite.
