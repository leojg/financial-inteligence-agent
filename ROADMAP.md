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

## v0.2 — Production Hardening 🔧

*Goal: make the project demo-able, testable, and contributor-ready.*

The core pipeline works but is not yet presentable. This version focuses on reliability, observability, and the scaffolding that makes a project look like a real product rather than a prototype.

**Delivered:**
- Enhanced README with architecture diagram, setup instructions, and demo GIF
- GitHub Issues and Milestones aligned to this roadmap
- SQL persistence across sessions: single SQLite DB (env `FINANCE_AGENT_DB_PATH`) for LangGraph checkpointing (Studio + Streamlit share state), exchange rate cache, and normalized document cache to avoid redundant LLM calls
- Persistent exchange rate cache (DB table) to avoid redundant API calls
- Synthetic sample data (xlsx and PDF bank statements for 4 fictitious accounts)

**Planned:**
- Fix known bugs: null handling in review count, false positive rate in suspicious detection, amounts missing from generated report
- Structured logging throughout the pipeline
- Unit tests for normalization, duplicate detection, and categorization nodes

---

## v0.3 — Document OCR Ingestion 📄

*Goal: accept real-world bank statements as images and PDFs, not just clean CSVs.*

Real bank statements come as PDFs or scanned images. This version adds a document parsing layer before the existing pipeline, using vision models to extract structured transaction data from unstructured documents.

**Planned:**
- `parse_documents` node added before `ingest`, accepting PDF and image inputs
- Vision API integration (Claude) for receipt and bank statement extraction
- Confidence scoring per extracted transaction — low-confidence rows flagged for human review
- Support for multi-page PDFs
- Fallback handling for documents that cannot be parsed
- Updated Streamlit UI to accept file uploads (PDF/PNG/JPG)
- Expanded synthetic dataset with realistic PDF statements

---

## v0.4 — Persistent Transaction Database 🗄️

*Goal: separate graph state from business data, enable cross-run querying.*

Currently the SQLite DB is the LangGraph checkpointer — it stores graph state, not reconciled transactions. This version introduces a proper business data layer that persists reconciliation results independently of graph execution state.

**Planned:**
- `transactions` table: canonical schema per reconciled transaction
- `reconciliation_runs` table: metadata per run (date, accounts, totals, flags)
- `categories` table: category taxonomy with user-defined overrides
- Post-run data written to business DB from `generate_report` node
- Query layer: filter transactions by date range, account, category, amount
- Streamlit "History" tab showing past runs and their summaries
- DB migration strategy for schema evolution

---

## v0.5 — Spending Intelligence & Financial Insights 📊

*Goal: transform reconciliation results into actionable financial intelligence.*

With a persistent transaction history, the tool can surface patterns that go beyond individual run reports. This version adds an optional analytics layer that reasons over historical data to generate insights, detect behavioral patterns, and provide a lightweight financial education layer.

**Planned:**
- `analyze_patterns` node (optional, toggled by config flag)
- Month-over-month spending by category with trend detection
- Anomaly detection vs personal baseline ("Feb restaurant spend +40% vs 3-month avg")
- Recurring charge detection (subscriptions, utilities, regular transfers)
- Natural language insight summaries generated by LLM
- Streamlit "Insights" tab: charts + narrative explanations
- Configurable alert thresholds (e.g. flag any category exceeding a monthly budget)

---

## v1.0 — Public Release 🚀

*Goal: polished, documented, deployable.*

A stable release suitable for personal use and public showcase. Focused on packaging, deployment, and end-to-end user experience.

**Planned:**
- Docker Compose setup for one-command local deployment
- Environment configuration via `.env` with documented variables
- Full README with architecture deep-dive, design decisions, and limitations
- End-to-end demo video
- API key rotation and basic security hardening
- Evaluation suite: accuracy metrics for categorization and duplicate detection against labeled synthetic data

---

## Backlog / Future Considerations

Ideas that are out of scope for the current roadmap but worth tracking:

- **Crypto / Bitcoin mining income** — BTC transaction ingestion and cost basis tracking
- **Budget vs actual** — compare reconciled spending against a defined monthly budget
- **Web deployment** — hosted version with user accounts and cloud storage
- **Bank API integration** — replace manual statement imports with Plaid or open banking APIs
- **Export** — CSV/PDF export of reconciliation reports and insights