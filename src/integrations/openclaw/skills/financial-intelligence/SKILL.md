---
name: financial-intelligence
description: >
    Reconciles bank statements and receipts, analyzes spending patterns,
    flags suspicious activities, and detects duplicate transactions.
    Answers personal finance questions via MCP.
    Use when the user: uploads bank statements, CSVs, or receipts;
    asks about spending habits, expenses, or budgets; requests a
    spending breakdown for a date range; or asks any question about
    their financial data. Trigger terms: "reconcile", "spending",
    "expenses", "insights", "transactions", "budget".
version: 1.0.0
metadata:
    openclaw:
        requires:
            env:
                - FINANCIAL_AGENT_MCP_URL
        primaryEnv: FINANCIAL_AGENT_MCP_URL
        emoji: "💰"
---

# Financial Intelligence Agent

Personal finance assistant. Reconciles statements, detects duplicates, flags suspicious activities, provides financial insights and answers financial questions. All tools exposes via MCP.

## MCP Connection

The MCP server runs at the URL in `FINANCIAL_AGENT_MCP_URL` (default:
`http://localhost:8811/mcp`). Tools are discovered automatically via the
protocol. Do not hardcode tool schemas — call them as the MCP server
exposes them.

## Tools reference

| Tool                         | Purpose                                      |
|------------------------------|----------------------------------------------|
| `start_reconciliation`       | Upload files (base64), returns `run_id` + `thread_id` |
| `get_reconciliation_status`  | Poll by `run_id` + `thread_id` until complete |
| `run_insights`               | Analyze spending; optional `date_from`/`date_to` |
| `get_latest_insights`        | Return cached insights (no LLM cost)         |
| `chat`                       | Free-form financial Q&A; optional `conversation_id` |

## Workflow: Reconciliation

Trigger: the user uploads a bank statement, CSVs, receipt or says "reconcile".

1. Encode each uploaded file as `{ "filename": "<name>", "content_base64": "<base64>" }`.
2. Call `start_reconciliation` with the files array. Always set `auto_approve: true`.
3. Extract `run_id` and `thread_id` from the response.
4. Poll `get_reconciliation_status` with both values. Wait 5 seconds between calls.
5. Repeat step 4 until `status` is `"complete"`.
6. Present the summary: total transactions, matched count, categories found, and any flagged duplicates.
 
## Workflow: Insights

Trigger: user asks about spending, habits, patterns, or says "insights".
 
1. First try `get_latest_insights`. If it returns data, use it — no LLM cost.
2. If the cache returns an error or the user specifies a date range, call `run_insights`.
   - If the user mentions a specific month (e.g., "January"), pass `date_from` and `date_to` as ISO dates.
   - If no date range is mentioned, omit both parameters to analyze the full history.
3. Present results in three sections: spending breakdown (by category), habits, and suggestions.

## Workflow: Compute Insights

Trigger: user asks for new insights or says "recompute insights".

1. call `run_insights`.
   - If the user mentions a specific month (e.g., "January"), pass `date_from` and `date_to` as ISO dates.
   - If no date range is mentioned, omit both parameters to analyze the full history.
   - Otherwise use the date range specified by the user
2. Present results in three sections: spending breakdown (by category), habits, and suggestions.

## Workflow: Chat
 
Trigger: user asks a specific financial question (e.g., "what did I spend on food last month?").
 
1. Call `chat` with the user's message.
2. For follow-up questions in the same conversation, pass the same `conversation_id` to maintain context.
3. Return the agent's response directly.
 
## Workflow: Reconcile + Compute Insights
 
Trigger: user uploads statements AND asks for analysis in the same message
(e.g., "reconcile these and tell me what I spent").
 
1. Run the full Reconciliation workflow (steps 1–6 above).
2. Only after status is `"complete"`, run the Compute Insights workflow.
3. Present both the reconciliation summary and the insights together.
 
## Formatting
 
- Currency amounts: always include the currency symbol and two decimal places.
- Percentages: one decimal place.
- Category names: title case.
- When presenting insights, use a compact format suitable for messaging apps (Telegram, WhatsApp).
  Avoid markdown tables — use simple line-by-line formatting instead.
 
## Constraints
 
- Never fabricate financial data. If a tool returns an error, report it to the user.
- Never call `run_insights` if `get_latest_insights` returned valid, recent data — unless the user explicitly asks to refresh.
- Never expose `run_id`, `thread_id`, or `conversation_id` to the user. These are internal.
- If reconciliation takes more than 10 polling attempts, inform the user it is still processing and offer to check back later.
 