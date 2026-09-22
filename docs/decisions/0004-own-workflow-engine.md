# 0004 — Build a scoped workflow engine instead of embedding n8n

- **Status:** Accepted
- **Date:** 2026-09-21

## Context

The Agents tab should feel like n8n: a visual canvas of connected nodes. Options
were to self-host real n8n behind an iframe, or build a scoped engine.

## Decision

Build a **scoped AI workflow engine**: React Flow canvas plus a custom async DAG
executor, with a deliberately small node set — Manual Trigger, LLM, RAG Query,
HTTP Request, Branch, Transform, Output.

## Reasoning

- Embedded n8n brings its own auth, UI and data model. It never feels part of
  the app, and wiring it to local models and notebooks still means building
  webhook glue.
- n8n's value is 400+ SaaS integrations. That is not the goal here; the goal is
  automating this assistant's own capabilities.
- ~1 GB of RAM for a second Node app is expensive on a 16 GB machine.

## Consequences

- No third-party integrations for free. The HTTP Request node is the escape
  hatch for anything with a REST API.
- The engine must persist every node's inputs and outputs to
  `workflow_run_steps`. This debuggability is what makes n8n usable, and it is
  the feature most easily skipped and most painful to lack.
- This is roughly half the total project effort (M5 + M6). Node types must stay
  frozen at seven until the canvas and executor are solid.
- The `Transform` node executes user-supplied expressions — acceptable for a
  single local user, but it must be sandboxed or removed before M10 exposes the
  app beyond localhost.
