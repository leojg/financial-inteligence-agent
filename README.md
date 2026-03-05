# Finance Intelligence Agent

An AI agent for personal finance intelligence built on LangGraph. It ingests bank statements from multiple accounts, normalizes and categorizes transactions, detects duplicates and suspicious activity across sources, and produces structured reconciliation reports — with a human-in-the-loop review step throughout.

Built to demonstrate production-grade LLM orchestration patterns applied to real financial data problems.

**See [ROADMAP.md](ROADMAP.md) for version history, current status, and planned work.**

---

## Architecture

The agent is a directed LangGraph pipeline with an interrupt before the human review step:

![Agent Architecture](https://raw.githubusercontent.com/leojg/financial-inteligence-agent/refs/heads/master/static/financial_reconciliator_graph.PNG)


| Node | Responsibility |
|---|---|
| `ingest` | Loads PDF and XLSX files from a source folder |
| `normalize` | LLM extracts transactions into a canonical schema, infers account names |
| `convert_currency` | Fetches historical exchange rates and converts amounts to USD |
| `categorize` | LLM assigns a category to each transaction in batches of 50 |
| `detect_duplicates` | Tiered matching: exact → fuzzy → LLM reasoning |
| `flag_suspicious` | LLM identifies anomalous activity with natural language explanations |
| `human_review` | Interrupt — user confirms or rejects flagged transactions via the UI |
| `generate_report` | Produces a structured summary of the reconciliation run |

State is persisted across sessions via a LangGraph checkpointer, enabling the interrupt/resume flow.

---

## Stack

- **[LangGraph](https://github.com/langchain-ai/langgraph)** — agent orchestration and state management
- **[LangChain OpenAI](https://github.com/langchain-ai/langchain)** — LLM integration (GPT-4o-mini by default)
- **[Streamlit](https://streamlit.io)** — UI for running the agent and reviewing results
- **[LangGraph Studio](https://github.com/langchain-ai/langgraph-studio)** — visual graph debugging
- **[exchangerate.host](https://exchangerate.host)** — historical exchange rate API
- Python 3.10+, pandas, pydantic, pytest, ruff

---

## Setup

**1. Clone the repo**

```bash
git clone https://github.com/leojg/finance-intelligence-agent.git
cd finance-intelligence-agent
```

**2. Install dependencies**

```bash
pip install -e ".[dev]"
```

**3. Configure environment**

```bash
cp .env.example .env
```

Edit `.env` and add your keys:

```env
OPENAI_API_KEY=sk-...
EXCHANGE_RATE_API_KEY=...       # from exchangerate.host
LANGSMITH_API_KEY=...           # optional, for tracing
LANGSMITH_PROJECT=finance-intelligence-agent
```

**4. Prepare your statements**

Create a folder and drop your bank statements into it (PDF or XLSX):

```bash
mkdir -p data/statements
# copy your files into data/statements/
```

**Generate sample data (optional)**

To generate synthetic bank statements (3 accounts: Itaú, BROU, Wise) for testing:

```bash
python scripts/generate_samples.py
```

Output is written to `./data` (XLSX and PDF files). Use that folder path in the Streamlit sidebar when you run the agent.

---

## Usage

**Streamlit UI (recommended)**

```bash
streamlit run src/ui/app.py
```

1. Enter the path to your statements folder in the sidebar
2. Click **Run Agent**
3. Review flagged transactions in the **Review** tab
4. Click **Resume** to generate the final report

**LangGraph Studio**

```bash
langgraph dev
```

Opens the visual graph editor at `http://localhost:8123`. Useful for inspecting state at each node and debugging the pipeline.

---

## Project Structure

```
finance-intelligence-agent/
├── src/
│   ├── agent/
│   │   ├── graph.py            # LangGraph pipeline definition
│   │   ├── nodes.py            # Node implementations
│   │   ├── state.py            # ReconciliationState, Transaction schema
│   │   ├── configuration.py    # ReconciliationConfig (model, categories, currency)
│   │   ├── services/
│   │   │   └── exchange_service.py
│   │   └── utils/
│   │       └── parsers.py      # PDF and XLSX document loaders
│   └── ui/
│       └── app.py              # Streamlit UI
├── tests/
│   ├── unit_tests/
│   └── integration_tests/
├── data/
│   └── statements/             # drop your bank statement files here
├── langgraph.json
├── pyproject.toml
├── ROADMAP.md
└── README.md
```

---

## Configuration

`ReconciliationConfig` in `src/agent/configuration.py` controls the agent's behavior:

| Parameter | Default | Description |
|---|---|---|
| `model_name` | `gpt-4o-mini` | OpenAI model used for all LLM nodes |
| `temperature` | `0.0` | Deterministic outputs for consistency |
| `base_currency` | `USD` | Target currency for amount conversion |
| `categories` | *(see file)* | Transaction category taxonomy |

---

## Development

```bash
# Run unit tests
make test

# Lint and format
make lint
make format

# Spell check
make spell_check
```

---

## License

Apache 2.0 — see [LICENSE](LICENSE).
