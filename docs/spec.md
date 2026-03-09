# YES CHEF — System Specification

> **This document describes the system as it exists today.** It is factual, not aspirational. For future improvements, see [What Could Be Improved](#what-could-be-improved).

> **For architecture diagrams and a visual walkthrough of design decisions, see [Key Concepts](concepts.md).**

---

## Table of Contents

1. [What the System Does](#what-the-system-does)
2. [Architecture Overview](#architecture-overview)
3. [The Pipeline](#the-pipeline)
4. [Concurrency Model](#concurrency-model)
5. [Checkpoint and Resumability](#checkpoint-and-resumability)
6. [SSE Event System](#sse-event-system)
7. [Ingredient Cache](#ingredient-cache)
8. [Catalog Layer](#catalog-layer)
9. [Data Models](#data-models)
10. [API Endpoints](#api-endpoints)
11. [Frontend](#frontend)
12. [Design Decisions and Rationale](#design-decisions-and-rationale)
13. [What Could Be Improved](#what-could-be-improved)

---

## What the System Does

Yes Chef is an AI-powered catering quote estimator. A user submits a menu specification (event name, date, venue, guest count, menu items by category). The system decomposes each menu item into purchasable ingredients using an LLM + recipe retrieval, then resolves each ingredient against a supplier catalog (Sysco) using semantic vector search + LLM reasoning. The result is a fully-priced quote with per-ingredient costs, catalog item matches, and source confidence levels.

---

## Architecture Overview

| Layer | Technology |
|---|---|
| **API** | FastAPI (Python 3.12, async) on port 8000 |
| **Database** | PostgreSQL 16 + pgvector extension |
| **Frontend** | React 19 + TypeScript + Vite + Tailwind CSS 4 + shadcn/ui |
| **LLM** | PydanticAI agents (OpenAI models via OpenRouter) |
| **Recipe retrieval** | Exa API (grounds decomposition with real recipes) |
| **Catalog** | Sysco product data with OpenAI embeddings (text-embedding-3-small, 1536 dims) |
| **Infrastructure** | Docker Compose (postgres, api, frontend) |

---

## The Pipeline

```
POST /quotes
    │
    ├─► Create Quote + MenuItems in DB
    ├─► Return {quote_id, status} immediately
    └─► Fire-and-forget: orchestrator.process_quote()
            │
            └─► For each MenuItem (bounded by asyncio.Semaphore(3)):
                    │
                    ├─► [DECOMPOSE]
                    │       ├─ Fetch recipe via Exa API
                    │       ├─ LLM extracts structured ingredients
                    │       └─ Checkpoint to DB: status=decomposed, ingredients in step_data
                    │
                    └─► [RESOLVE] (for each ingredient)
                            ├─ Check IngredientCache → cache hit: use cached match, skip LLM
                            ├─ Cache miss: LLM agent searches catalog via pgvector
                            │       ├─ Embeds ingredient query
                            │       ├─ pgvector HNSW cosine-similarity search
                            │       ├─ LLM picks best match, gets price, classifies source
                            │       └─ Write to IngredientCache
                            └─ Checkpoint to DB: status=completed, matches in step_data
```

**Result assembly:** `GET /quotes/{id}/result` joins Quote, MenuItems, and step_data to build the full priced quote with line items.

**Real-time updates:** At each checkpoint, the orchestrator publishes an SSEEvent to the in-memory EventBus, which fans out to all connected SSE subscribers for that quote.

---

## Concurrency Model

The orchestrator uses `asyncio.Semaphore(max_concurrent=3)` to bound concurrent menu item processing.

**Why bounding is necessary:**

- Each item requires 2+ LLM API calls (decompose + resolve per ingredient)
- LLM APIs have rate limits and latency (~1–5s per call)
- Without bounding, a 20-item menu would fire 40+ concurrent LLM calls, triggering rate-limit errors
- The semaphore ensures at most 3 items process simultaneously

**How it works:**

- Items are independent — no shared state between them, so no locks needed beyond the semaphore
- `asyncio.gather(*tasks, return_exceptions=True)` collects all results
- Failed items do not block others — each task catches its own exceptions
- Items execute as async tasks, yielding at every `await` (network I/O), so the event loop is never blocked

---

## Checkpoint and Resumability

Each `MenuItem` has a `status` field tracking its position in the pipeline:

```
pending → decomposing → decomposed → resolving → completed
                                                 ↘ failed (at any point)
```

At each stage transition, results are persisted to the `step_data` JSON column on the `MenuItem` row.

**Resume behavior** (if processing is interrupted by a server restart or timeout):

| Status at interruption | Behavior on resume |
|---|---|
| `completed` | Skipped entirely — no work repeated |
| `decomposed` | Skips decomposition, resumes at resolution |
| `pending` / `decomposing` | Restarts from decomposition |
| `failed` | Treated as a terminal state — not retried automatically |

This prevents expensive LLM calls from being re-run after transient failures.

---

## SSE Event System

**Architecture:** In-memory only. No Redis, no persistence.

- `EventBus` maintains `dict[str, list[asyncio.Queue]]` — quote_id → subscriber queues
- When the orchestrator checkpoints an item, it publishes an `SSEEvent` to the bus
- The bus fans out the event to all queues subscribed to that quote_id

**SSE endpoint:** `GET /quotes/{id}/stream`

- Subscribes a new `asyncio.Queue` to the bus for the given quote_id
- Yields events as `text/event-stream` (HTTP chunked, `Content-Type: text/event-stream`)
- Cleans up the queue subscription on client disconnect

**Event types:**

| Event type | When emitted |
|---|---|
| `connected` | Immediately on subscribe |
| `item_step_change` | When a MenuItem transitions between pipeline stages |
| `item_completed` | When a MenuItem reaches `completed` status |
| `item_failed` | When a MenuItem reaches `failed` status |
| `quote_completed` | When all MenuItems are done (any terminal status) |

**Trade-off:** Events are lost on server restart. This is acceptable for real-time UI progress; it is not suitable for audit logging.

---

## Ingredient Cache

**Table:** `IngredientCache`

**Purpose:** Cross-quote learning. Once "butter" is resolved to Sysco item X, all future quotes reuse that mapping without an LLM call.

**Lookup key:** `ingredient_name` (unique constraint)

**Cache entry:** `(source_item_id, source, provider)`

**Invalidation:** If the price lookup for a cached `source_item_id` fails, the cache entry is invalidated (stale catalog data assumed). The next resolution for that ingredient will go through the full LLM path.

**Fast path:** A cache hit bypasses the embed → vector-search → LLM-reason cycle entirely, reducing both latency and LLM cost for common ingredients.

---

## Catalog Layer

**Table:** `CatalogItem` — stores Sysco products with precomputed embeddings.

**Embedding model:** OpenAI `text-embedding-3-small`, 1536 dimensions.

**Index:** HNSW (pgvector) on the embedding column, cosine distance metric. Provides fast approximate nearest-neighbor search (~10ms for the current catalog size of ~5K items).

**Key operations:**

| Method | What it does |
|---|---|
| `CatalogService.search(query)` | Embeds `query`, runs pgvector cosine-similarity search, returns top candidates |
| `CatalogService.get_price(source_item_id)` | Returns `cost_per_case` + `unit_of_measure` for a catalog item |

**Data ingestion:** Sysco CSV is loaded into `CatalogItem` on first startup if the table is empty. Embeddings are generated at ingest time.

---

## Data Models

### Quote

| Column | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | |
| `event` | text | Event name |
| `date` | date | Event date |
| `venue` | text | Venue name or address |
| `guest_count_estimate` | integer | |
| `notes` | text | Optional freeform notes |
| `status` | text | `pending` / `processing` / `completed` / `failed` |
| `menu_spec` | JSONB | Raw submitted menu spec |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

### MenuItem

| Column | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | |
| `quote_id` | UUID (FK → Quote) | |
| `item_name` | text | e.g. "Lobster Bisque" |
| `category` | text | e.g. "Soups", "Entrées" |
| `status` | text | Pipeline stage (see [Checkpoint and Resumability](#checkpoint-and-resumability)) |
| `step_data` | JSONB | Checkpoint store: ingredients after decompose, matches after resolve |
| `error` | text | Error message if `status=failed` |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

### IngredientCache

| Column | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | |
| `ingredient_name` | text (UNIQUE) | Lookup key |
| `source_item_id` | text | Catalog item ID |
| `source` | text | Match source classification |
| `provider` | text | e.g. "sysco" |

### CatalogItem

| Column | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | |
| `source_item_id` | text | Vendor's own product ID |
| `description` | text | Product description |
| `provider` | text | e.g. "sysco" |
| `embedding` | vector(1536) | OpenAI text-embedding-3-small |
| `unit_of_measure` | text | e.g. "20/8 OZ", "CS" |
| `cost_per_case` | numeric | |
| `category` | text | Product category |
| `brand` | text | |
| `is_active` | boolean | |

---

## API Endpoints

| Method | Path | Description | Notes |
|---|---|---|---|
| `GET` | `/health` | Health check | Returns `{status: "ok"}` |
| `GET` | `/quotes` | List all quotes | Returns `QuoteSummary[]`, newest first |
| `POST` | `/quotes` | Create quote + start processing | Returns `{quote_id, status}` immediately; processing runs in background |
| `GET` | `/quotes/{id}` | Status snapshot | Returns `QuoteStatusResponse` with per-item progress |
| `GET` | `/quotes/{id}/result` | Full priced quote | Returns line items with costs; `409 Conflict` if not yet `completed` |
| `GET` | `/quotes/{id}/stream` | SSE event stream | `text/event-stream`; real-time progress events |

---

## Frontend

**Tech stack:** React 19 + TypeScript + Vite + Tailwind CSS 4 + shadcn/ui + Framer Motion

**State management:** React Query for server state, local `useState` for UI state, `EventSource` for SSE.

**Design language:** Dark luxury kitchen aesthetic.

### Routes

| Route | View | Description |
|---|---|---|
| `/` | Dashboard | Lists all quotes with status badges; click to navigate to Kitchen or Pass |
| `/new` | Submit | Form: event details + JSON menu spec; redirects to Kitchen on submit |
| `/kitchen/:quoteId` | Kitchen | Real-time progress via SSE; ticket cards per menu item grouped by station |
| `/pass/:quoteId` | Pass | Final quote display with expandable line items and ingredient cost tables |

### Key implementation notes

- SSE connection is managed via `useRef` + manual cleanup on navigation (no framework abstraction)
- React Query handles polling fallback and cache invalidation on quote state changes
- Motion animations used for ticket card transitions in Kitchen view

---

## Design Decisions and Rationale

### Why fire-and-forget POST?

Quote processing takes 30–120s depending on menu size. Blocking the HTTP request would timeout most clients and proxies. Instead, `POST /quotes` returns immediately with `quote_id`, and the client connects via SSE or polls `GET /quotes/{id}` for updates.

### Why `asyncio.Semaphore` instead of a task queue (Celery, Temporal, etc.)?

This is a prototype. An in-process semaphore is the simplest bounded-concurrency primitive available. It works here because:

- Single server instance (no horizontal scaling needed yet)
- Items are network-I/O-bound (waiting on LLM APIs), not CPU-bound
- No need for retry infrastructure, dead-letter queues, or distributed coordination at this scale

For production, migrating to Temporal.io or a proper task queue would add durability and retry semantics.

### Why checkpoint to DB instead of keeping state in memory?

LLM calls are expensive (latency + cost). If the server crashes mid-processing, checkpoints prevent re-running completed work. The `step_data` JSONB column stores intermediate results: ingredient lists after decompose, catalog matches after resolve.

### Why in-memory EventBus instead of Redis pub/sub?

Single server, prototype scope. Redis would add infrastructure complexity (another service, connection management, serialization) for no benefit at this scale. The trade-off — events lost on restart — is acceptable for a real-time progress UI but would not be acceptable for audit logging.

### Why pgvector HNSW instead of a dedicated vector database?

PostgreSQL already handles all relational data. pgvector keeps everything in one database, eliminating an entire infrastructure component. HNSW provides good recall at ~10ms search latency for the current catalog size (~5K items). A dedicated vector database (Pinecone, Weaviate, Qdrant) would only offer meaningful advantages at 100K+ items or with advanced filtering requirements.

### Why two LLM stages (decompose → resolve) instead of one?

Separation of concerns:

- **Decomposition** is creative — interpreting a dish name into a list of purchasable ingredients. It benefits from recipe grounding (Exa provides real professional recipes as context, preventing circular outputs like "hollandaise sauce requires hollandaise sauce").
- **Resolution** is analytical — matching a named ingredient against a real catalog. It benefits from structured tool use (embed → search → reason → pick).

Different prompts, different tool sets, different failure modes, different checkpoints. A single mega-prompt would be harder to debug, tune, and resume independently.

### Why Exa for recipe retrieval?

Grounding. LLMs hallucinate ingredient lists without context. Exa fetches real professional recipes at decompose time, providing the LLM with accurate source material. Without it, decomposition accuracy drops significantly for complex or regional dishes.

---

## What Could Be Improved

These are known gaps and limitations in the current implementation. They are not bugs — they are deliberate scope decisions for the prototype.

### Reliability

- **Per-ingredient retry with exponential backoff.** Currently, any error in resolving a single ingredient fails the entire MenuItem. A fine-grained retry would allow the others to succeed.
- **Dead letter queue.** Persistently failing items have no recovery path beyond manual intervention.
- **Structured error classification.** Transient failures (rate limit, timeout) should be retried; permanent failures (bad catalog data, malformed LLM output) should not. Currently both result in `status=failed`.
- **Pre-flight health checks.** No check for LLM API availability before starting processing; failures surface mid-pipeline.

### Cost accuracy

- **UOM parsing engine.** Unit-of-measure strings like `"20/8 OZ"` (20 units of 8oz each) are currently interpreted by the LLM during resolution, which is fragile. A deterministic parser would be more reliable.
- **Quantity normalization.** Ingredient quantities like "a pinch of salt" are passed through without conversion to measurable amounts.
- **Multi-provider support.** Only Sysco is currently supported. US Foods, Restaurant Depot, and other distributors would increase price coverage.
- **Historical price tracking.** Catalog prices change; there is no mechanism to track price history or detect stale data (beyond cache invalidation on failed lookups).

### Observability

- **Structured logging with correlation IDs.** `quote_id` is not threaded through log statements, making it hard to trace a single quote's processing across log lines.
- **LLM token usage and cost tracking.** No per-quote accounting of tokens consumed or dollars spent.
- **Agent reasoning traces.** Resolution decisions (why the LLM picked catalog item X over Y) are not stored, making debugging difficult.
- **Metrics.** No instrumentation for processing time per stage, cache hit rate, or error rate by category.

### Scale

- **Migrate to Temporal.io workflows.** The in-process semaphore provides no durability, distributed coordination, or retry policies. Temporal would provide all three.
- **Embedding refresh pipeline.** Catalog embeddings are generated at ingest and never refreshed. If product descriptions change, embeddings go stale.
- **Connection pooling.** Concurrent quote processing can exhaust the PostgreSQL connection pool under load.
- **Pagination on `GET /quotes`.** The endpoint returns all quotes unbounded; it will degrade as quote volume grows.

### Frontend

- **Error boundaries.** No graceful crash recovery for component-level errors.
- **Optimistic updates.** The dashboard does not reflect a newly created quote until the next refetch.
- **Mobile layout.** The Kitchen and Pass views are not optimized for small screens.
- **Accessibility.** ARIA labels and keyboard navigation are incomplete.
- **SSE cleanup.** The `EventSource` connection is managed manually via `useRef`; there is no framework-level cleanup guarantee on fast navigation.
