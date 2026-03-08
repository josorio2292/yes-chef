# Yes Chef — AI Catering Estimation Agent

> Phase 1 — Specify (SDD)

---

## Goal

A catering company submits a menu specification describing dishes by name and prose description. The system produces a fully-priced ingredient cost quote as structured output — breaking each dish into component ingredients, matching those ingredients against a supplier catalog, converting case-level pricing to per-serving costs, and handling missing catalog items gracefully. The system must survive interruption mid-run and resume from the last completed step without re-processing completed work or wasting API calls on already-resolved ingredients.

## Workflow Architecture

```mermaid
flowchart TB
    subgraph Input
        MS[Menu Specification JSON]
    end

    subgraph JobCreation["Job Creation (deterministic)"]
        PARSE[Parse menu spec] --> CREATE[Create job + work items in Postgres]
    end

    subgraph Orchestrator["Async Orchestrator"]
        SEM[Semaphore - concurrency limit 3-5]
        DISPATCH[Dispatch items concurrently]
    end

    subgraph PerItem["Per-Item Processing (2 steps)"]
        direction TB

        subgraph Step1["Step 1 — Decompose (AI: structured extraction)"]
            EXA["Exa: programmatic query\n'professional catering recipe {dish}'"] --> RECIPE[Retrieve recipe text]
            RECIPE --> EXTRACT["LLM: extract ingredients +\nquantities from recipe"]
            EXTRACT --> CP1[(Checkpoint → Postgres)]
        end

        subgraph Step2["Step 2 — Resolve (per ingredient)"]
            direction TB
            INGR[For each ingredient] --> CACHE{Cache hit?}
            CACHE -->|Yes| FAST["Fast path: get_price\n(deterministic, no LLM)"]
            CACHE -->|No| AGENT["Matching Agent\n(PydanticAI + tools)"]
            AGENT -->|search_catalog\nget_price\nupdate_cache| CS_LINK[Catalog Service]
            FAST --> MATCH[IngredientMatch result]
            AGENT --> MATCH
            MATCH --> NEXT{More?}
            NEXT -->|Yes| INGR
            NEXT -->|No| ROLLUP[Sum unit costs] --> CP2[(Checkpoint → Postgres)]
        end

        CP1 --> INGR
    end

    subgraph CatalogLayer["Catalog Layer"]
        CS["Catalog Service\n(unified API + embeddings)"]
        P1["Provider: Sysco CSV"]
        PN["Provider: future API"]
        CS --> P1
        CS --> PN
    end

    subgraph External["External Services"]
        OR[OpenRouter - LLM]
        EXAAPI[Exa API]
    end

    subgraph Persistence["Postgres"]
        JOBS[(Jobs)]
        ITEMS[(Work Items)]
        ICACHE[("Cache\n(global)")]
        EMB[(Embeddings)]
    end

    subgraph Output
        ASSEMBLE[Assemble quote] --> VALIDATE[Validate schema]
        VALIDATE --> QUOTE[Quote JSON]
    end

    subgraph Clients
        API[FastAPI] --> SSE[SSE Stream]
        REACT[React UI] -.-> API
    end

    MS --> PARSE
    CREATE --> DISPATCH
    SEM --> PerItem
    EXA -.-> EXAAPI
    EXTRACT -.-> OR
    AGENT -.-> OR
    CS_LINK -.-> CS
    FAST -.-> CS
    CS -.-> EMB
    CP1 -.-> ITEMS
    CP2 -.-> ITEMS
    CACHE -.-> ICACHE
    CP2 --> ASSEMBLE
    API --> DISPATCH
```

### AI vs Deterministic Boundaries

| Component | AI? | Guardrails |
|-----------|-----|------------|
| Job creation | No | JSON schema validation |
| Exa recipe search | No | Programmatic query template |
| Ingredient extraction | **Yes** (LLM) | Structured output (Pydantic, `min_length=1`), temperature 0, grounded in recipe text only |
| Quantity estimation | **Yes** (LLM) | Structured output, derived from recipe serving sizes |
| Cache-hit fast path | No | Orchestrator resolves in Python — zero LLM cost |
| Matching agent — search | **Yes** (embeddings) | Cosine similarity over catalog embeddings, top-5 candidates |
| Matching agent — evaluation | **Yes** (LLM) | Must select from provided candidates or declare not_available |
| Matching agent — pricing | No (tool) | LLM interprets UOM string + arithmetic |
| Matching agent — cache write | No (tool) | Postgres upsert, non-fatal on failure |
| Cost rollup | No | Arithmetic sum |
| Quote assembly | No | Schema validation |

### Grounding & Guardrail Strategy

**Principle: the LLM is a reasoning engine over retrieved data, never a source of facts.**

1. **Temperature 0** for all LLM calls.
2. **Structured output** (Pydantic models) on every LLM response — typed, validated, auto-retried on failure (default 3 retries).
3. **Input grounding** — decomposition LLM receives ONLY Exa-retrieved recipe text. Matching agent sees ONLY embedding-retrieved catalog candidates. Neither receives open-ended prompts.
4. **Output constraints** — decomposition extracts from recipe, decomposes compound preparations to base ingredients. Matching selects from candidates or declares `not_available`. Neither can fabricate data.
5. **Separation of concerns** — pricing (UOM parsing + arithmetic), cost rollup, and caching are deterministic Python code in agent tools. The LLM never does math.
6. **Persona enforcement** — system prompts define restricted personas: recipe analyst (decomposition), procurement specialist (matching). Both cite sources, never guess.
7. **Bounded agent loops** — matching agent has a maximum iteration limit to prevent runaway tool-calling.

## Behaviors

### Job Lifecycle

- **JobSubmission:**
  - Given the system is running and the catalog service is available,
  - When a caller submits a valid menu specification,
  - Then the system creates a job with a unique ID, one work item per menu item (each "pending"), and returns the job ID.

- **JobCompletion:**
  - Given a job has items being processed,
  - When the last item finishes (completed or failed),
  - Then the system assembles the final quote from successfully completed items and marks the job as "completed".

### Catalog Layer

- **CatalogProviderInterface:**
  - Given the system needs supplier data,
  - When the Catalog Service loads or queries a supplier,
  - Then it uses the Provider interface: `load_catalog()` returns all items (item_number, description, uom, cost_per_case), and `get_price(item_number)` returns the current price and UOM. Each provider is a simple data adapter. The prototype implements one provider (Sysco CSV). Adding a provider means implementing these two methods — no changes to the Catalog Service or agents.

- **CatalogServiceInterface:**
  - Given the system needs to search across all providers,
  - When any component queries the catalog,
  - Then it uses the Catalog Service — a unified API that sits above all providers:
    - `search(query: str) → list[CatalogCandidate]` — embeds the query, runs cosine similarity against all catalog embeddings, returns top-5 candidates ranked by score. Each candidate has: item_number, description, uom, provider, similarity_score.
    - `get_price(item_number: str, provider: str) → PriceResult` — delegates to the correct provider, returns cost_per_case and uom.
    - `embed_catalog()` — loads all providers, embeds all descriptions, stores in Postgres. Called once at startup.
  - The matching agent interacts ONLY with the Catalog Service. It never knows which provider data came from — the Catalog Service handles routing.

- **EmbeddingIndex:**
  - Given the Catalog Service has loaded items from all providers,
  - When the application starts,
  - Then it embeds every catalog item description using an embedding model (text-embedding-3-small). Embeddings are stored in Postgres. For the prototype (565 items), this is a single operation at startup — no incremental tracking needed. If the application restarts, embeddings are reloaded from Postgres; if the catalog changes, `embed_catalog()` re-embeds everything.

### Per-Item Processing

Each menu item moves through two steps with a checkpoint after each.

**Step 1 — Decompose:**

- **RecipeRetrieval:**
  - Given a menu item with a name and description,
  - When decomposition begins,
  - Then the system sends a programmatic query to Exa (e.g., "professional catering recipe {dish_name} ingredients per serving") and retrieves the recipe text. The query is deterministic. A crash during "decomposing" restarts both the Exa call and LLM extraction on resume (Exa cost is negligible).

- **IngredientExtraction:**
  - Given recipe text from Exa (or the dish description if Exa is unavailable),
  - When the LLM processes it,
  - Then it returns a structured list of base purchasable ingredients with per-serving quantities (Pydantic structured output, `min_length=1`). The LLM decomposes compound preparations (hollandaise → butter, egg yolks, lemon juice) to base ingredients and derives quantities from the recipe's serving count. An empty ingredient list triggers a validation retry.

- **DecompositionCheckpoint:**
  - Given extraction is complete,
  - Then the ingredient list is persisted to Postgres and the item advances to "decomposed". Interruption after this point does not repeat decomposition.

**Step 2 — Resolve:**

- **IngredientResolution:**
  - Given a menu item has a persisted ingredient list,
  - When resolve begins,
  - Then each ingredient goes through one of two paths:

- **CacheHitFastPath:**
  - Given an ingredient name,
  - When the orchestrator finds a valid cache entry (Postgres lookup, normalized key),
  - Then it resolves the ingredient in deterministic code — no LLM. It calls `catalog_service.get_price()` for the cached item and builds an `IngredientMatch` in Python using the cached cost data. If `get_price()` fails (item no longer exists), the cache entry is invalidated and the ingredient falls through to the matching agent.

- **MatchingAgentPath:**
  - Given an ingredient has no valid cache entry,
  - When the orchestrator invokes the matching agent (PydanticAI),
  - Then the agent runs with the ingredient name and serving quantity. The LLM decides the tool-call sequence:
    1. `search_catalog(query)` — gets top-5 candidates from embedding search via Catalog Service.
    2. **Evaluate** — LLM reasons about which candidate best matches. Considers semantic fit, quality tier, product category. This is the core AI judgment.
    3. `get_price(item_number, provider)` — tool fetches price, parses UOM, computes per-serving cost in Python.
    4. `update_cache(ingredient_name, item_number, source, provider)` — persists mapping via Postgres upsert. Non-fatal on failure.
    5. Returns `IngredientMatch` structured output.
  - If no candidate is acceptable: source `"not_available"`, null cost. The agent caches `not_available` mappings too to prevent redundant searches.

- **SourceClassification:**
  - **`"sysco_catalog"`** — exact or near-exact match. The catalog item IS the ingredient.
  - **`"estimated"`** — reasonable approximation. Same category, different quality/brand/spec.
  - **`"not_available"`** — no acceptable match. Specialty item, premium product, or non-food item.
  - The agent explains its classification in a reasoning field (logged, not in final quote).

- **CostRollup:**
  - Given all ingredients resolved,
  - Then sum non-null unit_costs into `ingredient_cost_per_unit`. `not_available` ingredients (null cost) don't contribute. The rollup is the minimum verifiable ingredient cost.

- **ResolveCheckpoint:**
  - Given all ingredients resolved and rollup complete,
  - Then the full line item is persisted to Postgres and the item advances to "completed". Not reprocessed under any circumstances.

### Ingredient Cache

- **Global Cache:**
  - The cache is stored in Postgres and shared across ALL jobs. Key: normalized ingredient name (lowercase, trimmed). Value: item_number, source, provider. No prices cached — prices are always fetched fresh. A mapping written during job 1 is available to job 2, eliminating redundant LLM calls for common ingredients.
  - Concurrent writes from parallel items are safe — Postgres upsert with temperature-0 LLM reasoning produces equivalent results regardless of write ordering.

### Persistence & Resumability

- **Checkpointing:**
  - Items progress: pending → decomposing → decomposed → resolving → completed | failed.
  - Checkpoint writes are atomic (single Postgres transaction). A crash during a checkpoint write leaves the item at its previous status.

- **Resumability:**
  - On restart:
    - **"completed"** → skipped.
    - **"decomposed"** → resumes at resolve using persisted ingredient list.
    - **"pending" / "decomposing"** → restarts decomposition.
    - **"resolving"** → restarts resolve. Individual ingredient results are lost (held in memory until final checkpoint), BUT the global cache preserves mappings written before interruption. On restart, these hit the zero-cost fast path. Only un-cached ingredients need fresh agent calls.

- **ItemIsolation:**
  - Each item runs independently. A failure on item N does not affect any other item.

### Concurrency

- **AsyncOrchestration:**
  - Items run concurrently with a configurable semaphore (default 3–5). Items communicate only through the persistent cache and checkpoint store.

### Observability

- **ProgressReporting:** Job status includes per-item step information.
- **RealTimeUpdates:** SSE events for step transitions, item completion, item failure, and job completion. Frontend consumes these via native `EventSource` API.
- **QuoteRetrieval:** Final quote as JSON conforming to `quote_schema.json`.

### Frontend Views

Three views mirror the kitchen workflow (design system: `.interface-design/system.md`):

- **Submit View:** Clean form for menu spec input (JSON upload or paste) + event details. Single CTA: "Start Quote."
- **Kitchen View:** Live progress — ticket cards representing menu items move through stations (Prep → Match → Done) as SSE events arrive. Live counters for total/in-progress/completed/failed.
- **The Pass View:** Final quote review — summary header + expandable line items with ingredient tables. Source badges (Catalog / Estimated / 86'd) on each ingredient. Export as JSON.

### Failure & Recovery

- **Retries:** Tool calls and API requests retry up to 3 times with backoff. PydanticAI auto-retries on structured output validation failure (default 3).

- **GracefulDegradation:** Exa unavailable → decomposition falls back to LLM-only extraction from dish description. Resolve step unaffected.

- **PartialIngredientFailure:** If some ingredients fail within an item, the item still completes with partial results. Failed ingredients get `not_available` + null cost. The item is NOT marked failed.

- **ItemLevelFailure:** If an entire step fails after retries, the item is marked "failed". Other items continue.

- **RateLimiting:** System pauses and waits. No items marked failed. If killed during the wait, standard resumability applies.

- **PartialQuote:** Job completes with failed items → quote contains only successful items. Status reflects failure count.

## Contracts

**Input — Menu Specification:**
- event, date, venue, guest_count_estimate, notes
- categories: map of category name → list of menu items
- Each item: name, description, dietary_notes (nullable), service_style (appetizers only)
- Categories: appetizers, main_plates, desserts, cocktails

**Catalog Provider Interface:**
- `load_catalog() → list[CatalogItem]` — each with: item_number (str), description (str), unit_of_measure (str), cost_per_case (float)
- `get_price(item_number: str) → PriceResult` — cost_per_case (float), unit_of_measure (str). Raises if item not found.

**Catalog Service Interface:**
- `search(query: str) → list[CatalogCandidate]` — each with: item_number (str), description (str), unit_of_measure (str), provider (str), similarity_score (float). Top-5 by cosine similarity.
- `get_price(item_number: str, provider: str) → PriceResult` — delegates to provider. Raises if item/provider not found.
- `embed_catalog() → None` — loads all providers, embeds all descriptions, stores in Postgres.
- `load_embeddings() → None` — loads previously-stored embeddings from Postgres without calling the embedding API. Startup calls `load_embeddings()` if embeddings exist; calls `embed_catalog()` only if the table is empty.

**IngredientMatch (Pydantic — agent output OR fast-path built):**
- name: str
- catalog_item: str | None (matched catalog description)
- sysco_item_number: str | None
- provider: str | None
- source: "sysco_catalog" | "estimated" | "not_available"
- unit_cost: float | None (per-serving cost)
- reasoning: str (logged, not in quote)

**Matching Agent Dependencies (RunContext):**
- catalog: CatalogService
- serving_quantity: str (e.g., "8 oz")

**Ingredient Cache (Postgres, global):**
- key: normalized ingredient name (lowercase, trimmed)
- value: item_number (str | None), source, provider (str)
- No prices. Entries invalidated when `get_price()` fails on a cached item.

**Checkpoint State:**
- job_id, item_name, category
- status: pending | decomposing | decomposed | resolving | completed | failed
- step_data: intermediate result for last completed step
- error: str | None

**Output — Quote:**
- quote_id, event, date, venue, generated_at (ISO 8601 string — to be added to quote_schema.json during implementation)
- line_items: list per menu item:
  - item_name, category
  - ingredients: list per ingredient: name, quantity, unit_cost, source, sysco_item_number
  - ingredient_cost_per_unit: float (sum of non-null unit_costs)

**Job Status:**
- job_id, status, total_items, completed_items, failed_items
- items: list of (item_name, step, status)

**Curl Test Case (YAML — `tests/curl/*.yml`):**
- name: str (human-readable test name)
- request: method (GET|POST), url (path only, base URL from env), headers (optional map), body (optional — inline object or file reference)
- expect: status (int), body (optional — partial match assertions, supports `any_string`, `any_number`, `any_uuid` matchers), headers (optional map)
- setup (optional): steps to run before the test (e.g., create a job first)
- depends_on (optional): name of another test whose response values are referenced via `${prev.field}` interpolation

**SSE Events:**
- item_step_change: job_id, item_name, status, step, timestamp
- item_completed: job_id, item_name, data, timestamp
- item_failed: job_id, item_name, error, timestamp
- job_completed: job_id, timestamp

## Constraints

### Language & Tooling
- Python (latest stable), uv, PydanticAI (latest), FastAPI (latest), pytest.
- SQLAlchemy (latest, async) + Alembic for persistence and migrations. asyncpg driver.
- Pydantic models for API/agent contracts; SQLAlchemy models for persistence. Explicit conversion between them.
- Two PydanticAI agents: decomposition (structured extraction) and matching (tools). Cache hits bypass agents entirely.
- Embedding: text-embedding-3-small via OpenRouter. Computed at startup, stored in Postgres.
- Frontend: React (latest). SSE consumed via native `EventSource` API (not Vercel AI SDK — custom SSE events don't fit AI SDK wire format). Design guided by the `interface-design` skill — domain-driven design system stored in `.interface-design/system.md` for consistency across views (job submission, progress tracking, quote display).
- Ruff for linting and formatting (replaces black, flake8, isort). PEP 8 as the style reference. Configured in `pyproject.toml`. Run `ruff check .` and `ruff format .` before every commit.
- CLI-first. TDD non-negotiable.
- Two test layers: (1) pytest unit/integration tests (TDD — write failing test first, then implement), (2) curl integration tests defined in YAML files (`tests/curl/*.yml`) for API endpoint verification. YAML files specify request method, URL, headers, body, and expected response (status code, body assertions). A test runner script executes them against the running API via curl.

### Local Development
- Docker Compose: Postgres + API service.

### AI & External Services
- LLM via OpenRouter (`openrouter:model-name`). Temperature 0.
- LLM used in two operations: (1) ingredient extraction from recipe, (2) catalog matching for cache-miss ingredients only.
- Exa API for recipe retrieval. Programmatic queries. Fallback to LLM-only on failure.
- Embedding: text-embedding-3-small. All catalog items embedded at startup.

### Data & Persistence
- Postgres (Render free tier / Docker local) for everything: jobs, checkpoints, cache, embeddings.
- Catalog: two-tier. Catalog Service (unified API + embeddings) → Provider (data adapter). Prototype: one provider (Sysco CSV). Adding a provider = implementing `load_catalog()` + `get_price()`.
- Cache: global, cross-job. No prices cached.

### Deployment
- Render: API (web service), React (static site), Postgres (managed). All free tier.
- Budget: $50 total.

### Processing
- Async Python + semaphore (default 3–5 concurrent items).
- Failed items don't block quote assembly.
- Output validated against `quote_schema.json`.

### Production Path (documented, not built)
- Temporal.io migration (each step = activity).
- pgvector for Postgres-native vector search.
- Per-ingredient checkpointing within resolve step.

## Error Cases

- **CatalogServiceUnavailable:** Resolve step fails. Decomposition continues.
- **ExaUnavailable:** Decomposition falls back to LLM-only. Resolve unaffected.
- **ItemFailure:** Item marked "failed" with error. Others continue. Partial progress preserved.
- **PartialIngredients:** Some ingredients fail → item completes with partial results, failed ingredients as `not_available`.
- **Interruption:** Resume from last checkpoint. Cache preserves mappings. Fast path handles previously-resolved ingredients.
- **CatalogParseFailure:** Unparseable CSV rows skipped with warning.
- **RateLimiting:** Pause and wait. If killed during wait, standard resume applies.
- **SchemaValidation:** Quote fails validation → job marked failed.
- **LLMValidationFailure:** Pydantic retry (3x). All fail → item marked failed.

## Out of Scope

- UI design is a separate phase — guided by the `interface-design` skill, not specced here.
- No markup/margin calculations.
- No authentication or multi-tenancy.
- Multi-supplier in prototype — architecture supports it, one provider implemented.
- No historical pricing or price trends.
- No guest-count total projection — per-serving only.
- No Temporal.io — documented as production path.
- No pgvector — cosine similarity in application code.
- No per-ingredient checkpointing — production improvement.

## Plan

> Phase 2 — Plan (SDD)

### Components Affected

1. **Persistence layer** — Postgres schema for jobs, work items, checkpoints, ingredient cache, and catalog embeddings.
2. **Catalog layer** — Provider interface (Sysco CSV adapter) and Catalog Service (unified API with embedding search and pricing).
3. **Decomposition engine** — Exa recipe retrieval + PydanticAI decomposition agent for structured ingredient extraction.
4. **Resolution engine** — Cache-hit fast path + PydanticAI matching agent with tools (search_catalog, get_price, update_cache).
5. **Orchestrator** — Sequential job runner with checkpointing, then concurrency.
6. **API layer** — Minimal FastAPI: submit job, get status, get quote, SSE.
7. **Frontend** — React with three views (Submit, Kitchen, The Pass) per `.interface-design/system.md`.

### Sequence of Changes

**Foundation (build and prove first):**

1. **Persistence layer** — must come first. Every other component reads from or writes to Postgres. The schema defines the data contracts that all other components depend on.

2. **Catalog layer** — depends on persistence (embeddings stored in Postgres). Provider loads CSV, Catalog Service embeds and searches. Prove that embedding search returns sensible candidates for real ingredients against the real Sysco catalog. Empirically validated: text-embedding-3-small scores 0.61 avg for matched ingredients vs 0.37 avg for unmatched — clear 0.24 separation. 14/14 correct top-1 matches. No preprocessing needed.

3. **Decomposition engine** — depends on persistence (writes checkpoints) but NOT on the catalog layer. Can be built and tested independently with mocked Exa responses. Produces ingredient lists that the resolution engine consumes.

4. **Resolution engine** — depends on persistence (reads/writes cache, writes checkpoints) AND catalog layer (search, get_price). The cache-hit fast path and matching agent both need the Catalog Service. Two code paths (fast path + agent), three agent tools, source classification logic.

**Wiring (connect the foundation):**

5. **Orchestrator** — depends on persistence, decomposition engine, and resolution engine. Wire the two engines with step sequencing and checkpointing. Start sequential (one item at a time). Add concurrency after the pipeline works end-to-end.

6. **API layer** — depends on orchestrator. Minimal endpoints: submit job, poll status, get quote. SSE after polling works.

7. **Frontend** — depends on API layer. Three views following `.interface-design/system.md`. Submit form, progress display (polling first), quote view.

### Risk Areas

All identified risks have been derisked:

- **Embedding search quality** — empirically validated. 14/14 correct top-1 matches. 0.24 avg score separation between matched (0.61) and unmatched (0.37) ingredients. No preprocessing needed.
- **UOM parsing** — derisked by design. The matching agent interprets UOM strings (e.g., "20/8 OZ" → 20 pieces × 8 oz) as part of its reasoning over catalog data. LLM handles the semi-structured format; structured output validates the arithmetic. No regex module needed.
- **Tool calling reliability** — derisked by design. PydanticAI structured output + prompt design + validation retry on malformed responses. Not architecture-level risk.

### Dependencies Map

- **CatalogProviderInterface** → independent
- **CatalogServiceInterface** → depends on: CatalogProviderInterface, EmbeddingIndex
- **EmbeddingIndex** → depends on: CatalogProviderInterface, persistence
- **RecipeRetrieval** → independent (Exa only)
- **IngredientExtraction** → depends on: RecipeRetrieval
- **DecompositionCheckpoint** → depends on: IngredientExtraction, persistence
- **CacheHitFastPath** → depends on: Global Cache, CatalogServiceInterface
- **MatchingAgentPath** → depends on: CatalogServiceInterface, Global Cache
- **CostRollup** → depends on: all ingredients resolved
- **Orchestrator behaviors** (Checkpointing, Resumability, AsyncOrchestration) → depends on: both engines + persistence
- **API + Frontend** → depends on: orchestrator

## Tasks

> Phase 3 — Tasks (SDD)

### Task 0: Set up test infrastructure and curl test runner

- **Spec behaviors satisfied:** (infrastructure — no spec behavior)
- **Acceptance condition:** Ruff is configured in `pyproject.toml` with PEP 8 rules (line length 88, isort, pyflakes, pycodestyle, bugbear enabled). `ruff check .` and `ruff format .` pass on an empty project. A `tests/curl/` directory exists with the YAML test case schema documented. A test runner script (`tests/curl/run.sh` or `tests/curl/runner.py`) reads YAML files, executes curl commands against a configurable base URL, validates response status and body assertions (including `any_string`, `any_uuid`, `any_number` matchers and `${prev.field}` interpolation), and reports pass/fail per test. A sample YAML test (`tests/curl/health.yml`) validates GET /health → 200. Documented: "No unit test written — test infrastructure, verified by running the runner against a mock endpoint."
- **Depends on:** none

### Task 1: Set up project scaffolding and persistence layer

- **Spec behaviors satisfied:** Checkpointing, Global Cache
- **Acceptance condition:** A Postgres database (via Docker Compose) accepts connections. SQLAlchemy async models define jobs (with status), work items (with step status), ingredient cache (with normalized key), and catalog embeddings. Alembic generates the initial migration (made idempotent). A Python test inserts a job, updates item status through all valid transitions, and reads it back. A cache upsert writes and reads a mapping. Docker Compose starts Postgres + API service.
- **Depends on:** none

### Task 2: Build the Catalog Provider (Sysco CSV adapter)

- **Spec behaviors satisfied:** CatalogProviderInterface, CatalogParseFailure
- **Acceptance condition:** `load_catalog()` parses `sysco_catalog.csv` and returns a list of catalog items (item_number, description, uom, cost_per_case). `get_price(item_number)` returns cost and UOM for a valid item, raises for an unknown item. A test loads the real CSV and verifies item count (565), spot-checks known items, and confirms get_price returns correct values.
  A test with a CSV containing one malformed row verifies the row is skipped with a warning and remaining rows are loaded.
- **Depends on:** none

### Task 3: Build the Catalog Service with embedding search

- **Spec behaviors satisfied:** CatalogServiceInterface, EmbeddingIndex
- **Acceptance condition:** `embed_catalog()` loads all provider items, embeds descriptions via text-embedding-3-small, and stores embeddings in Postgres. `search("applewood smoked bacon")` returns top-5 candidates with the correct Sysco item at rank 1. `get_price(item_number, provider)` delegates to the correct provider and returns cost + UOM. A test verifies search quality against 5+ known ingredient-to-catalog pairs from the embedding test results.
- **Depends on:** Tasks 1, 2

### Task 4: Build the decomposition engine (Exa + LLM extraction)

- **Spec behaviors satisfied:** RecipeRetrieval, IngredientExtraction, DecompositionCheckpoint, GracefulDegradation
- **Acceptance condition:** Given a menu item (name + description), the engine queries Exa for recipe text, passes it to the decomposition agent, and returns a structured ingredient list with per-serving quantities. A test with a mocked Exa response verifies structured output (list of ingredients with names and quantities, min_length=1). A test with Exa unavailable verifies fallback to LLM-only extraction from the dish description. Checkpoint writes ingredient list to Postgres and advances item to "decomposed."
- **Depends on:** Task 1

### Task 5: Build the resolution engine (cache fast path + matching agent)

- **Spec behaviors satisfied:** IngredientResolution, CacheHitFastPath, MatchingAgentPath, SourceClassification, CostRollup, ResolveCheckpoint, PartialIngredientFailure, CatalogServiceUnavailable, Retries
- **Acceptance condition:** Given an ingredient with a cache entry, the fast path resolves it without LLM — calls get_price, computes per-serving cost, returns IngredientMatch. A test verifies zero LLM calls on cache hit. Given an ingredient without a cache entry, the matching agent searches the catalog, evaluates candidates, gets price, updates cache, and returns IngredientMatch with correct source classification. Tests verify: (1) "beef tenderloin" → source "sysco_catalog", (2) a specialty ingredient → source "not_available", (3) cache is populated after agent run, (4) subsequent call for same ingredient hits fast path. Cost rollup sums non-null unit costs. Failed ingredients get not_available + null cost without failing the item.
- **Depends on:** Tasks 1, 3

### Task 6: Wire the orchestrator (sequential pipeline + checkpointing)

- **Spec behaviors satisfied:** JobSubmission, JobCompletion, Checkpointing, Resumability, ItemIsolation, ItemLevelFailure, PartialQuote
- **Acceptance condition:** Submit a menu spec → orchestrator creates a job, processes each item through decompose → resolve, assembles a quote. A test with 3 menu items verifies all reach "completed." A test with one failing item verifies others still complete and the quote contains only successful items. A resume test interrupts mid-processing and verifies: completed items are skipped, decomposed items resume at resolve, resolving items restart resolve (ingredients cached before interruption hit the fast path), pending items restart decomposition.
- **Depends on:** Tasks 4, 5

### Task 7: Add concurrency to the orchestrator

- **Spec behaviors satisfied:** AsyncOrchestration, RateLimiting
- **Acceptance condition:** Items process concurrently with a configurable semaphore (default 3). A test with 6+ items verifies no more than N run simultaneously. Rate limiting pauses and retries without marking items failed.
- **Depends on:** Task 6

### Task 8: Build minimal API layer (submit, status, quote)

- **Spec behaviors satisfied:** ProgressReporting, QuoteRetrieval, SchemaValidation
- **Acceptance condition:** POST /jobs with a menu spec returns a job ID. GET /jobs/{id} returns job status with per-item step info. GET /jobs/{id}/quote returns the final quote conforming to quote_schema.json. Pytest tests cover request validation and response shapes. Curl test YAML files (`tests/curl/`) define the full API contract:
  - `submit_job.yml` — POST /jobs with valid menu spec → 201, job_id returned
  - `submit_invalid.yml` — POST /jobs with empty body → 422
  - `job_status.yml` — GET /jobs/{id} → 200, status + items array
  - `job_not_found.yml` — GET /jobs/{unknown} → 404
  - `get_quote.yml` — GET /jobs/{id}/quote after completion → 200, validates against quote_schema.json
  - `quote_not_ready.yml` — GET /jobs/{id}/quote while processing → 409 or appropriate status
  A test runner script executes all YAML files against the running API and reports pass/fail.
- **Depends on:** Task 6

### Task 9: Add SSE streaming

- **Spec behaviors satisfied:** RealTimeUpdates
- **Acceptance condition:** GET /jobs/{id}/stream returns an SSE stream. Events fire for item_step_change, item_completed, item_failed, and job_completed. A pytest test connects to the stream, submits a job, and verifies events arrive in order. Curl test YAML file:
  - `sse_stream.yml` — GET /jobs/{id}/stream → 200, Content-Type: text/event-stream, receives at least one event
- **Depends on:** Task 8

### Task 10: Build the frontend (Submit view)

- **Spec behaviors satisfied:** Submit View
- **Acceptance condition:** A React page with a form for menu spec input (JSON paste or upload) and event details. Submitting calls POST /jobs and navigates to the Kitchen view. Styled per `.interface-design/system.md` — copper CTA, butcher paper canvas, parchment surfaces.
- **Depends on:** Task 8

### Task 11: Build the frontend (Kitchen view)

- **Spec behaviors satisfied:** Kitchen View
- **Acceptance condition:** A React page showing ticket cards for each menu item. Cards display current station (Prep/Match/Done) and update in real-time via SSE. Live counters show total/in-progress/completed/failed. Cards use left-border accent colors per design system (copper = processing, herb green = done, brick red = failed).
- **Depends on:** Tasks 9, 10

### Task 12: Build the frontend (The Pass view)

- **Spec behaviors satisfied:** The Pass View
- **Acceptance condition:** A React page showing the final quote. Summary header with event name, total items, total cost. Expandable line item cards — collapsed shows item name + cost, expanded shows ingredient table with name, quantity, unit cost, source badge (Catalog/Estimated/86'd), catalog item number. Export as JSON button. Prices use tabular-nums monospace, right-aligned.
- **Depends on:** Tasks 8, 10

### Task 13: Deploy to Render

- **Spec behaviors satisfied:** (deployment — no spec behavior, infrastructure task)
- **Acceptance condition:** API runs as a Render web service. Frontend deployed as a Render static site. Postgres is a Render managed instance. A submitted job completes end-to-end in production. Documented: "No test written — deployment infrastructure, verified by end-to-end smoke test."
- **Depends on:** Tasks 9, 12

## Open Questions

None.
