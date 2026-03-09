# Yes Chef — Architecture Plan

## System Overview

```
Client (React SPA)
  ↕ HTTP/SSE
FastAPI (api container, :8000)
  ├── POST /quotes → Orchestrator.submit_quote() → DB
  │                   asyncio.create_task(process_quote)
  ├── GET /quotes/{id}/stream → EventBus.subscribe() → SSE
  └── GET /quotes/{id}/result → DB query → assembled quote
  
Orchestrator
  ├── asyncio.Semaphore(3) bounds concurrent items
  ├── Per item:
  │   ├── Decomposition Agent (PydanticAI)
  │   │   ├── Exa API (recipe retrieval)
  │   │   └── LLM (ingredient extraction)
  │   └── Resolution Agent (PydanticAI)
  │       ├── CatalogService.search() → pgvector HNSW
  │       ├── CatalogService.get_price() → catalog_items table
  │       ├── IngredientCache (fast path)
  │       └── LLM (matching + reasoning)
  └── EventBus.publish() at each checkpoint

PostgreSQL 16 + pgvector
  ├── quotes table
  ├── menu_items table (with step_data JSON checkpoints)
  ├── ingredient_cache table
  └── catalog_items table (with HNSW vector index)
```

## Components Affected (by layer)

### Data Layer
- **Quote model** — represents a catering event with menu spec
- **MenuItem model** — individual dish being processed through the pipeline
- **IngredientCache** — cross-quote ingredient→catalog mappings
- **CatalogItem** — supplier product catalog with vector embeddings
- **Alembic migrations** — schema versioning (5 migrations total)

### Processing Layer
- **Orchestrator** — coordinates the two-stage pipeline with semaphore-bounded concurrency
- **Decomposition engine** — Exa retrieval + PydanticAI agent for ingredient extraction
- **Resolution engine** — cache lookup + PydanticAI agent with catalog search/price tools
- **CatalogService** — vector search + price lookup against pgvector

### API Layer
- **FastAPI app** — 6 endpoints (health, list, create, status, result, stream)
- **Pydantic models** — request/response validation (QuoteSubmitRequest, QuoteStatusResponse, QuoteSummary)
- **SSE streaming** — EventSource-compatible server-sent events

### Event Layer
- **EventBus** — in-memory pub/sub with asyncio.Queue per subscriber
- **SSEEvent** — typed event payloads (item_step_change, item_completed, item_failed, quote_completed)

### Frontend Layer
- **DashboardView** — quote list with status indicators
- **SubmitView** — quote creation form with JSON menu spec
- **KitchenView** — real-time progress via SSE + polling fallback
- **PassView** — final quote display with expandable ingredient tables
- **React Query hooks** — useQuotes, useSubmitQuote, useQuoteStatus, useQuoteResult
- **Zod schemas** — runtime validation of API responses

### Infrastructure
- **Docker Compose** — postgres (pgvector), api (FastAPI/uvicorn), frontend (nginx)
- **nginx** — reverse proxy, SSE support (proxy_buffering off)
- **Vite dev proxy** — /api → localhost:8000 for local development

## Sequence of Operations

### Quote Creation Flow
1. User fills form on `/new`, submits
2. POST /quotes → creates Quote + MenuItems in single transaction
3. Returns `{quote_id, status: 'pending'}` immediately
4. `asyncio.create_task(_run_processing(orch, quote_id))` — fire and forget
5. Frontend redirects to `/kitchen/:quoteId`
6. Frontend opens EventSource to `/quotes/{id}/stream`

### Processing Flow (per quote)
1. Orchestrator loads all MenuItems for quote
2. Separates completed (skip) from pending (process)
3. Creates semaphore-bounded tasks: `async with sem: await _process_item(mi)`
4. `asyncio.gather(*tasks, return_exceptions=True)`
5. Per item:
   a. Check status — if 'decomposed', skip to resolution
   b. Decompose: fetch recipe (Exa) → LLM extract ingredients → checkpoint
   c. Resolve: for each ingredient, cache check → on miss, LLM agent → checkpoint
   d. Publish SSE events at each stage
6. Assemble final quote from all completed items
7. Mark quote status = 'completed' or 'completed_with_errors'
8. Publish `quote_completed` event

### SSE Event Flow
1. Client connects: GET /quotes/{id}/stream
2. Server subscribes asyncio.Queue to EventBus for that quote_id
3. Yields `connected` event immediately
4. Loop: await queue.get() → yield SSE-formatted event
5. On `quote_completed`: break loop, close connection
6. On client disconnect: unsubscribe queue

## Risk Areas
- **LLM API reliability** — rate limits, timeouts, model availability affect processing
- **Checkpoint integrity** — step_data JSON must be valid for resume to work; corruption = re-process
- **SSE connection lifecycle** — browser reconnection, nginx proxy buffering, timeout management
- **Catalog staleness** — embeddings and prices can drift from real supplier data
- **Single server** — in-memory EventBus, in-process semaphore don't scale horizontally

## Dependencies Map
- Quote creation is independent (synchronous DB write)
- Processing depends on: LLM API (OpenRouter), Exa API, PostgreSQL
- SSE depends on: EventBus (in-memory), active processing task
- Frontend depends on: API availability, SSE for real-time updates
- Catalog search depends on: ingested embeddings in catalog_items table
