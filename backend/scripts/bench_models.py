"""Measure every pulled chat model: load time, first token, throughput.

Useful for deciding which model to default to, and for checking that a new
model is worth its memory on a 16 GB machine.

    uv run python scripts/bench_models.py
"""

import argparse
import asyncio
import time

import httpx

from app.config import get_settings
from app.llm import ChatMessage, get_registry

PROMPT = "Explain what a vector database is, in about 80 words."


async def unload(model: str) -> None:
    """Evict a model so the next run measures a genuine cold start."""
    settings = get_settings()
    async with httpx.AsyncClient(base_url=settings.ollama_base_url, timeout=60.0) as client:
        # keep_alive: 0 tells Ollama to drop it immediately.
        await client.post("/api/chat", json={"model": model, "messages": [], "keep_alive": 0})


async def bench(model: str, prompt: str) -> dict:
    registry = get_registry()
    provider = await registry.provider_for(model)

    started = time.monotonic()
    first_token: float | None = None
    chars = 0
    out_tokens: int | None = None
    error: str | None = None

    async for chunk in provider.chat_stream(
        [ChatMessage(role="user", content=prompt)], model=model, max_tokens=250
    ):
        if chunk.type == "text":
            if first_token is None:
                first_token = time.monotonic()
            chars += len(chunk.text)
        elif chunk.type == "error":
            error = chunk.text
            break
        elif chunk.type == "done":
            out_tokens = chunk.usage.get("output_tokens")
            break

    total = time.monotonic() - started
    ttft = (first_token - started) if first_token else total
    # Generation rate excludes the wait for the first token, which is dominated
    # by prompt processing and model load.
    gen_seconds = max(total - ttft, 1e-6)

    return {
        "model": model,
        "error": error,
        "ttft": ttft,
        "total": total,
        "chars": chars,
        "tokens": out_tokens,
        "tok_per_s": (out_tokens / gen_seconds) if out_tokens else None,
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", default=PROMPT)
    parser.add_argument(
        "--cold",
        action="store_true",
        help="unload each model first, to measure load time too",
    )
    args = parser.parse_args()

    registry = get_registry()
    chat = [m for m in await registry.list_models(refresh=True) if m.kind == "chat" and m.local]
    if not chat:
        print("No local chat models found. Is Ollama running?")
        return 1

    rows = []
    for info in chat:
        if args.cold:
            await unload(info.id)
            await asyncio.sleep(1)
        print(f"  benchmarking {info.id} …", flush=True)
        result = await bench(info.id, args.prompt)
        result["size_gb"] = (info.size_bytes or 0) / 1e9
        result["params"] = info.parameter_size
        rows.append(result)

    print(f"\n{'model':<16} {'params':<8} {'size':<8} {'ttft':<8} {'tok/s':<8} {'total':<8}")
    print("-" * 60)
    for r in rows:
        if r["error"]:
            print(f"{r['model']:<16} FAILED: {r['error'][:40]}")
            continue
        rate = f"{r['tok_per_s']:.1f}" if r["tok_per_s"] else "-"
        print(
            f"{r['model']:<16} {str(r['params'] or '-'):<8} {r['size_gb']:.2f} GB  "
            f"{r['ttft']:.2f}s   {rate:<8} {r['total']:.2f}s"
        )

    print("\n(ttft = time to first token; tok/s excludes that wait)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
