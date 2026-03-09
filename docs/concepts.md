# Yes Chef — Key Concepts

> Deep-dive into the architectural decisions, concurrency model, data flow, and system design.

---

## System Architecture

```
┌───────────────────────────────────────────────────────────────────────┐
│                           FRONTEND                                  │
│  React 19 + TypeScript + Tailwind CSS 4 + shadcn/ui + motion        │
│                                                                     │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐  │
│  │ Dashboard  │ │  Submit    │ │  Kitchen   │ │   Pass     │  │
│  │    /       │ │  /new      │ │ /kitchen/* │ │  /pass/*   │  │
│  └────────────┘ └────────────┘ └────────────┘ └────────────┘  │
│       │              │            │  EventSource    │            │
└───────┼──────────────┼────────────┼───────────────┼────────────┘
        │              │            │               │
    GET /quotes   POST /quotes  GET /stream   GET /result
        │              │            │               │
┌───────┴──────────────┴────────────┴───────────────┴────────────┐
│                        FastAPI (:8000)                              │
│  Pydantic models • SSE streaming • Background tasks                  │
└──────────────────┬───────────────────────┬─────────────────────┘
                   │                       │
          submit_quote()            process_quote()
                   │                       │
┌──────────────────┴───────────────────────┴─────────────────────┐
│                      ORCHESTRATOR                                   │
│                                                                     │
│  asyncio.Semaphore(3)                                               │
│  ┌───────────────────────┐  ┌─────────────────────────────────┐  │
│  │  Decomposition Agent  │  │  Resolution Agent              │  │
│  │                       │  │                                 │  │
│  │  Exa API (recipes)    │  │  IngredientCache (fast path)   │  │
│  │  LLM (extraction)     │  │  pgvector search (catalog)     │  │
│  │  PydanticAI agent     │  │  LLM (matching + reasoning)    │  │
│  └───────────────────────┘  │  PydanticAI agent + tools      │  │
│                            └─────────────────────────────────┘  │
│                                                                     │
│  EventBus.publish() ─────────► asyncio.Queue ───► SSE stream   │
└──────────────────────────────────────┬────────────────────────────┘
                                       │
┌──────────────────────────────────────┴────────────────────────────┐
│                  PostgreSQL 16 + pgvector                           │
│                                                                     │
│  quotes • menu_items • ingredient_cache • catalog_items (HNSW)       │
└───────────────────────────────────────────────────────────────────────┘
```

---

## Database Schema (ER Diagram)

```
┌────────────────────────────────┐       ┌────────────────────────────────┐
│           quotes              │       │          menu_items            │
├────────────────────────────────┤       ├────────────────────────────────┤
│ id               UUID    PK  │◄─────┤ id               UUID    PK  │
│ event            VARCHAR     │  1:N  │ quote_id         UUID    FK  │
│ date             VARCHAR?    │       │ item_name        VARCHAR     │
│ venue            VARCHAR?    │       │ category         VARCHAR     │
│ guest_count_est  INTEGER?    │       │ status           VARCHAR     │
│ notes            VARCHAR?    │       │ step_data        JSON?       │
│ status           VARCHAR     │       │ error            VARCHAR?    │
│ menu_spec        JSON?       │       │ created_at       TIMESTAMPTZ │
│ created_at       TIMESTAMPTZ │       │ updated_at       TIMESTAMPTZ?│
│ updated_at       TIMESTAMPTZ?│       └────────────────────────────────┘
└────────────────────────────────┘
         Indexes: status                Indexes: (quote_id, status)

┌────────────────────────────────┐       ┌────────────────────────────────┐
│       ingredient_cache        │       │         catalog_items          │
├────────────────────────────────┤       ├────────────────────────────────┤
│ id               UUID    PK  │       │ id               UUID    PK  │
│ ingredient_name  VARCHAR UQ  │       │ source_item_id   VARCHAR     │
│ source_item_id   VARCHAR?    │       │ description      VARCHAR     │
│ source           VARCHAR     │       │ provider         VARCHAR     │
│ provider         VARCHAR?    │       │ embedding        VECTOR(1536)│
│ created_at       TIMESTAMPTZ │       │ unit_of_measure  VARCHAR     │
│ updated_at       TIMESTAMPTZ?│       │ cost_per_case    FLOAT       │
└────────────────────────────────┘       │ category         VARCHAR?    │
         Index: ingredient_name (UQ)    │ brand            VARCHAR?    │
                                        │ source_metadata  JSONB?      │
                                        │ is_active        BOOLEAN     │
                                        │ created_at       TIMESTAMPTZ │
                                        └────────────────────────────────┘
                                        Indexes: (provider, source_item_id) UQ
                                                 embedding HNSW
                                                 is_active (partial)
```

**Relationships:**
- `quotes` 1:N `menu_items` (via `quote_id` FK, cascade delete)
- `ingredient_cache` is standalone (cross-quote lookup table)
- `catalog_items` is standalone (supplier product catalog)
- No FK between ingredient_cache and catalog_items (cache stores source_item_id as string reference)

---

## Semaphore-Bounded Concurrency

### The Problem

A catering menu might have 20+ items. Each item needs 2+ LLM API calls (decompose + resolve per ingredient). Firing all simultaneously would:
- Hit LLM rate limits (OpenRouter/OpenAI cap concurrent requests)
- Overwhelm the event loop with 40+ pending HTTP calls
- Create unpredictable latency spikes

### The Solution

```python
semaphore = asyncio.Semaphore(max_concurrent)  # default: 3

tasks = [
    _process_with_semaphore(semaphore, item, quote_id)
    for item in pending_items
]
results = await asyncio.gather(*tasks, return_exceptions=True)
```

```
Time ──►

Slot 1: [Item A: decompose -----> resolve ------->] [Item D: decompose --> resolve -->]
Slot 2: [Item B: decompose --> resolve -->]          [Item E: decompose --------> ...]
Slot 3: [Item C: decompose -------> resolve --->]    [Item F: ...]
         │                                          │
         Semaphore(3): max 3 concurrent              Items queue up, enter as slots free
```

### Why not Celery/Redis Queue?

- **Prototype scope**: Single server, no horizontal scaling needed
- **CPU-light work**: Items are I/O-bound (waiting on LLM APIs), not CPU-bound
- **Simplicity**: asyncio.Semaphore is 1 line vs Celery infrastructure (broker, worker, result backend)
- **Latency**: In-process → zero serialization overhead. Celery would add ~10ms per task dispatch
- **Trade-off**: No retry infrastructure, no dead letter queue, no multi-server distribution

### What production would look like

Migrate to **Temporal.io** workflows:
- Each menu item becomes a workflow activity
- Built-in retry with exponential backoff
- Durable execution (survives server restarts)
- Distributed across multiple workers
- Observable (Temporal UI shows workflow state)

---

## Two-Stage Pipeline

### Why two stages instead of one?

```
Menu Item ─► DECOMPOSE ─► Ingredients[] ─► RESOLVE ─► CatalogMatches[] + Cost
```

**Decomposition** is creative:
- Interprets "Eggs Benedict" into butter, eggs, English muffins, Canadian bacon, lemon juice, cayenne
- Benefits from recipe grounding (Exa API provides real recipes as context)
- Failure mode: hallucinated ingredients, missing components
- LLM persona: professional chef breaking down a dish

**Resolution** is analytical:
- Matches "unsalted butter" to Sysco item #1234567
- Benefits from structured tool use (search catalog, get price, update cache)
- Failure mode: wrong catalog match, incorrect UOM interpretation
- LLM persona: procurement specialist with catalog tools

Separating them means:
- Different prompts optimized for different tasks
- Independent checkpointing (decompose result saved before resolve starts)
- Different failure handling (bad decomposition → retry decompose; bad resolution → retry resolve only)
- Easier debugging (which stage failed? what did it produce?)

### Decomposition Flow

```
item_name + description
         │
         ▼
  Exa API: fetch_recipe(dish_name)
         │
         ▼ (recipe text or fallback)
  PydanticAI Agent:
    System: "You are a professional chef. Decompose to raw purchasable ingredients."
    Input: recipe context + item description
    Output: DecompositionResult { ingredients: [{name, quantity}] }
         │
         ▼
  Checkpoint: MenuItem.status = 'decomposed'
             MenuItem.step_data.ingredients = [...]
```

### Resolution Flow (per ingredient)

```
ingredient_name
         │
         ▼
  Cache lookup: IngredientCache.get(ingredient_name)
         │
    ┌────┴────┐
    │ hit?     │
    └───┬────┘
   yes │    no
    │      │
    ▼      ▼
  Verify  PydanticAI Agent:
  price     Tools: search_catalog(), get_price(), update_cache()
    │       Output: IngredientMatch { source_item_id, source, unit_cost }
    │       │
    ▼       ▼
  Return  Upsert cache + return
```

---

## Checkpoint and Resumability

### MenuItem Status State Machine

```
                    ┌─────────────┐
                    │   pending   │
                    └──────┬──────┘
                           │
                           ▼
                    ┌─────────────┐
                    │ decomposing │ ───► [failed]
                    └──────┬──────┘
                           │
                           ▼
                    ┌─────────────┐
                    │  decomposed │  ← checkpoint: ingredients saved to step_data
                    └──────┬──────┘
                           │
                           ▼
                    ┌─────────────┐
                    │  resolving  │ ───► [failed]
                    └──────┬──────┘
                           │
                           ▼
                    ┌─────────────┐
                    │  completed  │  ← checkpoint: matches + cost saved to step_data
                    └─────────────┘
```

### Resume behavior

| Status when interrupted | On resume |
|------------------------|----------|
| `pending` | Restart from decomposition |
| `decomposing` | Restart from decomposition (partial work lost) |
| `decomposed` | Skip decomposition, resume at resolution (ingredients loaded from step_data) |
| `resolving` | Restart resolution (partial matches lost) |
| `completed` | Skip entirely |
| `failed` | Skip entirely (requires manual retry) |

### Startup recovery

On server start, the FastAPI lifespan hook queries for all `Quote` rows with `status='processing'` — quotes whose background task was cut short by a crash or restart — and re-fires `process_quote()` for each one via `asyncio.create_task()`. Recovery is non-blocking: the server reports healthy immediately and stalled quotes resume behind the scenes using the standard checkpoint logic above. DB errors during the recovery scan are caught and logged so a corrupted quote never blocks the server from starting.

Clients already connected to an SSE stream will not receive a replay of events emitted before the restart, but they will receive fresh events as the resumed quote progresses through each checkpoint.

### Why this matters

LLM calls cost money and time:
- Decomposition: ~$0.01–0.05 per item (Exa query + LLM call)
- Resolution: ~$0.02–0.10 per item (multiple LLM calls for ingredient matching)
- A 15-item menu: ~$0.50–2.00 total
- Without checkpoints, a crash at item #14 wastes $1.50+ and 2+ minutes

---

## SSE Event System

### Architecture

```
Orchestrator                    EventBus                    SSE Endpoint
     │                            │                            │
     │  publish(quote_id, event)  │                            │
     │─────────────────────────►│                            │
     │                            │  queue.put(event)          │
     │                            │─────────────────────────►│
     │                            │                            │  yield SSE
     │                            │                            │───► Client
```

### Event types

| Event | When | Payload |
|-------|------|---------|
| `connected` | Client connects to stream | `{ quote_id }` |
| `item_step_change` | Item enters decomposing or resolving | `{ quote_id, item_name, status, step }` |
| `item_completed` | Item fully resolved with cost | `{ quote_id, item_name }` |
| `item_failed` | Item processing errored | `{ quote_id, item_name, error }` |
| `quote_completed` | All items done | `{ quote_id, status }` |

### Frontend handling

KitchenView uses a hybrid approach:
1. **Primary**: EventSource (SSE) for real-time updates
2. **Fallback**: React Query polling (3s interval) if SSE fails
3. **Initial load**: React Query fetches current state, SSE provides deltas
4. On `quote_completed` event: close SSE, navigate to PassView

### Trade-offs

- **In-memory**: Events lost on server restart (no replay/catch-up). Quotes that were processing at restart time are automatically resumed by the startup recovery hook; clients will receive fresh SSE events from the resumed checkpoint onwards, but will not see events emitted before the restart.
- **No backpressure**: Queue grows unbounded if client is slow
- **Single server**: EventBus doesn't work across multiple API instances
- **Good enough for**: Real-time UI feedback during active processing
- **Not good enough for**: Audit trails, guaranteed delivery, multi-server

---

## Ingredient Cache

### How it works

```
Resolve "unsalted butter"
         │
         ▼
  SELECT * FROM ingredient_cache WHERE ingredient_name = 'unsalted butter'
         │
    ┌────┴────┐
    │  found? │
    └──┬───┬─┘
      yes   no
       │    │
       ▼    ▼
    Verify  Run LLM agent
    price   (full resolution)
       │    │
       │    ▼
       │  INSERT/UPDATE ingredient_cache
       │    │
       ▼    ▼
     Return IngredientMatch
```

### Cache invalidation

- If cached `source_item_id` fails price lookup (item discontinued, catalog updated) → delete cache entry → fall through to LLM agent
- Cache entries for `not_available` (86'd) are kept — prevents repeated LLM calls for unobtainable ingredients
- No TTL — cache entries persist until invalidated

### Cross-quote learning

First quote with "unsalted butter" pays the LLM cost. All subsequent quotes get instant cache hits. Over time, the cache covers most common ingredients, dramatically reducing LLM calls and cost.

---

## Catalog and Vector Search

### How pgvector HNSW works here

```
Query: "unsalted butter"  ─►  embed(query)  ─►  1536-dim vector
                                                    │
                                                    ▼
                                    HNSW index scan on catalog_items
                                    ORDER BY embedding <=> query_vector
                                    LIMIT 10
                                                    │
                                                    ▼
                                    Top 10 nearest catalog items
                                    with similarity scores
```

- **Embedding model**: OpenAI text-embedding-3-small (1536 dimensions)
- **Index type**: HNSW (Hierarchical Navigable Small World) — approximate nearest neighbor
- **Index params**: m=16, ef_construction=64 (balanced recall vs build time)
- **Search latency**: ~5-10ms for ~565 items
- **Why not exact search?**: HNSW is O(log n) vs O(n) for exact; matters at scale even if catalog is small now

### Data ingestion

On first API startup:
1. Load Sysco CSV from `SYSCO_CSV_PATH`
2. For each product: embed description, store in catalog_items
3. HNSW index builds automatically
4. Subsequent startups skip ingestion (data persists in PostgreSQL)

---

## Application Flow (Sequence Diagram)

### Happy Path: Create and Complete a Quote

```
User            Frontend         API              Orchestrator       LLM/Exa        DB
 │                │               │                   │                │             │
 │  Fill form     │               │                   │                │             │
 │─────────────►│               │                   │                │             │
 │                │ POST /quotes  │                   │                │             │
 │                │─────────────►│ submit_quote()    │                │             │
 │                │               │─────────────────►│                │  INSERT     │
 │                │               │                   │────────────────│───────────►│
 │                │  {quote_id}   │                   │                │             │
 │                │◄─────────────│ create_task()     │                │             │
 │                │               │─────fire&forget──►│                │             │
 │  /kitchen/:id  │               │                   │                │             │
 │◄─────────────│               │                   │                │             │
 │                │ GET /stream   │                   │                │             │
 │                │─────────────►SSE connected      │                │             │
 │                │◄─────────────│                   │                │             │
 │                │               │                   │  decompose()   │             │
 │                │               │                   │──────────────►│             │
 │                │ item_step     │                   │  ingredients   │  checkpoint │
 │                │◄─────────────│                   │◄──────────────│───────────►│
 │                │               │                   │  resolve()     │             │
 │                │               │                   │──────────────►│             │
 │                │ item_complete │                   │  matches+cost  │  checkpoint │
 │                │◄─────────────│                   │◄──────────────│───────────►│
 │                │               │                   │                │             │
 │                │ quote_done    │                   │                │  UPDATE     │
 │                │◄─────────────│                   │────────────────│───────────►│
 │  /pass/:id     │               │                   │                │             │
 │◄─────────────│               │                   │                │             │
 │                │ GET /result   │                   │                │  SELECT     │
 │                │─────────────►│───────────────────────────────│───────────►│
 │  View quote    │  quote JSON   │                   │                │             │
 │◄─────────────│◄─────────────│                   │                │             │
```

---

## Domain Language

| Domain term | Code term | Meaning |
|-------------|-----------|---------|
| Quote | `Quote` model | A catering event being estimated |
| Menu item | `MenuItem` model | A dish on the menu (e.g. "Eggs Benedict") |
| Ingredient | `DecompositionResult.ingredients` | Raw purchasable component (e.g. "unsalted butter") |
| Catalog item | `CatalogItem` model | A supplier product with price and UOM |
| 86'd | `source: 'not_available'` | Kitchen slang: ingredient can't be sourced |
| The Pass | PassView | Final quote inspection (kitchen term for quality check station) |
| Kitchen | KitchenView | Live processing view (ticket rail metaphor) |
| Ticket | MenuItem card in UI | Visual representation of a menu item being processed |
