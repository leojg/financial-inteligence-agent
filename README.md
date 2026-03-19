# Finance Intelligence Agent

![Version](https://img.shields.io/badge/version-v1.1-blue)

An AI agent system for personal finance intelligence built on LangGraph. It ingests bank statements and receipts from multiple accounts, normalizes and categorizes transactions, detects duplicates and suspicious activity across sources, produces structured reconciliation reports, and surfaces spending patterns and financial insights over time — with human-in-the-loop review steps throughout.

The system is composed of three independent LangGraph graphs — **Reconciliation**, **Insights**, and **Chat** — connected through a shared database layer. Each graph has a distinct data source and invocation lifecycle, but they all operate on the same persistent transaction history.

All three agents are accessible through a **Streamlit UI** for interactive use and a **FastAPI REST API** for programmatic access by external consumers (automation tools, MCP servers, OpenClaw skills, custom integrations).

Built to demonstrate production-grade LLM orchestration patterns applied to real financial data problems.

**See [ROADMAP.md](ROADMAP.md) for version history, current status, and planned work.**

---

## Architecture

The system is split into three independent LangGraph graphs registered in `langgraph.json`, all sharing the same PostgreSQL database through a common `DatabaseRepository` layer.

A **service layer** (`src/services/`) provides transport-agnostic orchestration — clean Python functions that compile graphs, invoke them, and return structured results. Both the Streamlit UI and the FastAPI API are thin adapters over this layer, and future transports (MCP server, OpenClaw skills) will follow the same pattern.

### Reconciliation Agent

A directed pipeline with fan-in/fan-out parallelism, conditional edges, and two human-in-the-loop interrupts:

![Reconciliation Agent Architecture](https://raw.githubusercontent.com/leojg/financial-inteligence-agent/refs/heads/master/static/reconciliation_agent_graph.svg)

| Node | Responsibility |
|---|---|
| `prepare_ingest` | Expands source paths (folders + files) into a deduplicated file list |
| `ingest` | Loads PDF and XLSX files into raw documents |
| `ingest_images` | Vision model (Claude Sonnet) extracts receipts and statements from images |
| `skip_documents` / `skip_images` | Passthrough nodes when no documents or images are present |
| `normalize` | LLM extracts transactions into a canonical schema with normalized document caching |
| `review_low_confidence_transactions` | Interrupt — user confirms, edits, or dismisses low-confidence OCR extractions |
| `convert_currency` | Fetches historical exchange rates and converts amounts to the base currency |
| `categorize` | LLM assigns categories in batches, with merchant category caching |
| `detect_duplicates` | Tiered matching: exact fingerprint → fuzzy → LLM reasoning, with duplicate pairs caching |
| `flag_suspicious` | LLM identifies anomalous activity with natural language explanations |
| `human_review` | Interrupt — user confirms or rejects flagged transactions via the UI |
| `generate_report` | Persists transactions and receipts to the DB, produces a structured run summary |

Key graph patterns: `prepare_ingest` uses conditional edges to fan out into parallel `ingest` + `ingest_images` paths (skipping either when no matching files exist), which fan back in at `normalize`. After normalization, a conditional edge routes to `review_low_confidence_transactions` only when OCR confidence is below threshold. After `categorize`, `detect_duplicates` and `flag_suspicious` run in parallel, converging at `human_review`.

### Insights Agent

A linear pipeline with a conditional cache bypass:

![Insights Agent Architecture](https://raw.githubusercontent.com/leojg/financial-inteligence-agent/refs/heads/master/static/insights_agent_graph.svg)

| Node | Responsibility |
|---|---|
| `load_context` | Resolves date range, loads user goals, checks if insights cache is stale vs latest reconciliation run |
| `compute_aggregations` | Pure Python — queries DB for spending by category, month-over-month deltas, recurring charges, transfer fees, receipt line breakdowns |
| `generate_insights` | LLM (`gpt-4o`) reasons over the compact aggregation dict and user goals, returns structured habits and suggestions |
| `persist_results` | Writes aggregations, habits, and suggestions to the insights cache |

### Chat Agent

A ReAct agent with a tool-calling loop:

![Chat Agent Architecture](https://raw.githubusercontent.com/leojg/financial-inteligence-agent/refs/heads/master/static/chat_agent_graph.svg)

Invoked independently on every user message. Receives aggregations and goals context from the UI session state (populated by a prior insights run). Has access to all DB query tools via `tools.py`.

---

## Stack

- **[LangGraph](https://github.com/langchain-ai/langgraph)** — agent orchestration, state management, and checkpointing
- **[LangChain OpenAI](https://github.com/langchain-ai/langchain)** — LLM integration (`gpt-4o-mini` for extraction/classification, `gpt-4o` for insights synthesis)
- **[LangChain Anthropic](https://github.com/langchain-ai/langchain)** — vision model integration (Claude Sonnet for receipt OCR)
- **[FastAPI](https://fastapi.tiangolo.com)** — REST API for programmatic access
- **[SQLAlchemy](https://www.sqlalchemy.org)** + **[Alembic](https://alembic.sqlalchemy.org)** — database abstraction and schema migrations, supporting SQLite (default) and PostgreSQL
- **[Streamlit](https://streamlit.io)** — UI for running agents, reviewing results, and exploring insights
- **[LangGraph Studio](https://github.com/langchain-ai/langgraph-studio)** — visual graph debugging (all three graphs visible independently)
- **[exchangerate.host](https://exchangerate.host)** — historical exchange rate API
- **[Pydantic](https://docs.pydantic.dev)** — structured LLM output validation and data models
- Python 3.12, pandas, pytest, ruff

---

## Quick Start (Docker)

The fastest way to run the project. Requires [Docker Desktop](https://www.docker.com/products/docker-desktop/).

**1. Clone and configure**

```bash
git clone https://github.com/leojg/finance-intelligence-agent.git
cd finance-intelligence-agent
cp .env.example .env
```

Edit `.env` and add your API keys:

```env
OPENAI_API_KEY=sk-...
EXCHANGE_RATE_API_KEY=...       # from exchangerate.host
DATABASE_URL=postgresql://finance:finance@postgres:5432/finance_agent
```

**2. Start the services**

```bash
docker compose up
```

This builds the image and starts three services:

| Service | URL | Purpose |
|---|---|---|
| Streamlit UI | [http://localhost:8501](http://localhost:8501) | Main interface — run agents, review transactions, explore insights |
| LangGraph Studio API | [http://localhost:8123](http://localhost:8123) | Graph debugger — open [LangGraph Studio](https://github.com/langchain-ai/langgraph-studio) desktop app and connect to this URL |
| PostgreSQL | `localhost:5432` | Primary database — schema migrations run automatically on startup |

**3. (Optional) Start the REST API**

```bash
docker compose --profile api up
```

This adds the FastAPI service alongside the existing ones:

| Service | URL | Purpose |
|---|---|---|
| REST API | [http://localhost:8000](http://localhost:8000) | Programmatic access — for automation tools, MCP servers, OpenClaw skills |
| Swagger docs | [http://localhost:8000/docs](http://localhost:8000/docs) | Interactive API documentation |

Sample data is included in the repo and available immediately. The database and any uploaded files persist across container restarts.

---

## Local Setup (without Docker)

<details>
<summary>Click to expand manual setup instructions</summary>

**1. Clone the repo**

```bash
git clone https://github.com/leojg/finance-intelligence-agent.git
cd finance-intelligence-agent
```

**2. Create virtual environment and activate it**

```bash
python -m venv venv
source venv/bin/activate
```

**3. Install dependencies**

```bash
pip install -r requirements.txt
pip install -e .
```

The editable install (`pip install -e .`) makes the `agents`, `shared`, `services`, and `api` packages importable so the Streamlit app, API server, and `langgraph dev` can find them.

**4. Configure environment**

```bash
cp .env.example .env
```

Edit `.env` and add your keys:

```env
OPENAI_API_KEY=sk-...
EXCHANGE_RATE_API_KEY=...       # from exchangerate.host

# Database URL — supports SQLite (default) and PostgreSQL
# DATABASE_URL=sqlite:///data/agent.db
# DATABASE_URL=postgresql://user:pass@localhost/finance_agent

# Optional: LangSmith tracing
LANGSMITH_API_KEY=...
LANGSMITH_PROJECT=finance-intelligence-agent
```

**5. Prepare your statements**

Sample data is included in the repo under `data/`. To regenerate or customize:

```bash
python scripts/generate_samples.py
```

**6. Run**

```bash
# Streamlit UI
streamlit run src/ui/app.py

# REST API (separate terminal)
uvicorn api.main:app --host 0.0.0.0 --port 8000

# LangGraph Studio (separate terminal)
langgraph dev
```

</details>

---

## Usage

1. Upload your statement files or enter the path to your statements folder in the sidebar
2. Click **Run Agent**
3. Review low-confidence OCR extractions if prompted
4. Review flagged transactions in the **Review** tab
5. Click **Resume** to generate the final report
6. Switch to the **Insights** tab to run spending analysis and view suggestions

### Execution Demo

<img src="https://raw.githubusercontent.com/leojg/financial-inteligence-agent/refs/heads/master/static/financial-agent-gif.gif" alt="Execution Demo" width="900" />

---

## API

The REST API exposes all three agents as HTTP endpoints for programmatic access. Interactive documentation is available at [http://localhost:8000/docs](http://localhost:8000/docs) when the API service is running.

### Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check with DB connectivity status |
| `POST` | `/reconciliation` | Upload files and run the reconciliation pipeline |
| `GET` | `/reconciliation/runs/{run_id}` | Check run status and retrieve results |
| `POST` | `/insights` | Run the insights pipeline |
| `GET` | `/insights/latest` | Return cached insights without re-running |
| `POST` | `/chat` | Send a message to the financial chat agent |

### Examples

**Run reconciliation on uploaded statements:**

```bash
curl -X POST http://localhost:8000/reconciliation \
  -F "files=@data/statements/itau_2026_01.pdf" \
  -F "files=@data/statements/brou_2026_01.xlsx" \
  -F "auto_approve=true"
```

Returns a `run_id`, status, transaction counts, and flagged items. With `auto_approve=true` (default), both human-in-the-loop interrupts are skipped — suitable for automated workflows.

**Generate spending insights:**

```bash
curl -X POST http://localhost:8000/insights \
  -H "Content-Type: application/json" \
  -d '{"date_from": "2026-01-01", "date_to": "2026-01-31"}'
```

Returns aggregations (spending by category, month-over-month deltas, recurring charges), habits, and suggestions. Omit the date range to analyze the full transaction history.

**Read cached insights (no LLM cost):**

```bash
curl http://localhost:8000/insights/latest
```

**Ask a question about your finances:**

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What did I spend the most on last month?"}'
```

### Integration patterns

The API is designed to be consumed by automation tools and AI agent frameworks:

- **n8n / Zapier** — HTTP Request nodes calling the endpoints to build email-triggered reconciliation workflows or scheduled reporting
- **OpenClaw** — Skills that wrap the API endpoints, enabling natural language financial queries through a personal AI assistant
- **MCP servers** — Tool definitions backed by the same service layer (planned — see [ROADMAP.md](ROADMAP.md))
- **Custom scripts** — Any HTTP client can trigger reconciliation, poll for results, and read insights

---

## Project Structure

```
finance-intelligence-agent/
├── src/
│   ├── agents/
│   │   ├── reconciliator/          # Reconciliation agent
│   │   │   ├── graph.py            # LangGraph pipeline with fan-in/fan-out and interrupts
│   │   │   ├── nodes.py            # Node implementations
│   │   │   ├── state.py            # ReconciliationState, reducers, initial_state
│   │   │   ├── configuration.py    # ReconciliationConfig
│   │   │   └── utils/
│   │   │       └── parsers.py      # PDF and XLSX document loaders
│   │   │
│   │   ├── insights/               # Insights agent
│   │   │   ├── graph.py            # Linear pipeline with cache conditional
│   │   │   ├── nodes.py            # load_context, compute_aggregations, generate_insights, persist_results
│   │   │   ├── state.py            # InsightsState
│   │   │   ├── configuration.py    # InsightsConfig
│   │   │   └── tools.py            # @tool wrappers over DatabaseRepository query methods
│   │   │
│   │   └── chat/                   # Chat agent
│   │       ├── graph.py            # ReAct tool-calling loop
│   │       ├── nodes.py            # llm_node, tool_node
│   │       ├── state.py            # ChatState
│   │       └── configuration.py    # ChatConfig
│   │
│   ├── api/                        # FastAPI REST API (transport adapter)
│   │   ├── main.py                 # App factory, lifespan, CORS, router wiring
│   │   └── routes/
│   │       ├── reconciliation.py   # POST /reconciliation, GET /reconciliation/runs/{run_id}
│   │       ├── insights.py         # POST /insights, GET /insights/latest
│   │       └── chat.py             # POST /chat
│   │
│   ├── services/                   # Orchestration layer — transport-agnostic agent invocation
│   │   ├── schemas.py              # Pydantic response models (RunResult, InsightsResult, ChatResponse)
│   │   ├── reconciliation.py       # run(), get_status()
│   │   ├── insights.py             # run(), get_latest()
│   │   └── chat.py                 # send_message()
│   │
│   ├── shared/                     # Shared infrastructure across all agents
│   │   ├── db/
│   │   │   ├── __init__.py         # SQLAlchemy engine, session factory, migrations, checkpointer
│   │   │   ├── models.py           # ORM models (RunHistory, TransactionRecord, receipts, etc.)
│   │   │   └── alembic/            # Alembic migration scripts
│   │   ├── models.py               # Pydantic models (Transaction, RawDocument, Receipt, InsightsOutput)
│   │   └── repositories/
│   │       ├── database_repository.py # All SQL — single source of truth for DB operations
│   │       └── exchange_repository.py # Historical exchange rate fetching
│   │
│   └── ui/
│       └── app.py                  # Streamlit UI (reconciliation, insights, settings, history tabs)
│
├── scripts/
│   └── generate_samples.py         # Synthetic bank statement and receipt generator (also writes eval_labels.json)
├── tests/
│   ├── unit_tests/                 # Mocked unit tests (no API keys required)
│   └── eval/                       # LLM accuracy evals (real API calls)
│       ├── conftest.py             # Fixtures: labels loader, API key guard
│       ├── helpers.py              # Transaction factory shared by eval tests
│       ├── test_categorize_eval.py # Categorization accuracy (threshold: 75%)
│       └── test_duplicate_eval.py  # Duplicate precision/recall (recall ≥ 90%, precision ≥ 85%)
├── data/                           # Generated sample data and database
├── Dockerfile
├── docker-compose.yml
├── dockerignore
├── alembic.ini
├── langgraph.json                  # Registers reconciliation, insights, and chat graphs
├── pyproject.toml
├── ROADMAP.md
└── README.md
```

---

## Configuration

The system uses separate configuration for each agent:

**`ReconciliationConfig`** (`src/agents/reconciliator/configuration.py`):

| Parameter | Default | Description |
|---|---|---|
| `model_name` | `gpt-4o-mini` | Model for normalization, categorization, duplicate detection, suspicious flagging |
| `vision_model_name` | `claude-sonnet-4-6` | Vision model for receipt and statement image extraction |
| `vision_max_tokens` | `4096` | Max tokens for vision model responses |
| `temperature` | `0.0` | Deterministic outputs for consistency |
| `base_currency` | `USD` | Target currency for amount conversion |
| `image_low_confidence_threshold` | `0.98` | OCR results below this trigger human review |

**`InsightsConfig`** (`src/agents/insights/configuration.py`):

| Parameter | Default | Description |
|---|---|---|
| `model_name` | `gpt-4o` | Larger model for analytical reasoning over aggregated spending data |
| `temperature` | `0.0` | Deterministic outputs — financial analysis should be reproducible |

**Database**: configured via `DATABASE_URL` environment variable. Supports `sqlite:///` (default: `data/agent.db`) and `postgresql://`. PostgreSQL is the default in Docker Compose. Schema migrations are handled automatically by Alembic on app startup.

---

## Design Decisions

This section documents the key architectural choices made during development, the alternatives considered, and the reasoning behind each decision.

### Three Separate Agents, One Shared Database

The system is split into three independent LangGraph graphs — **Reconciliation** (pipeline), **Insights** (pipeline), and **Chat** (ReAct agent) — rather than a single monolithic graph. The deciding factor was that each agent has a fundamentally different data source and invocation lifecycle:

- The **Reconciliation Agent** operates on in-memory graph state — transactions flowing through nodes in a single run.
- The **Insights Agent** operates on historical DB data — querying the `transactions` table across multiple reconciliation runs to surface spending patterns.
- The **Chat Agent** is invoked independently on every user message, running a ReAct tool-calling loop against the same DB.

There's no meaningful state to hand off between them — the shared medium is the database, not graph state. All three are registered independently in `langgraph.json` and visible as separate graphs in LangGraph Studio.

This separation also means insights never run at the end of reconciliation. Insights are pull, not push — tacking analysis onto every upload would slow the core flow and produce poor results on early runs with little history.

### Service Layer Over Direct Graph Invocation

The Streamlit UI originally invoked graphs directly — compiling them, managing checkpointers, and parsing results inline. v1.1 extracted this into a `services/` layer that provides clean Python functions (`reconciliation.run()`, `insights.run()`, `chat.send_message()`) returning Pydantic models.

Both the Streamlit UI and the FastAPI API are thin adapters over this layer. The service owns graph compilation, config defaults, error handling, and response shaping. The transports only handle their protocol-specific concerns (Streamlit session state, HTTP request/response). This means adding a new transport — an MCP server, an OpenClaw skill, a CLI tool — requires no changes to the orchestration logic.

### The `shared/` Layer and `DatabaseRepository` Boundary

All SQL lives in `DatabaseRepository`. Nodes and tools never call `get_connection()` directly. Tools are thin `@tool` wrappers over repository methods, split by usage scope (pipeline vs. chat-only). This pattern comes from Android's datasource abstraction and ensures that the DB schema is a single source of truth, not scattered across graph nodes.

When the insights agent was introduced, `DatabaseRepository`, `db.py`, and the `Transaction` model were extracted into a `shared/` package — the contract layer between all three agents. The restructure from `agent/` into `agents/reconciliation/`, `agents/insights/`, and `agents/chat/` followed naturally.

### Model Selection by Task

Three models are used, each chosen for the characteristics of its task:

- **`gpt-4o-mini`** for high-volume structured extraction — normalization, categorization, and duplicate detection. These are well-defined tasks with clear right/wrong answers where mini performs well at a fraction of the cost.
- **Claude Sonnet** for vision — receipt OCR requires multimodal capabilities that OpenAI's mini model doesn't handle as reliably.
- **`gpt-4o`** for synthesis — the insights `generate_insights` node needs to reason over spending trends across categories, cross-reference user goals, and produce nuanced observations. The quality gap between mini and a larger model is significant for analytical judgment tasks. The cost tradeoff is acceptable because this node runs once per session, not per transaction.

All LLM nodes use `temperature=0.0`. An initial design considered `0.3` for insights generation to produce more varied language, but this was rejected: in a financial context, two runs on the same data should produce consistent, reproducible output. Determinism is more important than stylistic variety.

### Multi-Layer Caching Strategy

Every LLM call that can be cached, is cached — not just for cost savings, but to make re-runs fast and idempotent:

- **Normalized document cache** — keyed by content hash. If the same bank statement is uploaded again, normalization is skipped entirely. Known caveat: structural changes to the normalization prompt require a full cache clear, as stale entries from pre-refactor runs won't match the new schema.
- **Merchant category cache** — maps `normalized_merchant → category`. Once "SUPERMERCADO DISCO" is categorized, it never hits the LLM again across any future run.
- **Duplicate pairs cache** — stores both positive *and* negative LLM evaluations. This is a deliberate design choice: the `transactions` table only records confirmed duplicates (via `duplicate_of`), but pairs the LLM cleared as not-duplicates have no record there. Without this cache, those same fuzzy pairs would trigger redundant LLM calls on every re-run.
- **Exchange rate cache** — avoids redundant API calls for historical rates already fetched.
- **Insights cache** — keyed by date range and accounts. Avoids re-running the LLM when the underlying data hasn't changed since the last reconciliation run.

### Tiered Duplicate Detection

Duplicate detection uses three tiers: exact fingerprint match → fuzzy match (date and amount proximity) → LLM reasoning. Each tier is progressively more expensive. The fingerprint is a hash of `(date, amount, currency, merchant)`, normalized to catch the same real-world transaction across different bank statements.

The `duplicate_pairs` table was initially removed in favor of querying the `transactions` table directly (via self-join on `duplicate_of`). This was reversed after realizing the two tables serve distinct purposes: `transactions` gives positive duplicate results as a side effect, but `duplicate_pairs` caches the full evaluation — including negatives — so the LLM is called at most once per pair regardless of outcome.

### Insights Pipeline: Aggregations as the Compression Layer

The LLM never sees raw transactions. The `compute_aggregations` node (pure Python, no LLM) collapses thousands of transaction rows into a compact summary — spending by category, month-over-month deltas, recurring charges, transfer fee summaries. This dict is typically 50–100 lines of JSON.

The `generate_insights` node receives only this compressed aggregation. This design means the system scales to any transaction volume without hitting context window limits, and the LLM reasons over percentages and deltas rather than individual rows — producing better analytical output.

When new reconciliation data arrives, aggregations are always recomputed from the full dataset (Option A) rather than merged incrementally with cached results (Option B). The aggregation queries run against indexed SQLite columns and are fast even with thousands of rows; the real cost is the LLM call. Incremental merging was rejected because aggregations like month-over-month deltas and recurring charge detection are not trivially additive — a single new transaction can affect multiple computed metrics.

### Chat as an Independent Graph, Not a Subgraph

The chat agent is a fully independent compiled graph — a sibling to reconciliation and insights, not a subgraph of either. The reason is invocation lifecycle: the insights pipeline runs once on demand, then exits. The chat agent needs to be invoked independently on every user message. If chat were a subgraph of insights, every new message would re-enter the parent graph from the top, running through `load_context` and the pipeline check again.

The UI invokes the insights pipeline once to produce aggregations and suggestions, then invokes the chat graph independently on each user message, passing the pipeline's output as context. Tools are just Python functions imported from `tools.py` — any graph can use them regardless of graph hierarchy, so the chat agent has full access to DB query tools without needing a parent-child relationship.

### User Goals in the Database, Not Config

User goals (e.g., "Save 20% of monthly income", "Reduce restaurant spending") are stored in a `user_goals` DB table, not in agent config. Config is for deployment-time settings — model name, temperature, base currency. Goals are user data: they're personal, they change over time, and they need to persist across sessions independently of any analysis run. The goals are loaded as free-text, joined into a single string, and injected into the LLM prompt — the model handles the semantics.

### Currency as a Graph-Level Concern

Currency extraction from receipts and invoices is intentionally left as `null` when ambiguous. The `$` symbol is used by Argentina, Uruguay, Colombia, Mexico, and the USA — it's not a reliable indicator. Even with explicit prompt instructions to return null for ambiguous currency, `gpt-4o-mini` will infer from contextual clues like business names. Pydantic field description examples (e.g., `"e.g. USD, EUR, UYU"`) further bias the model toward guessing.

The architectural resolution: treat currency as a graph-level concern. Extraction tools return null for ambiguous fields; downstream nodes in the graph resolve ambiguity using broader context — account region, other documents in the same batch, user settings. This keeps tools lean and honest, and pushes resolution to where the context actually exists.

### LLM-Inferred Categories Over Fixed Taxonomy

The categorization node initially used a fixed, config-driven category list injected into the prompt. This was replaced with LLM-inferred categories — the model creates appropriate categories based on the actual spending patterns in the data, rather than forcing transactions into a predefined taxonomy. This produces more natural categorization that adapts to different users' spending profiles, at the cost of slightly less consistency across runs (mitigated by the merchant category cache, which ensures the same merchant always gets the same category once categorized).

### Node-Level DB Writes

Each node writes its results to the database as it completes, rather than batching all writes at the end of the run. This means a partially completed run still persists useful data (e.g., normalized and categorized transactions are saved even if duplicate detection fails). It also simplifies error recovery and makes the system more debuggable — you can inspect intermediate DB state at any point during a run.

---

## Development

```bash
# Run unit tests (mocked, no API keys required)
make test

# Run LLM eval suite (requires OPENAI_API_KEY, makes real API calls)
make eval

# Lint and format
make lint
make format

# Spell check
make spell_check
```

### Eval Suite

`tests/eval/` contains node-level accuracy evals that measure LLM output quality against labeled synthetic data. Unlike unit tests, these make real API calls and are intentionally excluded from `make test` and CI.

**How it works:**

1. `scripts/generate_samples.py` writes `data/eval_labels.json` alongside the synthetic statement files. Labels are derived directly from the ground-truth constants in the script (`ITAU_TRANSACTIONS`, `BROU_TRANSACTIONS`, etc.) — expected categories, known duplicate pairs, and non-duplicate pairs are all extracted automatically.

2. `make eval` loads those labels, builds `Transaction` objects, calls the real `categorize` and `detect_duplicates` nodes, and computes metrics.

**Thresholds (from last run):**

| Test | Metric | Threshold | Last result |
|---|---|---|---|
| `test_categorize_accuracy` | Accuracy | ≥ 75% | 89.9% |
| `test_duplicate_precision_recall` | Recall | ≥ 90% | 100% |
| `test_duplicate_precision_recall` | Precision | ≥ 85% | 100% |

Matching uses case-insensitive substring comparison plus a semantic equivalents map for known synonyms (`Healthcare` ↔ `Fitness`, `Salary` ↔ `Freelance`, `Transfer` ↔ `Other Income`, `Fees & Charges` ↔ `Other`). Duplicate detection uses union-find clustering so transitive groups (A→B and A→C implies B≡C) are handled correctly.

To regenerate labels after changing the synthetic data:

```bash
python scripts/generate_samples.py
```

---

## License

Apache 2.0 — see [LICENSE](LICENSE).