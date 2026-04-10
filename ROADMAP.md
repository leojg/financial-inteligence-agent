# Roadmap

An AI agent for personal finance intelligence built on LangGraph. It ingests bank statements from multiple accounts, normalizes and categorizes transactions, detects duplicates and suspicious activity across sources, produces structured reconciliation reports, and surfaces spending patterns and financial insights over time — with a human-in-the-loop review step throughout.

The project is designed as a showcase of applied LLM orchestration for real-world financial data problems. It prioritizes agentic reasoning over brittle rule-based matching, and production-grade patterns over quick demos.

---

## v0.1 — Core Pipeline ✅

The foundational reconciliation pipeline. Multi-document ingestion, LLM-powered normalization and categorization, cross-account duplicate detection with tiered matching, suspicious activity flagging, human-in-the-loop review, and structured report generation.

**Delivered:**
- LangGraph pipeline: `ingest → normalize → convert_currency → categorize → detect_duplicates → flag_suspicious → human_review → generate_report`
- Multi-format document ingestion with LLM normalization to canonical schema
- Historical exchange rate conversion via open APIs
- LLM-based transaction categorization with configurable taxonomy
- Tiered duplicate detection: exact match → fuzzy match → LLM reasoning
- Suspicious activity detection with natural language explanations
- Human review interrupt/resume via LangGraph checkpointing (MemorySaver)
- Streamlit UI with LangGraph Studio integration

---

## v0.2 — Production Hardening ✅

*Goal: make the project demo-able, testable, and contributor-ready.*

The core pipeline works but is not yet presentable. This version focuses on reliability, observability, and the scaffolding that makes a project look like a real product rather than a prototype.

**Delivered:**
- Enhanced README with architecture diagram, setup instructions, and demo GIF
- GitHub Issues and Milestones aligned to this roadmap
- SQL persistence across sessions: single SQLite DB (env `FINANCE_AGENT_DB_PATH`) for LangGraph checkpointing (Studio + Streamlit share state), exchange rate cache, and normalized document cache to avoid redundant LLM calls
- Persistent exchange rate cache (DB table) to avoid redundant API calls
- Synthetic sample data (xlsx and PDF bank statements for 4 fictitious accounts)
- Structured logging throughout the pipeline
- Fix known bugs: null handling in review count, false positive rate in suspicious detection, amounts missing from generated report
- Unit tests for normalization, duplicate detection, and categorization nodes

---

## v0.3 — Document OCR Ingestion ✅

*Goal: accept real-world bank statements and reciepts as images, not just clean CSVs. and PDFs*

Real bank statements come as PDFs or scanned images. This version adds a document parsing layer before the existing pipeline, using vision models to extract structured transaction data from unstructured documents.

**Delivered:**
- `ingest_images` node added to run parallel to `ingest`, accepting image inputs
- Vision API integration (Claude) for receipt and bank statement extraction
- Updated Streamlit UI to accept file uploads (PDF/PNG/JPG/XLSX)
- Expanded synthetic dataset with realistic statements in image and pdf formats
- Confidence scoring per extracted transaction — low-confidence rows flagged for human review

---

## v0.4 — Persistent Transaction Database ✅

*Goal: separate graph state from business data, enable cross-run querying.*

Currently the SQLite DB is the LangGraph checkpointer — it stores graph state, not reconciled transactions. This version introduces a proper business data layer that persists reconciliation results independently of graph execution state.

**Delivered:**
- `transactions` table: canonical schema per reconciled transaction
- `categories` table: category taxonomy with user-defined overrides
- Post-run data written to business DB from `generate_report` node
- `reconciliation_runs` table: metadata per run (date, accounts, totals, flags)
- Streamlit "History" tab showing past runs and their summaries
- DB migration: `schema_version` table + `_migrate()` in `agent/db.py` for schema evolution
- Query layer: filter transactions by date range, account, category, amount

---

## v0.5 — Spending Intelligence & Financial Insights ✅

*Goal: transform reconciliation results into actionable financial intelligence.*

With a persistent transaction history, the tool can surface patterns that go beyond individual run reports. This version adds an optional analytics layer that reasons over historical data to generate insights, detect behavioral patterns, and provide a lightweight financial education layer.

**Delivered:**
- `generate_insights` node (optional, toggled by config flag)
- Month-over-month spending by category with trend detection
- Anomaly detection vs personal baseline ("Feb restaurant spend +40% vs 3-month avg")
- Recurring charge detection (subscriptions, utilities, regular transfers)
- Natural language insight summaries generated by LLM
- Streamlit "Insights" tab: charts + narrative explanations

---

## v0.6 — Prompt Quality Improvements ✅

*Goal: fix accuracy problems in LLM prompts that cause receipts to be mis-parsed and income to be mis-classified.*

Receipt images currently turn every line item into a separate transaction instead of extracting the total, and bank statement normalization has no debit/credit distinction — so income is misclassified as expenses throughout the pipeline.

**Delivered:**
- Fixes for `ingest_images`, `categorize` and `normalize` prompts
- Added new `Receipt` and `ReceiptLine` models

---

## v0.7 — Database Architecture ✅

*Goal: split the flat `transactions` table into a proper three-table schema and abstract the DB layer for multi-backend support.*

The current `transactions` table cannot represent receipts (which have a total and N line items), and is tightly coupled to SQLite, making production deployments difficult.

**Delivered:**
- Split into three tables: `statements` (bank rows), `receipts` (receipt totals), `receipt_lines` (line items FK → receipts) via DB migration version 2 ([#39](https://github.com/leojg/financial-inteligence-agent/issues/39))
- Replace raw `sqlite3` with SQLAlchemy `Engine`; introduce `DATABASE_URL` env var supporting SQLite (default) and PostgreSQL ([#40](https://github.com/leojg/financial-inteligence-agent/issues/40))
- `generate_report` writes to `statements` or `receipts`/`receipt_lines` based on source type
- LangGraph checkpointer keeps its own raw `sqlite3` connection — unchanged
- Update insights to use `recepit_lines`
- Update reconciliation to use `recepit` 

---

## v0.8 — Chat Agent ✅

*Goal: add a conversational finance assistant that lets users ask natural language questions about their spending.*

Users can ask broad questions answered from pre-loaded insights context, or drill down into specific merchants, categories, and transactions via tool calls. Implemented as a separate ReAct LangGraph graph with a tool-calling loop, registered independently in langgraph.json.

**Delivered:**
- Chat agent as an independent LangGraph graph: `load_context → chat ↔ tools → END`
- ReAct loop using `ToolNode` — LLM autonomously decides when and which tools to call
- System prompt injected with aggregations, habits, suggestions, and user goals from insights cache
- Selective context inclusion: compact aggregations in prompt, granular data (receipt lines, fee transactions) available via tools
- Full date range by default — LLM narrows tool queries only when the user specifies a time period
- All 10 existing tools (5 aggregation + 5 narrow/chat-exclusive) bound to the agent
- Chat UI integrated into the Insights tab with session state and checkpointer wiring
- Registered in `langgraph.json` for LangGraph Studio debugging

---

## v1.0 — Public Release ✅

*Goal: polished, documented, deployable.*

A stable release suitable for personal use and public showcase. Focused on packaging, deployment, and end-to-end user experience.

**Delivered:**
- Docker Compose setup for one-command local deployment
- Full README with architecture deep-dive, design decisions, and limitations
- Evaluation suite: accuracy metrics for categorization and duplicate detection against labeled synthetic data
- End-to-end demo video

---

## v1.1 — Service Layer & API

*Goal: expose the three agents as a programmable service so the system can be consumed by any transport — HTTP, MCP, OpenClaw skills, or custom integrations.*

The Streamlit UI proved the agents work. This version extracts the invocation logic into a reusable service layer and wraps it in a FastAPI API, making the agent consumable as a product backend rather than just a local demo.

**Scope:**
- Rename `shared/services/` → `shared/repositories/` to reflect data access responsibility (`DatabaseService` → `DatabaseRepository`)
- `src/services/` orchestration layer — clean Python interface to invoke all three agents, decoupled from any transport
- `src/api/` FastAPI app with routes for all three agents:
  - `POST /reconciliation` — file uploads, invoke reconciliation pipeline, return run summary
  - `GET /reconciliation/runs/{run_id}` — check run status and results
  - `POST /insights` — trigger insights pipeline, return aggregations + habits + suggestions
  - `GET /insights/latest` — return cached insights without re-running
  - `POST /chat` — send a message, get a response
  - `GET /health` — liveness check with DB connectivity
- `auto_approve` mode for reconciliation — skips human-in-the-loop interrupts for automated consumers
- `api` service added to Docker Compose behind `--profile api`
- PostgreSQL promoted to default (always-on in Docker Compose); all services depend on it
- Pydantic request/response schemas in `services/schemas.py`

**Does not deliver:**
- Authentication / API keys (v2.0 — multi-tenant)
- Streaming / SSE responses (backlog)
- MCP server (separate milestone — consumes the same service layer)
- Webhook callbacks on completion (consumers poll instead)

---

## v1.2 — n8n Integration ✅

*Goal: connect the API to real-world triggers via n8n workflow automation, so the system runs without manual intervention.*

The API made the agents programmable. This version connects them to Telegram via self-hosted n8n — scheduled spending digests and a conversational chat interface, all running locally in Docker Compose.

**Delivered:**
- n8n service in Docker Compose behind `--profile n8n` with auto-import of workflow templates on first boot
- **Scheduled Insights Digest** workflow: Cron trigger → POST `/insights` → JavaScript formatting → Telegram message with spending breakdown, habits, and suggestions
- **Chat via Telegram** workflow: Telegram bot trigger → POST `/chat` with conversation persistence (Telegram chat ID as `conversation_id`) → Telegram reply
- Telegram credentials injected via environment variables (zero UI configuration)
- ngrok tunnel service behind `--profile dev` for local Telegram webhook development
- Integration documentation in `src/integrations/n8n/README.md`

**Does not deliver:**
- Email → Reconcile → Notify workflow (requires webhook callback endpoint — moved to backlog)
- Webhook callback on `POST /reconciliation` (backlog)
- n8n custom nodes (HTTP Request nodes are sufficient and more portable)

---

## v1.3 — MCP Server ✅

*Goal: expose the service layer as an MCP tool server so Claude, OpenClaw, and other MCP-compatible agents can invoke reconciliation, insights, and chat as native tools.*

The FastAPI routes proved the service layer pattern: thin transport adapters over `src/services/`. This version adds a second adapter — an MCP server — following the same pattern. The key design challenge is file ingestion: MCP tools receive structured JSON inputs, not multipart uploads, so the service layer needs a bytes-based ingestion path alongside the existing file-path flow.

**Delivered:**
- `src/mcp/` — MCP server adapter with SSE transport (Streamable HTTP), following the same thin-adapter pattern as `src/api/`
- Service layer addition: `reconciliation.run()` accepts `files: list[tuple[str, bytes]]` (filename + content) as an alternative to `file_paths: list[str]`, writing bytes to a temp directory internally — one method, two input shapes
- MCP tool definitions:
  - `start_reconciliation` — accepts base64-encoded files + `auto_approve` flag, returns `run_id` and initial status
  - `get_reconciliation_status` — accepts `run_id`, returns current `RunResult` (two-tool pattern for long-running pipeline)
  - `run_insights` — accepts optional `date_from` / `date_to`, returns aggregations + habits + suggestions
  - `get_latest_insights` — returns cached insights (no LLM cost)
  - `chat` — accepts a message string + optional `conversation_id`, returns the agent's response
- `mcp` service added to Docker Compose (`--profile mcp`)
- MCP server config template for Claude Desktop / Claude Code (`claude_desktop_config.json`)
- Documentation: tool catalog with input/output schemas, setup instructions for Claude Desktop and Claude Code

**Does not deliver:**
- Authentication / API keys (v2.0 — multi-tenant)
- Streaming partial results during reconciliation (tool returns final result or status)
- Resource endpoints (MCP resources for browsing transaction history — backlog)
- Prompt templates (MCP prompts for common financial queries — backlog)

---

## v1.4 — Evaluation Framework

*Goal: establish reliable baselines for every LLM-dependent node across all three graphs so that model and prompt optimizations can be measured, not guessed.*

The existing eval suite covers categorization accuracy (89.9%) and duplicate detection precision/recall (100%/100%), but the test data is too easy, the matching criteria too lenient, and most LLM nodes have no eval coverage at all. The 100% duplicate scores are a red flag, not a victory, the synthetic pairs only exercise the trivial exact-match tier.

This milestone hardens the evaluation framework into something trustworthy across all three agents, so the next phase (model and prompt optimizations) has honest numbers to improve against.

**Scope:**

- Improved `generate_samples.py` with harder test data: merchant name variations across banks, fuzzy amount pairs, false positive bait for duplicates, ambiguous merchants for categorization, suspicious activity patterns (outlier amounts, rapid-fire charges), and a new normalization labels section
- Normalization eval: field-level accuracy (date, merchant, amount, currency) for the normalize node against labeled synthetic data
- Duplicate eval rewrite: harder pairs, per-tier metrics (fingerprint vs fuzzy vs LLM), tier-hit diagnostics, expanded non-duplicate set (25 to 30 pairs)
- Categorization eval revision: tighter semantic equivalents, alternatives-aware scoring (primary vs alternative vs miss)
- Suspicious activity eval: recall on known suspicious patterns, precision on clean transactions, broken down by pattern type
- Insights `generate_insights` eval: LLM-as-judge scoring on faithfulness to aggregation data, goal relevance, and coverage, against curated aggregation snapshots with documented expectations
- Chat tool selection eval: labeled question set mapped to expected tool calls, tool selection accuracy metric
- Chat response faithfulness eval: LLM-as-judge comparing tool output to final response, plus out-of-scope handling verification
- Baseline numbers documented as the reference point for the optimization phase

**Does not deliver:**
- Model or prompt optimizations (this milestone establishes baselines, the next milestone improves them)
- Vision/image extraction eval (deferred, higher cost, added incrementally once text-based eval is proven)
- End-to-end multi-graph eval (node-level baselines first)

**Full spec:** `docs/v1.4-evaluation-framework.md`

**Status:** Core deliverables are in place (samples + labels, normalize / categorize / duplicate / suspicious / insights / chat tool-selection evals, documented baselines). Remaining spec refinements (e.g. alternatives-aware categorize scoring, duplicate implementation-tier diagnostics, insights judge, chat §4.2) are tracked under **Backlog → Eval & pipeline hardening**.

---

## v1.5 — Prompt and Model Optimization

*Goal: use the v1.4 baselines to make targeted improvements to LLM output quality and reduce running costs, with every change measured against the eval framework.*

This milestone is experiment-driven. Every optimization follows the same loop: identify a target from the v1.4 baselines, make exactly one change (prompt or model, not both), run the eval suite, compare quality and cost deltas, keep or revert.

**Scope:**

- Comparison runner infrastructure: run the same eval test set against multiple model/prompt configurations and produce side-by-side metrics with cost estimates
- Prompt variant management: named prompt alternatives per node, selectable by the comparison runner without branch juggling
- Reconciliation experiments: currency extraction improvements (prompt debiasing, few-shot examples, potential model upgrade for normalize, deterministic post-processing), categorization prompt anchoring (few-shot examples, preferred category vocabulary), duplicate detection prompt improvements if v1.4 reveals weaknesses in fuzzy/LLM tiers
- Insights experiments: test gpt-4o-mini as a replacement for gpt-4o, structured output guidance, few-shot examples
- Chat experiments: test gpt-4o-mini for all chat (baseline before building routing), classifier-based model routing if mini fails on complex questions, tool description improvements
- Documented comparison tables for every experiment, saved as reference
- Cost baseline: estimated per-run cost for reconciliation, insights, and per-chat-message

**Does not deliver:**
- New features or architectural changes (purely quality/cost optimization on existing nodes)
- Vision model optimization (deferred)
- Chat message persistence or summarization (separate milestone)

**Full spec:** `docs/v1.5-prompt-model-optimization.md`

---

## Backlog / Future Considerations

Ideas that are out of scope for the current roadmap but worth tracking:

### Eval & pipeline hardening (post–v1.4)

Follow-ups deferred from the v1.4 evaluation framework; see `docs/v1.4-evaluation-framework.md`. Intended to support **v1.5 — Prompt and Model Optimization** with sharper signals.

- **Categorization**
  - **`alternatives` in labels + scoring** — `eval_labels` / `generate_samples.py`: optional acceptable categories per ambiguous merchant; eval reports primary vs alternative vs miss (not a single accuracy bit).
  - **Richer model input** — include **account** (and optionally **source**) in the categorize batch line so Wise vs local bank is not inferred from merchant alone.
  - **Tighter semantic equivalents** — keep only true synonyms in `test_categorize_eval.py`; remove broad bridges unless product accepts them as equivalent.
  - **Segment EVAL bait** — exclude or score separately rows meant for duplicate eval (`EVAL BAIT …`) so categorize metrics reflect “real” merchants.
  - **Merchant category cache policy** — document / revisit when `merchant_categories` upserts run; mitigate wrong first-seen categories sticking forever (product concern, affects production more than eval).

- **Duplicate detection**
  - **Implementation-tier diagnostics** — log which **code path** linked each pair (fingerprint vs fuzzy amount vs LLM), not only label tier (exact / alias / fuzzy_amount / …). Surfaces whether hard pairs actually exercise non-exact tiers.
  - **Label coverage** — ensure synthetic data includes explicit **temporal** and **recurring same-merchant different-month** non-duplicates where the spec calls for them.
  - **Eval stability** — set recall/precision floors from measured baselines; watch per-tier false-positive rates on non-dup bait.

- **Insights eval (stretch)**
  - Additional **profiles** (e.g. recurring bloat, minimal data) and optional **LLM-as-judge** for numeric faithfulness vs deterministic substring checks.

- **Chat eval (stretch)**
  - **§4.2 Response faithfulness** — judge compares tool output to final reply; out-of-scope handling assertions beyond tool selection.

- **Cross-cutting**
  - **Comparison runner** infrastructure overlaps with **v1.5**; keep eval tables as the single source of truth before changing prompts/models.

---

- **Chat message persistence** — Hybrid approach: store messages in a dedicated `chat_messages` table, load a sliding window (~20 messages) into state via `load_context`. Prevents checkpointer bloat from full message history serialization on every ReAct step. Enables searchable chat history and a conversation list sidebar in the UI.
- **Chat message summarization** — Periodically condense older messages into a summary message to preserve long-range context without growing the token window. Complements the message windowing approach.
- **Chat model routing** — Classifier-based routing between `gpt-4o-mini` (simple questions answerable from context or a single tool call) and `gpt-4o` (complex multi-step reasoning). Reduces cost for the majority of questions while preserving quality for hard ones.
- **Chat date range filtering** — Allow the chat agent to operate on a scoped date range instead of always full range. Requires insights to always maintain a full-range cache alongside scoped runs.
- **Insights cache relational refactor** — Split habits and suggestions out of the JSON blob in `insights_cache` into proper relational tables with typed columns (category, severity, observation). Enables querying by severity, tracking insight evolution across runs, and safer schema migrations.
- **Crypto / Bitcoin mining income** — BTC transaction ingestion and cost basis tracking
- **Budget vs actual** — compare reconciled spending against a defined monthly budget
- **Web deployment** — hosted version with user accounts and cloud storage
- **Bank API integration** — replace manual statement imports with Plaid or open banking APIs
- **Export** — CSV/PDF export of reconciliation reports and insights
- **Webhook callback on POST /reconciliation** — optional `callback_url` parameter; API fires a POST with the `RunResult` payload on completion. Enables fire-and-forget workflows (e.g. email → reconcile → notify) without polling.
- **Email → Reconcile → Notify workflow** — n8n workflow: Gmail/IMAP trigger → extract attachments → POST `/reconciliation` with callback → format result → Telegram. Depends on the webhook callback endpoint.