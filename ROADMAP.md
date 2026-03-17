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

## Backlog / Future Considerations

Ideas that are out of scope for the current roadmap but worth tracking:

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