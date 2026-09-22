# Personal AI Assistant — Build Plan

## Context

Build a self-hosted, personal AI assistant that runs locally on a MacBook Pro (Apple M5, 16 GB RAM) with three tabs:

1. **Chat** — streaming conversation with local or cloud models
2. **Notebooks** — NotebookLM-style: upload sources, ask grounded questions, get cited answers
3. **Agents** — n8n-style visual workflow builder, scoped to AI automation

Models run locally via Ollama to start, with a switch to Claude when quality matters. The app must work in a mobile browser and be convertible to a native app with minimal rework. Deployment comes later; the first goal is a good local application.

**The governing constraint is 16 GB of unified memory, not the 50 GB disk budget.** Disk is effectively unlimited for this project (259 GB free, ~16 GB of actual need). RAM is what caps model size: a 30B-class model needs ~18 GB resident and cannot coexist with Postgres, Docker and a browser. Every model decision below follows from that.

---

## Decisions

| Area | Choice |
|---|---|
| Daily model | `qwen3:8b` Q4_K_M (~5.2 GB) |
| Deep mode | `qwen3:14b` Q4_K_M (~9.3 GB), loaded on demand |
| Cloud escape hatch | Claude via API, selectable per conversation |
| Embeddings | `nomic-embed-text` v1.5, 768-dim (~274 MB) |
| Runtime | Ollama (one server for chat + embeddings, OpenAI-compatible) |
| Frontend | Next.js 16 App Router, TypeScript, Tailwind v4, shadcn/ui |
| Backend | Python 3.12 + FastAPI, `uv` for dependency management |
| Database | Postgres 17 + pgvector — one DB for relational data, JSONB documents *and* vectors |
| Workflow canvas | React Flow + custom DAG executor |
| Auth | Deferred; `user_id` carried from day one, seeded to one local user |

**Why Ollama over MLX:** MLX is meaningfully faster on Apple Silicon, but Ollama serves chat and embedding models from a single OpenAI-compatible endpoint, which the RAG pipeline hits constantly. The provider abstraction (below) makes switching to LM Studio/MLX a config change later.

**Memory budget:** set `OLLAMA_MAX_LOADED_MODELS=1` and `OLLAMA_KEEP_ALIVE=5m`. The 8B and 14B models must never be resident simultaneously — switching to deep mode evicts the 8B.

**Python 3.14 is installed but the backend should pin 3.12.** Several ML/parsing dependencies (docling, torch-backed tokenizers) lag on 3.14. `uv python pin 3.12` in `backend/`.

---

## Why SQL (Postgres), not NoSQL

This was an explicit question, so the reasoning is recorded here rather than left implicit.

**The data is relational almost everywhere.** The three core chains are `user → conversation → message`, `notebook → document → chunk`, and `workflow → version → run → step`. Nearly every query in the app is a join or a filtered traversal down one of those. That is the shape relational databases are built for, and the shape document stores force you to either denormalize or join in application code.

**pgvector collapses two databases into one.** Local MongoDB has no vector search — Atlas Vector Search is cloud-only. So the NoSQL path means a *separate* vector database (Qdrant, Chroma) alongside the document store: two systems to run, two to back up, and a dual-write consistency problem every time a chunk row and its embedding fall out of sync. Postgres stores the chunk text, its metadata and its 768-dim vector in the same row, updated in the same transaction.

**Hybrid retrieval needs both search types in one query.** M4 fuses vector similarity with keyword search using RRF. Postgres evaluates `tsvector` ranking and vector cosine distance in a single SQL statement. The Mongo + Qdrant equivalent is two network round-trips fused in Python — slower and harder to tune.

**Transactions matter more than they look.** Ingesting one document writes a `documents` row plus N `chunks` rows; one workflow run writes a run plus many step rows. These need to commit or fail as a unit, or you get orphaned half-ingested documents that quietly poison retrieval.

**RAM, again.** Postgres idles at ~50–100 MB. MongoDB plus a vector DB costs meaningfully more of a 16 GB budget already dominated by the model.

**Where document flexibility is genuinely needed, use `JSONB`.** These fields are schemaless by nature and should not be normalized:

| Column | Holds |
|---|---|
| `workflow_versions.graph` | the full `{nodes, edges}` canvas definition |
| `workflow_run_steps.input` / `.output` | arbitrary per-node payloads |
| `messages.metadata` | tool calls, citations, token counts, model used |
| `documents.source_meta` | parser-specific structure (page map, tables, headings) |

JSONB with GIN indexes gives document-store flexibility inside the relational database, so there is no second system to operate. Postgres handles both halves of this app's data honestly — that is the whole argument.

---

## Architecture

```
vish_ai_assistant/
  docker-compose.yml          # postgres 17 + pgvector
  .env.example
  docs/
    PLAN.md                   # this plan, versioned in git
    decisions/                # one short ADR per significant choice
      0001-postgres-over-nosql.md
      0002-ollama-runtime.md
      0003-provider-abstraction.md
      0004-own-workflow-engine.md
  backend/
    pyproject.toml            # uv, python 3.12
    alembic/
    app/
      main.py                 # FastAPI app, CORS, SSE
      config.py               # pydantic-settings
      db.py                   # async SQLAlchemy session
      models/                 # ORM tables
      schemas/                # pydantic request/response
      api/
        chat.py  notebooks.py  documents.py  workflows.py  providers.py
      llm/
        base.py               # LLMProvider protocol  <-- the key seam
        ollama.py  anthropic.py  registry.py
      rag/
        ingest.py  chunk.py  store.py  retrieve.py
      workflows/
        executor.py           # topological DAG runner
        registry.py           # node type -> handler
        nodes/                # one module per node type
  frontend/
    app/(app)/chat|notebooks|agents/
    components/ui/            # shadcn
    lib/api.ts  lib/useStream.ts
```

### The provider seam (most important design decision)

`app/llm/base.py` defines one protocol:

```python
class LLMProvider(Protocol):
    async def chat_stream(self, messages, model, **opts) -> AsyncIterator[Chunk]: ...
    async def embed(self, texts: list[str], model: str) -> list[list[float]]: ...
```

`OllamaProvider` and `AnthropicProvider` implement it. A registry maps a model id (`qwen3:8b`, `claude-opus-5`) to a provider instance. `GET /api/providers` returns what is available; the chat UI renders it as a dropdown.

Everything downstream — chat, RAG answering, every workflow LLM node — depends only on this protocol. Switching models, adding a provider, or moving to a hosted model never touches feature code.

### Streaming

FastAPI emits plain SSE (`text/event-stream`) with a small self-defined event shape (`token`, `tool`, `citation`, `done`, `error`). The frontend consumes it with a ~80-line `useStream` hook.

Deliberately *not* using the Vercel AI SDK's `useChat`: its data-stream protocol is a moving target that a Python backend has to chase. A custom hook keeps the wire contract yours. Revisit only if the chat UI grows complex enough to justify it.

### Data model

Core tables, all carrying `user_id` from the start:

- `users` — one seeded row (`local`)
- `conversations`, `messages`
- `notebooks`, `documents`, `chunks`
- `workflows`, `workflow_versions`, `workflow_runs`, `workflow_run_steps`

`chunks.embedding` is `vector(768)` with an HNSW index (`vector_cosine_ops`). `chunks` also holds a generated `tsvector` column for keyword search, plus `page`, `section`, and character offsets to power citations.

---

## Keeping the plan versioned

The plan is a living document in the repo, not a one-time artifact.

- **Location:** `docs/PLAN.md`, committed in M0 before any code.
- **Status markers:** each milestone heading carries `[ ]` → `[~]` → `[x]` (not started / in progress / done). Flip the marker in the same commit that finishes the work, so `git log` ties plan state to actual code.
- **Changes are commits, not overwrites.** When a decision changes mid-build, edit `docs/PLAN.md` and commit with a `docs:` prefix explaining the reason — e.g. `docs: drop docling for pymupdf4llm, torch install too heavy`. The reason is the valuable part; the diff is recoverable either way.
- **Branch per milestone:** `m2-chat-tab`, `m5-workflow-core`. Squash-merge to `main`. This gives a clean one-commit-per-milestone history on `main` with full detail available on the branches.
- **Tag each milestone** on merge (`git tag m2-chat-done`) so you can always get back to a known-working state — valuable when M5/M6 get invasive.
- **ADRs are append-only.** Never rewrite an ADR; supersede it with a new one that links back. A reversed decision with its original reasoning intact is far more useful six months later than a clean file that hides the reversal.

---

## Milestones

Each milestone is independently completable and leaves the app in a working state. Status markers in `docs/PLAN.md` start as `[ ]`.

### [x] M0 — Foundations
*Target: half a day*

- `git init`; monorepo layout above; `.gitignore` for `node_modules`, `.venv`, `__pycache__`, `.env`, `.next`
- **Copy this plan to `docs/PLAN.md` and commit it as the first commit.** It then lives in the repo and every later edit is tracked by git history (`git log -p docs/PLAN.md` shows how the plan evolved and why).
- Write the four ADRs in `docs/decisions/` — each ~15 lines: context, decision, consequences. These capture *why*, which git history alone does not.
- Establish the plan-maintenance convention below
- `docker-compose.yml` with `pgvector/pgvector:pg17`, named volume, port 5432
- `uv init` in `backend/`, pin Python 3.12, FastAPI + uvicorn + SQLAlchemy + asyncpg + pydantic-settings
- `create-next-app` in `frontend/` (TS, Tailwind, App Router), init shadcn/ui
- Install Ollama; `ollama pull qwen3:8b` and `ollama pull nomic-embed-text`
- Three-tab shell with routing and a placeholder page each

**Done when:** `docker compose up` + backend + frontend all run, and the Next.js page renders data fetched from `GET /api/health`.

### [ ] M1 — Data layer and provider abstraction
*Target: 1–2 days*

- Alembic init; migration creating all core tables and the `local` user
- Enable `vector` extension; HNSW index on `chunks.embedding`
- `llm/base.py` protocol; `OllamaProvider`; `AnthropicProvider`
- Model registry driven by config; `GET /api/providers`
- A `scripts/smoke_llm.py` that streams a completion from Ollama *and* Claude

**Done when:** the smoke script streams tokens from both providers through the identical interface.

### [ ] M2 — Chat tab
*Target: 2–3 days*

- `POST /api/chat` → SSE stream; persist user and assistant messages
- Conversation CRUD, sidebar list, rename, delete
- `useStream` hook; token-by-token rendering
- Model dropdown (`qwen3:8b` / `qwen3:14b` / Claude), remembered per conversation
- Markdown + syntax-highlighted code blocks, copy button
- Stop generation, regenerate, edit-and-resend
- Mobile-responsive from the start (collapsible sidebar, sticky composer)

**Done when:** a real multi-turn conversation persists across reloads, streams smoothly, and mid-conversation model switching works.

### [ ] M3 — Notebooks: ingestion
*Target: 2–3 days*

- Notebook CRUD; document upload (PDF, docx, txt, md) and URL fetch
- Parse with `docling` (strong PDF layout/table handling), fall back to plain text
- Chunk ~800 tokens with ~120 overlap, preserving `page`/`section`/offsets
- Embed via `nomic-embed-text`, store in pgvector
- **Gotcha to encode:** nomic-embed requires `search_document: ` prefix on stored chunks and `search_query: ` on queries. Mismatched prefixes silently degrade retrieval — put this in `rag/store.py`, not in calling code.
- Background processing with a status column; UI shows per-document progress

**Done when:** a 50-page PDF uploads, processes, and its chunks are queryable via a test endpoint with sensible similarity scores.

### [ ] M4 — Notebooks: retrieval and grounded chat
*Target: 2–3 days*

- Hybrid retrieval: pgvector cosine + Postgres full-text, fused with Reciprocal Rank Fusion
- Grounded answering prompt with numbered source context
- Inline `[1]`, `[2]` citations that scroll to and highlight the source span
- Notebook-scoped chat with a source panel; per-source include/exclude toggles
- NotebookLM-style extras: auto-summary on upload, suggested starter questions, generated briefing doc

**Done when:** asking a question about uploaded sources returns an answer whose citations, when clicked, land on the correct passage.

### [ ] M5 — Agents: execution core (backend only)
*Target: 3–4 days*

- Workflow CRUD; graph stored as `{nodes, edges}` JSON, versioned on save
- Node registry mapping a type string to `async def run(ctx, inputs) -> outputs`
- Topological-sort executor with per-node timeout and error capture
- Persist every step's inputs and outputs to `workflow_run_steps` — this is what makes debugging tolerable and is the single feature that makes n8n usable
- Initial nodes: **Manual Trigger, LLM, RAG Query, HTTP Request, Branch, Transform, Output**

**Done when:** a JSON-defined workflow runs end to end via `POST /api/workflows/{id}/run` and its full step trace is inspectable.

### [ ] M6 — Agents: visual canvas
*Target: 3–4 days*

- React Flow canvas: drag from palette, connect, pan/zoom, delete
- Per-node config side panel driven by each node type's schema
- Save/load, validation (cycles, orphans, missing required fields)
- Run from the canvas with live per-node status via SSE (idle → running → ok/error)
- Run history with the per-node input/output inspector from M5

**Done when:** a useful workflow — e.g. *fetch a URL → summarize with the local model → write to a notebook* — is built entirely in the UI and runs.

### [ ] M7 — Triggers and scheduling
*Target: 1–2 days*

- Schedule trigger via APScheduler (in-process; note Redis/ARQ if load grows)
- Webhook trigger with a per-workflow token
- Enable/disable toggle; next-run display; run-on-schedule history

**Done when:** a workflow fires on a cron schedule unattended and its runs appear in history.

### [ ] M8 — Mobile and PWA
*Target: 1–2 days*

- Responsive audit across all three tabs; bottom nav on small screens
- 44px touch targets; canvas gets a read-and-run mobile mode (editing stays desktop)
- PWA manifest, icons, service worker; installable to home screen
- Verify on a phone over LAN

**Done when:** the app installs to an iPhone home screen and chat + notebooks are fully usable there.

### [ ] M9 — Auth retrofit (deferred by choice)
*Target: 1–2 days*

Cheap precisely because M1 carried `user_id` throughout: login page, argon2 password hashing, session cookie, route middleware, and swapping the hardcoded local user for the session user. No data migration.

### [ ] M10 — Deployment
*Target: 1–2 days*

Dockerfiles for both services, Tailscale or reverse proxy with TLS, automated `pg_dump` backups, and a decision on where models run (the deploy host needs the same RAM headroom, or point the provider registry at a cloud model).

**Native mobile app**, if wanted, comes after M8 via Capacitor wrapping the existing frontend — no UI rewrite, since the PWA work already did the hard part.

---

## Risks and notes

- **14B deep mode is tight.** ~9.3 GB resident on a 16 GB machine means closing heavy apps. Treat it as a deliberate mode, not a default. If it proves too slow, the Claude path already exists.
- **The Code/Transform node is an arbitrary-execution risk.** Acceptable while single-user on localhost. It must be sandboxed or removed before M10 exposes anything publicly — flagging now because deployment is a stated goal.
- **docling is heavy** (pulls torch). If install friction or disk is a problem, `pymupdf4llm` is a much lighter fallback with worse table extraction.
- **Scope discipline on M5–M6.** The agent tab is roughly half the total work. Resist adding node types until the seven core ones are solid.

---

## Verification

Per milestone, end to end:

- **M0:** `docker compose up -d && cd backend && uv run uvicorn app.main:app --reload` and `cd frontend && npm run dev`; browser shows backend health data.
- **M1:** `uv run python scripts/smoke_llm.py` streams from Ollama and Claude.
- **M2:** hold a 5-turn conversation, reload, confirm persistence; switch models mid-thread.
- **M3:** upload a real multi-page PDF; confirm chunk count, page metadata, and non-degenerate similarity scores.
- **M4:** ask three questions with known answers in the sources; every citation must resolve to the correct passage.
- **M5:** `curl` a workflow run; inspect the step trace in Postgres.
- **M6:** build and run the fetch → summarize → save workflow from the canvas only.
- **M7:** schedule a workflow for two minutes out; confirm unattended execution.
- **M8:** load over LAN on a phone, install to home screen, exercise chat and notebooks.

Add `pytest` for the backend from M1 — especially the chunker, the RRF fusion, and the DAG executor, which are the three places subtle bugs hide quietly.

A milestone is not done until its verification step passes, its status marker in `docs/PLAN.md` is flipped to `[x]`, and the branch is merged and tagged.
