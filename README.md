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
| Models | Ollama — `qwen3:8b` default, smaller models for speed, `nomic-embed-text` for RAG |
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

## Models

All pulled models appear in the picker automatically — the registry reads
Ollama's `/api/tags`, so `ollama pull <model>` is all that is needed. Labels,
size, context window and capability flags come from Ollama's own metadata
rather than a hardcoded list.

Measured on an Apple M5 / 16 GB, cold start, ~80-word answer:

| Model | Params | Size | Context | tok/s | First token | Tools | Thinking |
|---|---|---|---|---|---|---|---|
| `qwen3:1.7b` | 2.0B | 1.4 GB | 41k | **86** | 1.1s | yes | yes |
| `llama3.2:3b` | 3.2B | 2.0 GB | **131k** | 53 | 2.0s | yes | no |
| `gemma3:4b` | 4.3B | 3.3 GB | **131k** | 43 | 4.1s | **no** | no |
| `qwen3:8b` *(default)* | 8.2B | 5.2 GB | 41k | 24 | 3.1s | yes | yes |
| `nomic-embed-text` | 137M | 0.3 GB | 2k | — | — | — | — |

Which to use:

- **`qwen3:8b`** — the default. Best quality that fits comfortably.
- **`qwen3:1.7b`** — 3.7× faster than the default. Good for quick questions,
  classification, and the high-volume LLM nodes in agent workflows.
- **`llama3.2:3b`** — 131k context. The one to reach for on long documents,
  where the default's 41k is the binding limit.
- **`gemma3:4b`** — strong prose for its size, but **no tool calling**, so it
  cannot drive workflow nodes that need tools.

Re-run the benchmark after pulling anything new:

```bash
cd backend && uv run python scripts/bench_models.py --cold
```

Total on disk: ~11 GB of the 50 GB budget.

## Memory notes

The 16 GB budget is the real constraint, not disk. Two settings matter:

- `OLLAMA_MAX_LOADED_MODELS=2` — keep the chat and embedding models both loaded.
- Keep Docker Desktop's memory allocation low (~3 GB). It only runs Postgres.

## Working conventions

- One branch per milestone (`m2-chat-tab`), squash-merged and tagged on completion.
- `docs/PLAN.md` carries `[ ]` / `[~]` / `[x]` status per milestone; flip the
  marker in the same commit that finishes the work.
- Decisions go in `docs/decisions/` as append-only ADRs. Supersede, never rewrite.
