---
description: Start Postgres, Ollama, backend and frontend, and report health
---

Bring the local stack up and report what is actually running:

1. `docker compose up -d`, then wait for the Postgres healthcheck to pass.
2. Check Ollama: `curl -s http://localhost:11434/api/version`. If it is down,
   `brew services start ollama`. Confirm `qwen3:8b` and `nomic-embed-text` are
   present with `ollama list`.
3. Start the backend and frontend in the background.
4. Verify `http://localhost:8000/api/health` and `http://localhost:3000` both
   respond, and report memory in use (`ollama ps`, `docker stats --no-stream`).

Report anything that did not come up, with the error. Do not claim a service is
healthy without having checked it.
