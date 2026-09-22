"""M1 verification: stream from every configured provider through one interface.

The point is not that the models work — it is that chat.py, the RAG answerer and
every workflow LLM node can reach any of them without knowing which vendor is
behind it (ADR 0003).

    uv run python scripts/smoke_llm.py
    uv run python scripts/smoke_llm.py --prompt "Explain HNSW in one sentence."
"""

import argparse
import asyncio
import sys
import time

from app.llm import ChatMessage, ProviderError, get_registry

PROMPT = "Reply with exactly one short sentence about why local models are useful."


async def stream_one(model: str, prompt: str, *, max_chars: int = 400) -> bool:
    """Stream from one model. Returns True on a clean finish."""
    registry = get_registry()
    provider = await registry.provider_for(model)

    print(f"\n\033[1m{model}\033[0m  (provider: {provider.name})")
    print("-" * 60)

    started = time.monotonic()
    text_chars = 0
    first_token_at: float | None = None
    failed: str | None = None

    async for chunk in provider.chat_stream(
        [ChatMessage(role="user", content=prompt)],
        model=model,
        max_tokens=300,
    ):
        if chunk.type == "text":
            if first_token_at is None:
                first_token_at = time.monotonic()
            if text_chars < max_chars:
                sys.stdout.write(chunk.text)
                sys.stdout.flush()
            text_chars += len(chunk.text)
        elif chunk.type == "thinking":
            pass  # not requested here; exercised by the chat tab
        elif chunk.type == "error":
            failed = chunk.text
            break
        elif chunk.type == "done":
            elapsed = time.monotonic() - started
            ttft = (first_token_at - started) if first_token_at else elapsed
            out = chunk.usage.get("output_tokens")
            rate = f", {out / elapsed:.1f} tok/s" if out and elapsed > 0 else ""
            print(
                f"\n\n  → stop={chunk.stop_reason}, {text_chars} chars, "
                f"ttft={ttft:.2f}s, total={elapsed:.2f}s{rate}"
            )
            return True

    if failed:
        print(f"\n\033[31m  ✗ {failed}\033[0m")
    else:
        print("\n\033[31m  ✗ stream ended without a done chunk\033[0m")
    return False


async def check_embeddings() -> bool:
    registry = get_registry()
    try:
        doc = await registry.embed_documents(["Postgres stores the vectors."])
        query = await registry.embed_query("Where are vectors stored?")
    except ProviderError as exc:
        print(f"\033[31m  ✗ {exc}\033[0m")
        return False

    # Prefixes differ for documents and queries; identical vectors would mean
    # the distinction was lost somewhere.
    same = doc[0][:8] == query[:8]
    print(f"  document vector: {len(doc[0])} dims")
    print(f"  query vector:    {len(query)} dims")
    print(f"  prefixes applied distinctly: {'no — check rag/store.py' if same else 'yes'}")
    return len(doc[0]) == len(query) and not same


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", default=PROMPT)
    args = parser.parse_args()

    registry = get_registry()
    models = await registry.list_models(refresh=True)
    chat = [m for m in models if m.kind == "chat"]

    print("\033[1mAvailable models\033[0m")
    for m in models:
        where = "local" if m.local else "cloud"
        print(f"  {m.id:<28} {m.provider:<10} {m.kind:<10} {where}")

    if not chat:
        print("\n\033[31mNo chat models. Is Ollama running? ollama pull qwen3:8b\033[0m")
        return 1

    print("\n\033[1mEmbeddings\033[0m")
    embeddings_ok = await check_embeddings()

    # One model per provider is enough to prove the interface.
    per_provider: dict[str, str] = {}
    for m in chat:
        per_provider.setdefault(m.provider, m.id)

    results = {model: await stream_one(model, args.prompt) for model in per_provider.values()}

    print("\n" + "=" * 60)
    print("\033[1mSummary\033[0m")
    print(f"  embeddings           {'ok' if embeddings_ok else 'FAILED'}")
    for model, ok in results.items():
        print(f"  {model:<20} {'ok' if ok else 'FAILED'}")

    missing = {"ollama", "anthropic"} - set(per_provider)
    if missing:
        print(
            f"\n  note: {', '.join(sorted(missing))} not exercised — "
            "set ANTHROPIC_API_KEY in .env to cover the cloud path."
        )

    return 0 if embeddings_ok and all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
