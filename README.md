# vish_ai_assistant

A self-hosted personal AI assistant that runs local models, with three tabs:

- **Chat** — streaming conversation, switchable between local models and Claude
- **Notebooks** — NotebookLM-style: upload sources, ask grounded questions, get cited answers
- **Agents** — visual workflow builder for AI automation

Built to run on a MacBook Pro (Apple M5, 16 GB). See [docs/PLAN.md](docs/PLAN.md)
for the full build plan and [docs/decisions/](docs/decisions/) for why the
architecture is what it is.

---

## One command

```bash
./start.sh
```

Starts Postgres, Ollama, the API and the UI, then prints the URL. Safe to run at
any time — if something is already up it is left alone.

```bash
./stop.sh
```

Stops all of it. Your data is never touched.

Everything below is what those two scripts do, for when you want to run a piece
by hand.

---

## Prerequisites

Install once:

```bash
brew install uv ollama
```

Plus [Docker Desktop](https://www.docker.com/products/docker-desktop/) and Node 20+.

## First run

On a fresh clone, `./start.sh` does all of this for you except the model pulls.
Run those first, because they are several gigabytes:

```bash
brew services start ollama
ollama pull qwen3:8b
ollama pull nomic-embed-text
```

Then:

```bash
./start.sh
```

That creates `.env`, starts Postgres, installs backend and frontend
dependencies, applies migrations, and launches both servers. The dependency
steps take a couple of minutes the first time and are skipped afterwards.

Doing it by hand instead:

```bash
cp .env.example .env
docker compose up -d
cd backend
uv sync --extra cloud
uv run alembic upgrade head
cd ../frontend
npm install
```

## Re-running

Every day after that:

```bash
./start.sh
```

By hand, two terminals. The API on port 8000:

```bash
cd backend
uv run uvicorn app.main:app --reload
```

The UI on port 3000:

```bash
cd frontend
npm run dev
```

Ollama and Postgres normally need nothing — Ollama is a login service and the
Postgres container restarts with Docker.

## Stopping

```bash
./stop.sh
```

Stops the UI, the API and Postgres, and unloads the model to free its memory.
The Ollama service itself is left running, because it idles at about 15 MB. To
stop that too:

```bash
./stop.sh --all
```

By hand: Ctrl+C in each server terminal, then `docker compose stop`.

> **One destructive command.** `docker compose down -v` deletes the named volume
> and every conversation and document with it. There is no undo. Neither
> `./stop.sh` nor `docker compose stop` nor `docker compose down` touches your
> data — only the `-v` removes it.

## Checking and troubleshooting

```bash
curl -s localhost:8000/api/health | python3 -m json.tool
```

`"status": "ok"` means Postgres, pgvector, Ollama and the required models are all
reachable. Anything else names the dependency and how to fix it, often the exact
`ollama pull` you are missing.

Server output goes to `logs/`:

```bash
tail -f logs/backend.log logs/frontend.log
```

| Symptom | Cause |
|---|---|
| `./start.sh` says Docker is not running | Start Docker Desktop, then re-run |
| Chat says "Cannot reach the backend" | The API is not up; check `logs/backend.log` |
| Health reports `degraded` | Read the `checks` block; it names the dependency |
| Model picker shows "No model" | `brew services restart ollama` |
| First message takes ~6s, later ones are instant | Normal — Ollama unloads an idle model after 5 minutes |

> **Copying commands into zsh.** Interactive zsh does not treat `#` as a comment,
> so pasting a line with a trailing comment fails with errors like
> `zsh: number expected`. Every block here is comment-free for that reason.

---

## Stack

| Layer | Choice |
|---|---|
| Frontend | Next.js 16 (App Router), TypeScript, Tailwind v4, shadcn/ui |
| Backend | Python 3.12, FastAPI, SQLAlchemy (async) |
| Database | Postgres 17 + pgvector |
| Models | Ollama — `qwen3:8b` default, smaller models for speed, `nomic-embed-text` for RAG |
| Cloud fallback | Claude via the Anthropic API (optional) |

## Models

All pulled models appear in the picker automatically — the registry reads
Ollama's `/api/tags`, so `ollama pull <model>` is all that is needed. Labels,
size, context window and capability flags come from Ollama's own metadata rather
than a hardcoded list.

Measured on an Apple M5 / 16 GB, cold start, ~80-word answer:

| Model | Params | Size | Context | tok/s | First token | Tools | Thinking |
|---|---|---|---|---|---|---|---|
| `qwen3:1.7b` | 2.0B | 1.4 GB | 41k | **86** | 1.1s | yes | yes |
| `llama3.2:3b` | 3.2B | 2.0 GB | **131k** | 53 | 2.0s | yes | no |
| `gemma3:4b` | 4.3B | 3.3 GB | **131k** | 43 | 4.1s | **no** | no |
| `qwen3:8b` *(default)* | 8.2B | 5.2 GB | 41k | 24 | 3.1s | yes | yes |
| `nomic-embed-text` | 137M | 0.3 GB | 2k | — | — | — | — |

Optional extras:

```bash
ollama pull qwen3:1.7b
ollama pull llama3.2:3b
ollama pull gemma3:4b
```

Which to use:

- **`qwen3:8b`** — the default. Best quality that fits comfortably.
- **`qwen3:1.7b`** — 3.7× faster than the default. Good for quick questions,
  classification, and the high-volume LLM nodes in agent workflows.
- **`llama3.2:3b`** — 131k context. The one to reach for on long documents,
  where the default's 41k is the binding limit.
- **`gemma3:4b`** — strong prose for its size, but **no tool calling**, so it
  cannot drive workflow nodes that need tools.

Benchmark anything newly pulled:

```bash
cd backend
uv run python scripts/bench_models.py --cold
```

Total on disk: ~11 GB of the 50 GB budget.

### Thinking mode

`qwen3:8b` and `qwen3:1.7b` can reason before answering; `llama3.2` and `gemma3`
cannot, and the composer's toggle disables itself for those. The trade is steep —
on the bat-and-ball problem, `qwen3:8b` answers in 3.2s and gets it wrong with
thinking off, and in 22.9s and gets it right with thinking on.

Worth it for maths, logic, debugging and planning. Not worth it for recall,
summarising or ordinary chat, which is why it defaults to off.

## Memory notes

The 16 GB budget is the real constraint, not disk. A single loaded model is by
far the largest consumer; everything else together is under 200 MB.

Set on the Ollama service by Homebrew's default plist:

- `OLLAMA_FLASH_ATTENTION=1` and `OLLAMA_KV_CACHE_TYPE=q8_0`, which roughly halve
  KV-cache memory.

Not currently set, worth considering if models start thrashing:

- `OLLAMA_MAX_LOADED_MODELS=2`, so a chat model and `nomic-embed-text` can stay
  resident together. Every RAG query needs both, and a limit of 1 would evict and
  reload the chat model on each question. Ollama's default has been evicting
  sensibly so far.

Expect roughly one chat model hot at a time. Benchmarking several back to back
will push the machine into swap.

## Working conventions

- One branch per milestone (`m2-chat-tab`), squash-merged and tagged on completion.
- `docs/PLAN.md` carries `[ ]` / `[~]` / `[x]` status per milestone; flip the
  marker in the same commit that finishes the work.
- Decisions go in `docs/decisions/` as append-only ADRs. Supersede, never rewrite.
