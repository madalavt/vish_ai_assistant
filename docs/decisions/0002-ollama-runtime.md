# 0002 — Ollama as the local model runtime

- **Status:** Accepted
- **Date:** 2026-09-21

## Context

Target hardware is a MacBook Pro (Apple M5, 16 GB unified memory). Candidate
runtimes: Ollama (llama.cpp), LM Studio / `mlx-lm` (MLX), or raw llama.cpp.
MLX is measurably faster on Apple Silicon for the same quantization.

## Decision

Use **Ollama** for M0–M8. Revisit only if generation speed becomes a real
irritation in daily use.

## Reasoning

- Ollama serves **chat and embedding models from one OpenAI-compatible server**.
  The RAG pipeline calls the embedding endpoint constantly; running a second
  process just for embeddings is avoidable complexity.
- Model management (`ollama pull`), automatic unloading, and keep-alive are
  built in.
- MLX's speed advantage is real but does not change what the app can do, and
  ADR 0003's provider abstraction makes the swap a config change.

## Consequences

- Set `OLLAMA_MAX_LOADED_MODELS=1`: the 8B and 14B models must never be resident
  at the same time on a 16 GB machine.
- Homebrew's service enables `OLLAMA_FLASH_ATTENTION=1` and
  `OLLAMA_KV_CACHE_TYPE=q8_0`, which roughly halves KV-cache memory. Keep both.
- Leaving ~20–35% of available tokens/sec on the table versus MLX, by choice.
