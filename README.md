# Yes Chef — AI Catering Estimation Agent

An AI-powered system that takes a catering menu specification and produces per-unit ingredient cost estimates. Built for [Elegant Foods](https://www.elegantfoods.com/) to automate the ingredient costing phase of their bid estimation workflow.

Given a menu spec (32 items across appetizers, mains, desserts, and cocktails) and a Sysco price catalog (~565 items), the agent:

1. **Decomposes** each dish into base purchasable ingredients using recipe retrieval (Exa) + LLM extraction (PydanticAI)
2. **Resolves** each ingredient against the Sysco catalog using pgvector similarity search + LLM matching
3. **Prices** each match by parsing case-level UOM into per-serving costs
4. **Produces** a structured quote conforming to `quote_schema.json`

Processing is resumable, observable via SSE, and designed for long-horizon runs (50-100+ items).

---

## Quick Start (Local)

### Prerequisites

- Docker & Docker Compose
- An OpenAI-compatible API key (for embeddings + LLM)

### 1. Clone and configure

```bash
git clone <repo-url>
cd yes-chef-impl

cp .env.example .env
# Edit .env with your API keys:
#   OPENAI_API_KEY or OPENROUTER_API_KEY
#   DECOMPOSITION_MODEL=openai:gpt-4o-mini
#   MATCHING_MODEL=openai:gpt-4o-mini
```

### 2. Start all services

```bash
docker compose up -d
```

This starts three containers:
- **postgres** (pgvector:pg16) on port 5432 — vector-enabled database
- **api** (FastAPI/uvicorn) on port 8000 — runs migrations on startup, ingests Sysco catalog if empty
- **frontend** (nginx) on port 3000 — React UI, proxies `/api/` to backend

### 3. Submit a job

```bash
# Submit the provided menu specification
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d @data/menu_spec.json

# Response: {"job_id": "uuid", "status": "pending"}
```

### 4. Track progress

```bash
# Poll status
curl http://localhost:8000/jobs/<job_id>

# Or stream real-time events (SSE)
curl -N http://localhost:8000/jobs/<job_id>/stream
```

### 5. Get the quote

```bash
# Available once status is "completed" or "completed_with_errors"
curl http://localhost:8000/jobs/<job_id>/quote
```

### 6. Use the UI

Open **http://localhost:3000** in your browser. The interface mirrors a kitchen workflow:
- **Submit** — paste menu JSON or upload a file
- **Kitchen** — live ticket rail showing each item's processing status
- **The Pass** — final quote with expandable ingredient details and source badges

---

## Cloud Deployment (Render)

The system deploys as three services on [Render](https://render.com/). All configuration is in the existing `Dockerfile`, `frontend/Dockerfile`, and `docker-compose.yml`.

### Step 1: Create a PostgreSQL database

1. Go to Render Dashboard → **New** → **PostgreSQL**
2. Select the **Free** plan (or Starter for persistence)
3. Name: `yeschef-db`
4. Note the **Internal Database URL** (starts with `postgresql://`)
5. After creation, connect and enable pgvector:
   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   ```

### Step 2: Deploy the API

1. **New** → **Web Service** → connect your GitHub repo
2. Configure:
   - **Name:** `yeschef-api`
   - **Root Directory:** `.` (repo root)
   - **Runtime:** Docker
   - **Dockerfile Path:** `./Dockerfile`
   - **Instance Type:** Starter ($7/mo) or Free
3. Set environment variables:
   - `DATABASE_URL` — the Internal Database URL from Step 1, but replace `postgresql://` with `postgresql+asyncpg://`
   - `OPENAI_API_KEY` — your OpenAI key (or `OPENROUTER_API_KEY` for OpenRouter)
   - `DECOMPOSITION_MODEL` — `openai:gpt-4o-mini`
   - `MATCHING_MODEL` — `openai:gpt-4o-mini`
   - `SYSCO_CSV_PATH` — `/app/data/sysco_catalog.csv` (bundled in Docker image)
4. Health check path: `/health`
5. Deploy. On first start, the entrypoint runs Alembic migrations and ingests the Sysco catalog (~565 items, ~2 min for embeddings).

### Step 3: Deploy the Frontend

1. **New** → **Static Site** (or Web Service with Docker)
2. Configure:
   - **Root Directory:** `frontend`
   - If Static Site: **Build Command:** `pnpm install && pnpm run build`, **Publish Directory:** `dist`
   - If Docker: use `frontend/Dockerfile`
3. Add a rewrite rule for SPA routing: `/* → /index.html` (status 200)
4. Set the API proxy — configure a route rule: `/api/*` → `https://yeschef-api.onrender.com/*`

### Step 4: Verify

```bash
# Health check
curl https://yeschef-api.onrender.com/health
# → {"status": "ok"}

# Submit a job
curl -X POST https://yeschef-api.onrender.com/jobs \
  -H "Content-Type: application/json" \
  -d @data/menu_spec.json
```

---

## Architecture

### Orchestration Design

```
Menu Spec (JSON)
       │
       ▼
  ┌─────────┐
  │   API    │  POST /jobs → creates Job + WorkItems
  │ (FastAPI)│  GET /jobs/{id}/stream → SSE events
  └────┬─────┘
       │ fire-and-forget
       ▼
  ┌──────────────┐
  │ Orchestrator  │  Semaphore-bounded concurrency (default 3)
  │              │  Atomic checkpoints after each step
  └──┬───────┬───┘
     │       │
     ▼       ▼        (per work item, concurrent)
  ┌──────┐ ┌──────┐
  │Decomp│ │Decomp│   Step 1: Exa recipe retrieval + PydanticAI extraction
  └──┬───┘ └──┬───┘         → checkpoint: status="decomposed", step_data={ingredients}
     │        │
     ▼        ▼
  ┌───────┐ ┌───────┐
  │Resolve│ │Resolve│  Step 2: Cache lookup → pgvector search → PydanticAI matching
  └──┬────┘ └──┬────┘        → checkpoint: status="completed", step_data={matches, cost}
     │         │
     ▼         ▼
  ┌────────────────┐
  │  Quote Assembly │  Aggregate all completed items → structured JSON
  └────────────────┘
```

**Why this design:**

The brief explicitly calls out three problems with naive sequential processing: **context degradation**, **no recoverability**, and **no observability**. The architecture addresses each:

1. **Context isolation** — Each menu item gets its own decomposition and resolution context. The LLM never accumulates a growing conversation history across items. This eliminates context degradation at item 40 of 50.

2. **Checkpoint-based resumability** — Each work item progresses through atomic state transitions (`pending → decomposing → decomposed → resolving → completed`). State is persisted to PostgreSQL after every step. If the system crashes mid-run, restart picks up from the last checkpoint — completed items are skipped, decomposed items resume at resolution.

3. **SSE observability** — An in-process EventBus publishes granular events (`item_step_change`, `item_completed`, `item_failed`, `job_completed`) to any connected SSE client. The frontend renders these as a live ticket rail.

### Module Breakdown

| Module | Responsibility |
|--------|---------------|
| `api/app.py` | FastAPI endpoints, job submission, SSE streaming, quote retrieval |
| `orchestrator/engine.py` | Job lifecycle, concurrent item processing, checkpointing, quote assembly |
| `decomposition/engine.py` | Exa recipe search → PydanticAI structured extraction of ingredients |
| `resolution/engine.py` | Ingredient cache fast-path → pgvector catalog search → PydanticAI matching agent |
| `catalog/service.py` | Embedding-based search (pgvector cosine similarity), pricing delegation |
| `catalog/provider.py` | Sysco CSV parser, `CatalogRecord` model, `get_price()` |
| `db/models.py` | SQLAlchemy ORM: Job, WorkItem, CatalogItem (pgvector), IngredientCache |
| `events.py` | Async pub/sub EventBus for SSE streaming |

---

## Performance Design

### State & Persistence

Every work item checkpoint is an atomic database write. The system never holds state only in memory:

- **Job table** — tracks overall status (`pending → processing → completed`)
- **WorkItem table** — per-item status + `step_data` JSON column stores intermediate results (ingredients after decomposition, matches after resolution)
- **IngredientCache table** — global, cross-job cache mapping normalized ingredient names to catalog items

This means a hard kill (`docker compose down`) mid-run loses zero completed work. On restart, the orchestrator queries work items by status and resumes only incomplete ones.

### Ingredient Cache (Carry-Forward Learnings)

The brief specifically requires: *"if the agent discovers that wagyu beef isn't available from Sysco, it shouldn't re-discover this when it encounters another wagyu dish."*

The `ingredient_cache` table maps normalized ingredient names → `(source_item_id, source, provider)`. When the resolution engine encounters "wagyu beef" a second time:

1. Cache lookup finds the previous result (e.g., `source="not_available"`)
2. Returns immediately — **zero LLM calls, zero embedding queries**
3. Cache entries persist across jobs, so learnings accumulate over the system's lifetime

Cache invalidation: if a cached `source_item_id` fails price lookup (item removed from catalog), the entry is deleted and the agent re-resolves.

### Context Management

Each LLM call receives only the context it needs — no accumulated history:

- **Decomposition agent** receives: dish name + description + Exa-retrieved recipe text (capped at 3000 chars per source). Temperature 0, structured output.
- **Matching agent** receives: ingredient name + serving quantity + catalog search results (top 5 by embedding similarity). Has tools to search, price, and cache — but never sees other items or the full menu.

This prevents context window degradation on large menus and keeps each call deterministic and auditable.

### Concurrency

Work items process concurrently within a job, bounded by an `asyncio.Semaphore` (default `max_concurrent=3`). Each item manages its own short-lived database sessions — connections are released during LLM API calls, not held open. This keeps the connection pool healthy under load.

### Catalog Search

The Sysco catalog is embedded using OpenAI `text-embedding-3-small` (1536 dimensions) and stored in PostgreSQL via pgvector with an HNSW index (`m=16, ef_construction=64`). Embedding is a one-time operation on first startup (~2 minutes for 565 items). Subsequent searches are sub-millisecond cosine similarity lookups.

---

## API Reference

### `GET /health`
Returns `{"status": "ok"}`. Used for container health checks.

### `POST /jobs` → 201
Submit a menu specification for processing.

**Request body:** JSON matching the menu spec format (see `data/menu_spec.json`)

**Response:**
```json
{"job_id": "uuid", "status": "pending"}
```

### `GET /jobs/{job_id}` → 200 | 404
Get job status and per-item progress.

**Response:**
```json
{
  "job_id": "uuid",
  "status": "processing",
  "total_items": 32,
  "completed_items": 15,
  "failed_items": 0,
  "items": [
    {"item_name": "Filet Mignon", "step": "resolving", "status": "resolving"}
  ]
}
```

### `GET /jobs/{job_id}/quote` → 200 | 404 | 409
Get the completed quote. Returns 409 if job is still processing.

**Response:** Conforms to `quote_schema.json` — see `data/quote_schema.json`.

### `GET /jobs/{job_id}/stream` → SSE
Real-time event stream. Events: `connected`, `item_step_change`, `item_completed`, `item_failed`, `job_completed`.

---

## What I Would Improve With More Time

### Production Reliability
- **Per-ingredient checkpointing** — Currently, resolution checkpoints at the work-item level. If a dish has 12 ingredients and fails on ingredient 8, all 12 are re-resolved on retry. Finer-grained checkpointing would save the 7 completed ingredient matches.
- **Structured retry with backoff** — LLM API calls currently fail-fast. Adding exponential backoff with jitter would handle transient rate limits and API outages gracefully.
- **Dead letter queue** — Failed items are marked `status="failed"` but there's no mechanism to retry them selectively. A DLQ pattern would let operators re-queue specific failures.

### Cost Accuracy
- **UOM parsing engine** — The current system relies on the LLM to interpret Sysco's unit-of-measure strings (e.g., "20/8 OZ", "6/#10"). A deterministic parser with regex + lookup tables would be more reliable and auditable. (Analysis already done — see UOM research in the spec repo.)
- **Quantity normalization** — Per-serving quantities from decomposition are free-text ("2 tbsp", "0.5 oz"). A normalization layer converting to standard units before cost calculation would improve accuracy.
- **Multi-provider support** — The provider interface (`CatalogProvider`) is designed for multiple suppliers, but only Sysco is implemented. Adding US Foods, Restaurant Depot, or specialty vendors would improve coverage.

### Observability
- **Structured logging with correlation IDs** — Currently uses Python `logging`. Structured JSON logs with job_id/item_id correlation would integrate with Datadog, CloudWatch, or similar.
- **Cost tracking per job** — Track LLM token usage and embedding API calls per job for budget monitoring against the $50 constraint.
- **Agent reasoning traces** — PydanticAI supports capturing the agent's tool calls and reasoning. Persisting these would let estimators audit *why* an ingredient was matched to a specific catalog item.

### Scale
- **Temporal.io migration** — The current `asyncio.Semaphore` orchestrator works well for single-node deployment. For multi-node or high-volume processing, migrating to Temporal would provide durable execution, automatic retries, and workflow versioning out of the box.
- **Embedding refresh pipeline** — Catalog re-ingestion during live traffic is handled via soft-delete flags, but there's no scheduled refresh or change-detection. A periodic sync job would keep prices current.

---

## Project Structure

```
yes-chef-impl/
├── src/yes_chef/
│   ├── api/app.py              # FastAPI application (endpoints, SSE, quote assembly)
│   ├── orchestrator/engine.py  # Job processing pipeline (concurrency, checkpointing)
│   ├── decomposition/engine.py # Recipe retrieval (Exa) + ingredient extraction (PydanticAI)
│   ├── resolution/engine.py    # Cache → pgvector search → LLM matching (PydanticAI)
│   ├── catalog/service.py      # Embedding search, pricing, ingestion
│   ├── catalog/provider.py     # Sysco CSV parser + CatalogProvider interface
│   ├── db/models.py            # SQLAlchemy ORM (Job, WorkItem, CatalogItem, IngredientCache)
│   ├── db/engine.py            # Async session factory
│   ├── config.py               # Environment-based settings
│   └── events.py               # Pub/sub EventBus for SSE
├── frontend/
│   ├── src/views/              # Submit, Kitchen, Pass views
│   ├── src/api.ts              # React Query hooks
│   ├── src/schemas.ts          # Zod request/response schemas
│   ├── Dockerfile              # Multi-stage Node → nginx build
│   └── nginx.conf              # SPA routing + API proxy + SSE support
├── data/
│   ├── sysco_catalog.csv       # Sysco price catalog (~565 items)
│   ├── menu_spec.json          # Event menu specification (32 items)
│   └── quote_schema.json       # Output schema for structured quotes
├── alembic/                    # Database migrations (pgvector, catalog schema)
├── tests/                      # pytest suite + curl integration tests
├── Dockerfile                  # API image (Python 3.12, uv)
├── docker-compose.yml          # Postgres + API + Frontend
├── entrypoint.sh               # Runs migrations → starts uvicorn
└── pyproject.toml              # Python dependencies (uv)
```

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.12 |
| Web Framework | FastAPI + uvicorn |
| LLM Integration | PydanticAI (structured output, tool use) |
| Embeddings | OpenAI text-embedding-3-small (1536d) |
| Recipe Research | Exa API (semantic search) |
| Database | PostgreSQL 16 + pgvector |
| ORM | SQLAlchemy (async) + Alembic |
| Frontend | React 19, TypeScript, Tailwind CSS 4, Vite |
| Container | Docker Compose (postgres + api + frontend) |
| Package Manager | uv (Python), pnpm (frontend) |
