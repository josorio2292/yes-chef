# Yes Chef

AI-powered catering quote estimator. Submit a menu → get a fully-priced ingredient cost quote.

## How It Works

You POST a menu specification (event name, guest count, list of dishes). The system decomposes each dish into purchasable ingredients using Exa recipe retrieval + an LLM, then resolves each ingredient against a Sysco supplier catalog using pgvector similarity search + an LLM matcher. Matched items are priced from case-level UOM data and assembled into a structured quote. The entire pipeline runs asynchronously — the POST returns immediately and clients stream live progress via SSE.

---

## Quick Start

### Prerequisites

- Docker & Docker Compose
- Node.js 20+ and pnpm (for frontend dev)
- Python 3.12+ and uv (for backend dev)

### Environment

```bash
cp .env.example .env
# Fill in: OPENAI_API_KEY, EXA_API_KEY, OPENROUTER_API_KEY, SYSCO_CSV_PATH, DATABASE_URL
```

### Run

```bash
docker compose up -d --build
# API:          http://localhost:8000
# Frontend:     http://localhost:3000
# Frontend dev: cd frontend && pnpm install && pnpm dev → http://localhost:5173
```

### Test

```bash
# Backend
uv run pytest tests/ -v

# Frontend
cd frontend
pnpm run typecheck
pnpm run lint
pnpm run format:check
```

---

## Architecture

```
React SPA ←→ FastAPI ←→ Orchestrator ←→ [Decomposition Agent, Resolution Agent] ←→ PostgreSQL + pgvector
                                    ↕
                              EventBus (SSE)
```

- **Fire-and-forget processing** — `POST /quotes` returns immediately with a `quote_id`; the orchestrator runs in a background task. Clients stream progress via `GET /quotes/{id}/stream`.
- **Semaphore-bounded concurrency** — `asyncio.Semaphore(3)` bounds parallel LLM calls per quote, keeping connection pool pressure low and API costs predictable.
- **Two-stage pipeline** — Decompose (Exa + LLM → ingredient list) then Resolve (cache + pgvector + LLM → catalog match + price). Stages are independent and individually checkpointed.
- **Checkpoint resumability** — Each menu item persists its state (`pending → decomposing → decomposed → resolving → completed`) and intermediate results to a `step_data` JSON column. An interrupted quote resumes from the last checkpoint on restart — no work is lost. On startup, the server automatically detects any quotes left in `"processing"` status (e.g. from a prior crash) and resumes them as background tasks. DB errors during recovery are caught and logged so a bad quote never prevents the server from starting.
- **Ingredient cache** — Resolved ingredients are cached in `ingredient_cache` keyed by normalized name. Subsequent quotes skip LLM calls entirely for already-resolved ingredients, accumulating learnings across the system's lifetime.
- **pgvector HNSW** — Supplier catalog (~565 items) is embedded with `text-embedding-3-small` (1536d) and indexed with HNSW (`m=16, ef_construction=64`). Embedding happens once on first startup; searches are fast cosine similarity lookups.

---

## API Reference

| Method | Path | Description | Status Codes |
|--------|------|-------------|--------------|
| `GET` | `/health` | Liveness check | 200 |
| `GET` | `/quotes` | List all quotes with summary stats | 200 |
| `POST` | `/quotes` | Submit a menu spec, start processing | 201 |
| `GET` | `/quotes/{id}` | Poll status and per-item progress | 200, 404 |
| `GET` | `/quotes/{id}/result` | Fetch the completed quote | 200, 404, 409 |
| `GET` | `/quotes/{id}/stream` | SSE stream of processing events | 200, 404 |

### curl examples

```bash
# Create a quote
curl -X POST http://localhost:8000/quotes \
  -H "Content-Type: application/json" \
  -d @data/menu_spec.json
# → {"quote_id": "uuid", "status": "pending"}

# Poll status
curl http://localhost:8000/quotes/<quote_id>

# Stream live progress
curl -N http://localhost:8000/quotes/<quote_id>/stream

# Get final result (once status is "completed" or "completed_with_errors")
curl http://localhost:8000/quotes/<quote_id>/result
```

---

## Frontend

Four views wired to the quote lifecycle:

- **Dashboard** (`/`) — lists all quotes with status, item counts, and links to Kitchen or Pass
- **Submit** (`/new`) — form to create a new quote from a menu spec
- **Kitchen** (`/kitchen/:quoteId`) — live ticket rail showing per-item step progress via SSE
- **Pass** (`/pass/:quoteId`) — final quote with expandable ingredient detail and match source badges

**Tech:** React 19, TypeScript, Tailwind CSS 4, shadcn/ui, Motion, React Query, Zod

---

## Project Structure

```
yes-chef-impl/
├── src/yes_chef/
│   ├── api/                    # FastAPI app, endpoints, SSE streaming
│   ├── orchestrator/           # Quote lifecycle, concurrency, quote assembly
│   ├── decomposition/          # Exa recipe search + LLM ingredient extraction
│   ├── resolution/             # Cache → pgvector search → LLM catalog matching
│   ├── catalog/                # Embedding search, pricing, Sysco CSV ingestion
│   ├── db/                     # SQLAlchemy models, async session factory
│   ├── config.py               # Environment-based settings
│   └── events.py               # Async pub/sub EventBus for SSE
├── frontend/src/
│   ├── views/                  # DashboardView, SubmitView, KitchenView, PassView
│   ├── components/             # Shared UI components
│   ├── api.ts                  # React Query hooks
│   └── schemas.ts              # Zod request/response schemas
├── frontend/eslint.config.js
├── frontend/.prettierrc.json
├── frontend/.prettierignore
├── alembic/                    # Database migrations
├── tests/                      # pytest suite
├── data/
│   ├── sysco_catalog.csv       # Sysco price catalog
│   ├── menu_spec.json          # Example menu specification
│   └── quote_schema.json       # Output quote schema
├── docker-compose.yml
├── Dockerfile                  # API image (Python 3.12, uv)
├── entrypoint.sh               # Runs migrations → uvicorn
└── pyproject.toml
```

---

## Tech Stack

| Backend | Frontend |
|---------|----------|
| Python 3.12, uv | React 19, TypeScript |
| FastAPI + uvicorn | Vite, Tailwind CSS 4 |
| PydanticAI (LLM agents) | shadcn/ui, Motion |
| OpenAI `text-embedding-3-small` | React Query |
| Exa API (recipe research) | Zod |
| PostgreSQL 16 + pgvector | pnpm |
| SQLAlchemy (async) + Alembic | ESLint, Prettier |
| Docker Compose | |

---

## Design Decisions

**Why fire-and-forget?** Processing a full menu takes 30–120 seconds — well past any reasonable HTTP timeout. The POST creates the quote and returns; work runs in a background task; clients poll or stream.

**Why `asyncio.Semaphore` not Celery?** Prototype scope, single-server deployment. Semaphore gives bounded concurrency without a broker, workers, or ops overhead.

**Why two LLM stages?** Decomposition and resolution have different inputs, tools, and failure modes. Separating them enables independent checkpointing, isolated context windows, and cleaner retries.

**Why in-memory EventBus?** Prototype scope, single server. SSE subscribers live in the same process as the publisher. A Redis pub/sub channel would be the natural replacement for multi-instance deployment.

---

## What Could Be Improved

- Add a more robust worker
- Multi-provider catalog support (US Foods, Restaurant Depot)
- Temporal.io for durable, multi-node workflow execution
- Structured logging with quote/item correlation IDs + token cost tracking per quote (tracking)
- Invalidate cache on Catalog update
- Iterate on CatalogService embeddings

---

## Documentation

- [System Specification](docs/spec.md)
- [Architecture Plan](docs/plan.md)
- [Implementation Tasks](docs/tasks.md)
- [Key Concepts](docs/concepts.md) — architecture deep-dive, diagrams, design decisions
- [Design System](.interface-design/system.md)
