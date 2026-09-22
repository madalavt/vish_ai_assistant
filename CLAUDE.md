# vish_ai_assistant

Personal self-hosted AI assistant. Three tabs: **Chat**, **Notebooks**
(NotebookLM-style RAG), **Agents** (n8n-style visual workflows).

Read [docs/PLAN.md](docs/PLAN.md) before starting a milestone — it holds the
milestone definitions and their "done when" criteria. Read
[docs/decisions/](docs/decisions/) before changing architecture; those ADRs
explain why things are the way they are.

## The constraint that drives everything

**16 GB unified memory on an Apple M5.** Not disk — there is 250 GB free.

RAM is why the model is 8B and not 30B, why Postgres carries the vectors instead
of a second database, and why the workflow engine is custom instead of an
embedded n8n. When proposing anything that adds a long-running process, account
for its memory first.

- `OLLAMA_MAX_LOADED_MODELS=2` — the chat model and `nomic-embed-text` must both stay resident (RAG needs both per query; ~6.9 GB together). Never load `qwen3:14b` alongside another model.
- Docker Desktop should be capped near 3 GB; it only runs Postgres.

## Stack

| Layer | Choice |
|---|---|
| Frontend | Next.js 16 App Router, TypeScript, Tailwind v4, shadcn/ui |
| Backend | Python **3.12** (not 3.14), FastAPI, async SQLAlchemy, `uv` |
| Database | Postgres 17 + pgvector, migrations via Alembic |
| Models | Ollama: `qwen3:8b` (default), `qwen3:1.7b`, `llama3.2:3b`, `gemma3:4b`, `nomic-embed-text` (768-dim) |
| Cloud | Claude via Anthropic API — optional, off when `ANTHROPIC_API_KEY` is blank |

## Commands

```bash
docker compose up -d                                  # Postgres
cd backend && uv run uvicorn app.main:app --reload    # API on :8000
cd frontend && npm run dev                            # UI on :3000

cd backend && uv run pytest                           # tests
cd backend && uv run alembic upgrade head             # migrations
cd backend && uv run alembic revision --autogenerate -m "msg"
cd backend && uv run ruff check --fix . && uv run ruff format .
```

## Architecture rules

**All model access goes through `backend/app/llm/base.py`.** Chat, RAG answering
and workflow LLM nodes depend only on the `LLMProvider` protocol — never import
`ollama` or `anthropic` outside `app/llm/`. This is what makes swapping models a
config change. See ADR 0003.

**Every table carries `user_id`**, currently pointing at one seeded local user.
Auth is deferred to M9, but the column is not — it exists so that adding login
later is a session check, not a data migration. Never add a user-owned table
without it.

**Streaming is plain SSE** with our own event shape (`token`, `tool`, `citation`,
`done`, `error`), consumed by `frontend/lib/useStream.ts`. We deliberately do not
use the Vercel AI SDK's `useChat`; its protocol is a moving target for a Python
backend.

**JSONB for schemaless data**, not a second database: `workflow_versions.graph`,
`workflow_run_steps.input`/`.output`, `messages.metadata`, `documents.source_meta`.

## Gotchas

- **shadcn here is built on Base UI, not Radix.** Three Radix habits fail
  *silently*, with no error and no type complaint:
  `onSelect` on a menu item does nothing (Base UI exposes `onClick`);
  `asChild` does nothing (use `render={<Button />}`); and
  `DropdownMenuLabel` outside a `DropdownMenuGroup` throws at runtime and
  blanks the page. All three shipped in M2 and were only caught by opening
  the menus in a real browser. **A build that type-checks proves nothing
  about menus** — click them.
- **Never hardcode model capabilities.** `OllamaProvider.available_models`
  reads `capabilities`, `context_length` and `parameter_size` from
  `/api/tags`. The composer's thinking toggle and the M5 workflow LLM node
  both depend on those flags, and `gemma3:4b` genuinely cannot call tools —
  so guessing from a model's name produces a feature that silently does
  nothing. `scripts/bench_models.py` measures any newly pulled model.
- **nomic-embed-text requires prefixes.** Stored chunks need `search_document: `,
  queries need `search_query: `. Mismatching them degrades retrieval silently —
  no error, just worse results. Handle this inside `rag/store.py` so callers
  cannot get it wrong.
- **Pin Python 3.12.** The system has 3.14; docling and torch-backed tokenizers
  lag behind it.
- **Postgres needs `maintenance_work_mem`** raised for HNSW index builds — already
  set in `docker-compose.yml`.
- **The Transform node runs user expressions.** Fine for one local user; must be
  sandboxed before M10 exposes anything past localhost.

## Conventions

- Branch per milestone (`m2-chat-tab`), squash-merge, tag on completion.
- Flip the `[ ]` → `[~]` → `[x]` marker in `docs/PLAN.md` in the same commit that
  changes the milestone's state.
- Plan changes are commits with a `docs:` prefix and a reason in the message. The
  reason is the point; the diff is recoverable anyway.
- ADRs are append-only. Supersede with a new one that links back; never rewrite.
- This repo uses a **repo-local git identity** (personal GitHub account, not work).
  Do not add a remote or push without asking.
