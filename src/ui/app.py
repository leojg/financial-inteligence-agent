"""Streamlit UI for the Financial Reconciliation Agent.

Run with: streamlit run src/ui/app.py
"""

from typing import Any

from dotenv import load_dotenv

load_dotenv()

from pathlib import Path  # noqa: E402

import pandas as pd  # noqa: E402  # type: ignore
import streamlit as st  # noqa: E402

from agent.db import get_checkpointer  # noqa: E402

# --- Page config ---
st.set_page_config(
    page_title="Financial Reconciliation Agent",
    page_icon="🏦",
    layout="wide",
)

# --- Minimal custom styling ---
st.markdown("""
<style>
    .stTabs [data-baseweb="tab-list"] { gap: 8px; }
    .stTabs [data-baseweb="tab"] { padding: 8px 20px; }
    .metric-card {
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 16px;
        text-align: center;
    }
    .flag-suspicious { color: #dc2626; font-weight: 600; }
    .flag-duplicate  { color: #d97706; font-weight: 600; }
    .flag-review     { color: #2563eb; font-weight: 600; }
    .flag-ok         { color: #16a34a; }
</style>
""", unsafe_allow_html=True)


# ── Session state helpers ────────────────────────────────────────────────────


def _init_session() -> None:
    defaults: dict[str, Any] = {
        "graph_state": None,       # last LangGraph state snapshot
        "graph_instance": None,    # compiled graph (cached)
        "thread_id": "reconciliation-1",
        "interrupted": False,      # waiting at human_review node
        "review_decisions": {},    # {transaction_id: "confirmed" | "rejected"}
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _get_graph() -> Any:
    """Return the compiled graph instance, creating it with checkpointer if needed."""
    if st.session_state.graph_instance is None:
        from agent.configuration import DEFAULT_CONFIG
        from agent.graph import make_graph

        if "checkpointer" not in st.session_state:
            st.session_state.checkpointer = get_checkpointer()

        st.session_state.graph_instance = make_graph(
            DEFAULT_CONFIG,
            checkpointer=st.session_state.checkpointer
        )
    return st.session_state.graph_instance


def _transactions_df(transactions: list[Any]) -> pd.DataFrame:
    """Convert transaction list (dicts or Pydantic) to a display DataFrame."""
    rows = []
    for t in transactions:
        d = t if isinstance(t, dict) else t.model_dump()
        rows.append({
            "ID":             d.get("id", "")[:8] + "…",
            "Date":           d.get("date", ""),
            "Merchant":       d.get("merchant", ""),
            "Amount":         d.get("amount_original", 0),
            "Currency":       d.get("currency", ""),
            "Amount (USD)":   d.get("amount_base"),
            "Account":        d.get("account", ""),
            "Category":       d.get("category") or "—",
            "Duplicate":      "⚠️" if d.get("duplicate_of") else "",
            "Suspicious":     "🚨" if d.get("suspicious") else "",
            "Needs Review":   "🔵" if d.get("needs_review") else "",
            "_id":            d.get("id", ""),   # hidden, used for review
        })
    return pd.DataFrame(rows)


# ── Sidebar ──────────────────────────────────────────────────────────────────


def render_sidebar() -> None:
    """Render the sidebar with run controls and thread selector."""
    with st.sidebar:
        st.title("🏦 Reconciliation")
        st.markdown("---")

        st.subheader("Input")
        folder = st.text_input(
            "Statements folder",
            placeholder="/path/to/bank/statements",
            help="Folder containing PDF and XLSX bank statements"
        )

        run_disabled = not folder or st.session_state.interrupted
        if st.button("▶ Run Agent", disabled=run_disabled, use_container_width=True, type="primary"):
            _run_graph(folder.strip())

        if st.session_state.interrupted:
            st.info("⏸ Waiting for your review.\nConfirm or reject items in the Review tab, then click Resume.")
            if st.button("▶ Resume", use_container_width=True, type="primary"):
                _resume_graph()

        if st.session_state.graph_state:
            st.markdown("---")
            st.subheader("Summary")
            state = st.session_state.graph_state
            txns = state.get("transactions", [])
            dups = state.get("duplicates", [])
            sus  = state.get("suspicious", [])
            needs = [t for t in txns if (t if isinstance(t, dict) else t.model_dump()).get("needs_review")]
            st.metric("Transactions", len(txns))
            st.metric("Duplicates",   len(dups))
            st.metric("Suspicious",   len(sus))
            st.metric("Needs Review", len(needs))

        st.markdown("---")
        st.caption("Financial Reconciliation Agent · AI Showcases")


# ── Graph execution ──────────────────────────────────────────────────────────


def _run_graph(source_folder: str) -> None:
    if not Path(source_folder).is_dir():
        st.error(f"Folder not found: {source_folder}")
        return

    from agent.state import initial_state

    graph = _get_graph()
    config = {"configurable": {"thread_id": st.session_state.thread_id}}

    with st.spinner("Running reconciliation agent…"):
        try:
            result = graph.invoke(initial_state(source_folder), config=config)
            st.session_state.graph_state = result

            # Check if interrupted at human_review
            snapshot = graph.get_state(config)
            if snapshot.next and "human_review" in snapshot.next:
                st.session_state.interrupted = True
            else:
                st.session_state.interrupted = False

            st.rerun()
        except Exception as e:
            st.error(f"Agent error: {e}")


def _resume_graph() -> None:
    graph  = _get_graph()
    config = {"configurable": {"thread_id": st.session_state.thread_id}}

    # Apply review decisions to state
    decisions = st.session_state.review_decisions
    if decisions:
        snapshot = graph.get_state(config)
        current_txns = snapshot.values.get("transactions", [])
        updated_txns = []
        for t in current_txns:
            d = t if isinstance(t, dict) else t.model_dump()
            tid = d.get("id", "")
            if tid in decisions:
                d["review_status"] = decisions[tid]
            updated_txns.append(d)
        graph.update_state(config, {"transactions": updated_txns}, as_node="human_review")

    with st.spinner("Resuming agent…"):
        try:
            result = graph.invoke(None, config=config)
            st.session_state.graph_state = result
            st.session_state.interrupted = False
            st.session_state.review_decisions = {}
            st.rerun()
        except Exception as e:
            st.error(f"Resume error: {e}")


# ── Tabs ─────────────────────────────────────────────────────────────────────


def render_main() -> None:
    """Render the main tab content (overview, transactions, duplicates, suspicious)."""
    state = st.session_state.graph_state

    if state is None:
        st.markdown("## Financial Reconciliation Agent")
        st.markdown("Enter a folder path in the sidebar and click **Run Agent** to start.")
        return

    tab_txn, tab_dup, tab_sus, tab_review, tab_report = st.tabs([
        "📋 Transactions",
        "⚠️ Duplicates",
        "🚨 Suspicious",
        "🔵 Review",
        "📄 Report",
    ])

    transactions = state.get("transactions", [])
    duplicates   = state.get("duplicates", [])
    suspicious   = state.get("suspicious", [])

    # ── Transactions tab ──────────────────────────────────────────────────
    with tab_txn:
        st.subheader(f"All Transactions ({len(transactions)})")
        if transactions:
            df = _transactions_df(transactions)

            # Filters
            col1, col2, col3 = st.columns(3)
            with col1:
                accounts = ["All"] + sorted(df["Account"].unique().tolist())
                account_filter = st.selectbox("Account", accounts)
            with col2:
                categories = ["All"] + sorted(df["Category"].unique().tolist())
                cat_filter = st.selectbox("Category", categories)
            with col3:
                flag_filter = st.selectbox("Flag", ["All", "Duplicate", "Suspicious", "Needs Review", "Clean"])

            filtered = df.copy()
            if account_filter != "All":
                filtered = filtered[filtered["Account"] == account_filter]
            if cat_filter != "All":
                filtered = filtered[filtered["Category"] == cat_filter]
            if flag_filter == "Duplicate":
                filtered = filtered[filtered["Duplicate"] == "⚠️"]
            elif flag_filter == "Suspicious":
                filtered = filtered[filtered["Suspicious"] == "🚨"]
            elif flag_filter == "Needs Review":
                filtered = filtered[filtered["Needs Review"] == "🔵"]
            elif flag_filter == "Clean":
                filtered = filtered[
                    (filtered["Duplicate"] == "") &
                    (filtered["Suspicious"] == "") &
                    (filtered["Needs Review"] == "")
                ]

            st.dataframe(
                filtered.drop(columns=["_id"]),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No transactions loaded yet.")

    # ── Duplicates tab ────────────────────────────────────────────────────
    with tab_dup:
        st.subheader(f"Duplicate Transactions ({len(duplicates) // 2} pairs)")
        if duplicates:
            df = _transactions_df(duplicates)
            st.dataframe(df.drop(columns=["_id"]), use_container_width=True, hide_index=True)
        else:
            st.success("No duplicates detected.")

    # ── Suspicious tab ────────────────────────────────────────────────────
    with tab_sus:
        st.subheader(f"Suspicious Transactions ({len(suspicious)})")
        if suspicious:
            for t in suspicious:
                d = t if isinstance(t, dict) else t.model_dump()
                with st.expander(f"🚨 {d.get('date')} · {d.get('merchant')} · {d.get('amount_original')} {d.get('currency')}"):
                    st.markdown(f"**Account:** {d.get('account')}")
                    st.markdown(f"**Category:** {d.get('category') or '—'}")
                    st.markdown(f"**Reason:** {d.get('suspicious_reason') or '—'}")
        else:
            st.success("No suspicious transactions detected.")

    # ── Review tab ────────────────────────────────────────────────────────
    with tab_review:
        st.subheader("Human Review")

        if not st.session_state.interrupted:
            st.info("No pending review. Run the agent to generate results.")
        else:
            needs_review = [
                t for t in transactions
                if (t if isinstance(t, dict) else t.model_dump()).get("needs_review")
            ]

            if not needs_review:
                st.success("Nothing requires review.")
            else:
                st.markdown(f"**{len(needs_review)} transactions need your review.** Check the ones you want to confirm, leave unchecked to reject.")
                st.markdown("---")

                decisions = st.session_state.review_decisions.copy()

                for t in needs_review:
                    d = t if isinstance(t, dict) else t.model_dump()
                    tid = d.get("id", "")
                    label = f"{d.get('date')} · {d.get('merchant')} · {d.get('amount_original')} {d.get('currency')} · {d.get('account')}"
                    reason = d.get("review_reason") or d.get("suspicious_reason") or "—"

                    col1, col2 = st.columns([0.05, 0.95])
                    with col1:
                        checked = st.checkbox(
                            "Confirm",
                            key=f"review_{tid}",
                            value=decisions.get(tid) == "confirmed",
                            label_visibility="hidden"
                        )
                    with col2:
                        st.markdown(f"**{label}**")
                        st.caption(f"Reason: {reason}")

                    decisions[tid] = "confirmed" if checked else "rejected"

                st.session_state.review_decisions = decisions
                st.markdown("---")
                st.caption("Click **Resume** in the sidebar to continue after reviewing.")

    # ── Report tab ────────────────────────────────────────────────────────
    with tab_report:
        st.subheader("Reconciliation Report")
        report = state.get("report")
        if report:
            st.text(report)

            st.download_button(
                label="⬇ Download Report",
                data=report,
                file_name="reconciliation_report.txt",
                mime="text/plain"
            )
        else:
            if st.session_state.interrupted:
                st.info("Complete the review and resume the agent to generate the report.")
            else:
                st.info("Report will appear here after the agent completes.")


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    """Initialize session, render sidebar and main content."""
    _init_session()
    render_sidebar()
    render_main()


if __name__ == "__main__":
    main()