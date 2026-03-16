"""Streamlit UI for the Financial Reconciliation Agent.

Run with: streamlit run src/ui/app.py

Requires the project to be installed (pip install -e .) so the agent package is importable.
"""

import datetime
import html
import json
import tempfile
from pathlib import Path
from typing import Any

import uuid
from dotenv import load_dotenv

load_dotenv()

# noqa: E402 for imports below

import pandas as pd  # type: ignore[import-untyped]  # noqa: E402
import streamlit as st  # noqa: E402

from agents.reconciliator.configuration import DEFAULT_CONFIG  # noqa: E402
from shared.db import get_checkpointer, run_migrations  # noqa: E402
from shared.services.database_service import DatabaseService  # noqa: E402

run_migrations()

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
    .severity-critical { color: #dc2626; font-weight: 600; }
    .severity-warning  { color: #d97706; font-weight: 600; }
    .severity-info     { color: #2563eb; }
</style>
""", unsafe_allow_html=True)


# ── Session state helpers ────────────────────────────────────────────────────


def _init_session() -> None:
    defaults: dict[str, Any] = {
        "graph_state": None,       # last LangGraph state snapshot
        "graph_instance": None,    # compiled graph (cached)
        "thread_id": str(uuid.uuid4()),
        "interrupted": False,      # waiting at review_low_confidence or human_review
        "interrupt_at": None,      # "review_low_confidence_transactions" | "human_review"
        "low_confidence_decisions": [],  # for review_low_confidence_transactions resume
        "low_confidence_review_txn": None,  # transaction dict when modal/panel is open
        "review_decisions": {},    # {transaction_id: "confirmed" | "rejected"}
        "source_paths": [],        # list of paths (temp paths for uploads or user paths)
        "upload_dir": None,        # temp dir for uploaded files (created on first upload)
        "past_runs_selected_run_id": None,  # run_id for drill-down in Past runs
        "pending_resume": False,  # True when Resume was clicked; agent will run this cycle, button disabled
        "insights_state": None,       # last insights result (aggregations, habits, suggestions, date_from, date_to)
        "insights_graph_instance": None,  # cached compiled insights graph
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _ensure_upload_dir() -> Path:
    """Create and return a session-scoped temp dir for uploaded files."""
    if st.session_state.upload_dir is None:
        st.session_state.upload_dir = Path(tempfile.mkdtemp(prefix="finance_agent_uploads_"))
    return Path(st.session_state.upload_dir)


def _process_uploads(uploaded_files: list[Any]) -> list[str]:
    """Save uploaded files to temp dir and return their paths. Dedupe by name."""
    if not uploaded_files:
        return []
    upload_dir = _ensure_upload_dir()
    # Names already in source_paths under our upload dir (use resolve() for consistent comparison)
    try:
        upload_dir_resolved = upload_dir.resolve()
        existing_names = {
            Path(p).name for p in st.session_state.source_paths
            if Path(p).resolve().parent == upload_dir_resolved
        }
    except Exception:
        existing_names = set()
    new_paths: list[str] = []
    for f in uploaded_files:
        name = f.name or "unnamed"
        if name in existing_names:
            continue
        path = upload_dir / name
        try:
            path.write_bytes(f.getvalue())
        except Exception:
            continue
        new_paths.append(str(path.resolve()))
        existing_names.add(name)
    return new_paths


def _get_reconciliation_graph() -> Any:
    """Return the compiled reconciliation graph instance, creating it with checkpointer if needed."""
    if st.session_state.graph_instance is None:
        from agents.reconciliator.configuration import DEFAULT_CONFIG
        from agents.reconciliator.graph import make_graph

        if "checkpointer" not in st.session_state:
            st.session_state.checkpointer = get_checkpointer()

        st.session_state.graph_instance = make_graph(
            DEFAULT_CONFIG,
            checkpointer=st.session_state.checkpointer
        )
    return st.session_state.graph_instance


def _get_insights_graph() -> Any:
    """Return the compiled insights graph instance, creating and caching it if needed."""
    if st.session_state.insights_graph_instance is None:
        from agents.insights.graph import make_graph

        st.session_state.insights_graph_instance = make_graph(checkpointer=None)
    return st.session_state.insights_graph_instance


def _run_insights_graph(
    date_from: datetime.date | None,
    date_to: datetime.date | None,
    accounts: list[str] | None,
    force_recompute: bool,
) -> None:
    """Run the insights graph and store the result in session state."""
    from agents.insights.state import initial_insights_state

    db = DatabaseService()
    if db.get_transaction_date_range(accounts) is None:
        st.error("No transactions found in the database. Run a reconciliation first.")
        return

    graph = _get_insights_graph()
    config = {"configurable": {"thread_id": "insights-1"}}
    date_from_str = str(date_from) if date_from else None
    date_to_str = str(date_to) if date_to else None
    initial = initial_insights_state(
        date_from=date_from_str,
        date_to=date_to_str,
        accounts=accounts or None,
        force_recompute=force_recompute,
    )
    with st.spinner("Running insights agent…"):
        try:
            result = graph.invoke(initial, config=config)
            st.session_state.insights_state = {
                "date_from": result.get("date_from"),
                "date_to": result.get("date_to"),
                "aggregations": result.get("aggregations"),
                "habits": result.get("habits") or [],
                "suggestions": result.get("suggestions") or [],
            }
            st.rerun()
        except Exception as e:
            st.error(f"Insights agent error: {e}")


def _parse_source_paths(raw: Any) -> list[str]:
    """Parse source_paths from DB (stored as JSON string). Return list of file names for display."""
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            paths = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return [raw]
        return [Path(p).name for p in paths] if isinstance(paths, list) else []
    if isinstance(raw, list):
        return [Path(p).name if isinstance(p, str) else str(p) for p in raw]
    return []


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
        uploaded = st.file_uploader(
            "Drop files here or browse",
            type=["pdf", "xlsx", "xls", "csv", "png", "jpg", "jpeg"],
            accept_multiple_files=True,
            help="Upload PDF, Excel, CSV, or image files. Drag and drop or click to open the file picker. For a folder, select multiple files from it at once.",
        )
        if uploaded:
            new_paths = _process_uploads(uploaded)
            if new_paths:
                existing = set(st.session_state.source_paths)
                added = [p for p in new_paths if p not in existing]
                if added:
                    st.session_state.source_paths = st.session_state.source_paths + added
                # Don't rerun here: same run will see updated source_paths and enable the button

        with st.expander("Add folder path (local only)"):
            path_input = st.text_input(
                "Folder or file path",
                placeholder="/path/to/folder or /path/to/file.pdf",
                key="path_input",
                label_visibility="collapsed",
            )
            if st.button("➕ Add path", key="add_path_btn"):
                p = (path_input or "").strip()
                if p and p not in st.session_state.source_paths:
                    st.session_state.source_paths = st.session_state.source_paths + [p]
                    st.rerun()
                elif p and p in st.session_state.source_paths:
                    st.warning("Already in the list.")

        st.subheader("Sources")
        if not st.session_state.source_paths:
            st.caption("No files yet. Drop files above or use the file picker.")
        else:
            upload_dir = Path(st.session_state.upload_dir) if st.session_state.upload_dir else None
            for i, path in enumerate(st.session_state.source_paths):
                col_path, col_rm = st.columns([1, 0.15])
                with col_path:
                    path_obj = Path(path)
                    label = path_obj.name if (upload_dir and path_obj.parent == upload_dir) else path
                    st.text(label)
                with col_rm:
                    if st.button("🗑", key=f"remove_{i}", help="Remove"):
                        st.session_state.source_paths = [
                            x for j, x in enumerate(st.session_state.source_paths) if j != i
                        ]
                        st.rerun()

        run_disabled = not st.session_state.source_paths or st.session_state.interrupted
        if st.button("▶ Run Agent", disabled=run_disabled, use_container_width=True, type="primary"):
            _run_graph(st.session_state.source_paths)

        if st.session_state.interrupted:
            st.info("⏸ Waiting for your review.\nConfirm or reject items in the Review tab, then click Resume.")
            resume_disabled = st.session_state.get("pending_resume", False)
            if st.button(
                "▶ Resume",
                disabled=resume_disabled,
                use_container_width=True,
                type="primary",
                key="btn_resume",
            ):
                st.session_state.pending_resume = True
                st.rerun()

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


def _run_graph(source_paths: list[str]) -> None:
    if not source_paths:
        st.error("Add at least one folder or file.")
        return

    from agents.reconciliator.state import initial_state

    graph = _get_reconciliation_graph()
    config = {"configurable": {"thread_id": st.session_state.thread_id}}

    with st.spinner("Running reconciliation agent…"):
        try:
            graph.invoke(initial_state(source_paths), config=config)

            # Use checkpoint state for UI (invoke return value may differ when interrupted)
            snapshot = graph.get_state(config)
            state_values = getattr(snapshot, "values", None)
            st.session_state.graph_state = dict(state_values) if state_values is not None else {}

            # Detect interrupt: next node is review_low_confidence or human_review
            next_nodes = getattr(snapshot, "next", None) or ()
            st.session_state.interrupted = (
                "review_low_confidence_transactions" in next_nodes
                or "human_review" in next_nodes
            )
            st.session_state.interrupt_at = (
                next_nodes[0] if next_nodes else None
            )

            st.rerun()
        except Exception as e:
            st.error(f"Agent error: {e}")


def _resume_graph() -> None:
    graph = _get_reconciliation_graph()
    config = {"configurable": {"thread_id": st.session_state.thread_id}}
    snapshot = graph.get_state(config)
    next_nodes = getattr(snapshot, "next", None) or ()

    resume_input = None

    if "review_low_confidence_transactions" in next_nodes:
        decisions = st.session_state.get("low_confidence_decisions") or []
        graph.update_state(config, {"low_confidence_decisions": decisions}, as_node="review_low_confidence_transactions")

    elif "human_review" in next_nodes:
        decisions = st.session_state.review_decisions
        if decisions:
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
            if resume_input is not None:
                graph.invoke(resume_input, config=config)
            else:
                graph.invoke(None, config=config)
            snapshot = graph.get_state(config)
            state_values = getattr(snapshot, "values", None)
            st.session_state.graph_state = dict(state_values) if state_values is not None else {}

            # The graph may have hit another interrupt_before node (e.g. resumed from
            # review_low_confidence_transactions and now paused at human_review).
            next_nodes = getattr(snapshot, "next", None) or ()
            still_interrupted = (
                "review_low_confidence_transactions" in next_nodes
                or "human_review" in next_nodes
            )
            st.session_state.interrupted = still_interrupted
            st.session_state.interrupt_at = next_nodes[0] if next_nodes else None
            st.session_state.pending_resume = False
            if not still_interrupted:
                st.session_state.review_decisions = {}
                st.session_state.low_confidence_decisions = []
                st.session_state.low_confidence_review_txn = None
            st.rerun()
        except Exception as e:
            st.session_state.pending_resume = False
            st.error(f"Resume error: {e}")


# ── Low-confidence review panel (image + form) ────────────────────────────────


def _is_image_path(path: str) -> bool:
    p = Path(path)
    return p.suffix.lower() in (".png", ".jpg", ".jpeg")


def _render_low_confidence_review_panel(
    txn: dict[str, Any],
    image_path: str,
) -> None:
    """Render a panel with image on one side and editable transaction form on the other (Save / Dismiss)."""
    st.markdown("---")
    st.subheader("✏️ Review transaction")
    col_img, col_form = st.columns([1, 1])

    with col_img:
        if _is_image_path(image_path) and Path(image_path).is_file():
            st.image(image_path, use_container_width=True)
        else:
            st.caption(f"Source: `{image_path}`")
            st.info("Preview not available for this file type.")

    with col_form:
        with st.form("low_confidence_edit_form"):
            date = st.text_input("Date", value=txn.get("date", ""), key="lc_date")
            amount = st.number_input(
                "Amount",
                value=float(txn.get("amount_original", 0) or 0),
                step=0.01,
                format="%.2f",
                key="lc_amount",
            )
            currency = st.text_input("Currency", value=txn.get("currency", ""), key="lc_currency")
            merchant = st.text_input("Merchant", value=txn.get("merchant", ""), key="lc_merchant")
            account = st.text_input("Account", value=txn.get("account", ""), key="lc_account")

            col_save, col_dismiss, col_back, _ = st.columns([1, 1, 1, 1])
            with col_save:
                save_clicked = st.form_submit_button("Save")
            with col_dismiss:
                dismiss_clicked = st.form_submit_button("Dismiss")
            with col_back:
                back_clicked = st.form_submit_button("Back")

        if back_clicked:
            st.session_state.low_confidence_review_txn = None
            st.rerun()
        if save_clicked:
            updated = dict(txn)
            updated["date"] = date
            updated["amount_original"] = amount
            updated["currency"] = currency
            updated["merchant"] = merchant
            updated["account"] = account
            decisions = st.session_state.get("low_confidence_decisions") or []
            by_id = {d["id"]: d for d in decisions if isinstance(d, dict) and d.get("id")}
            by_id[txn.get("id", "")] = {"id": txn.get("id"), "action": "edit", "transaction": updated}
            st.session_state.low_confidence_decisions = list(by_id.values())
            st.session_state.low_confidence_review_txn = None
            st.success("Transaction updated. You can review more or click **Resume** in the sidebar.")
            st.rerun()
        if dismiss_clicked:
            tid = txn.get("id", "")
            decisions = st.session_state.get("low_confidence_decisions") or []
            by_id = {d["id"]: d for d in decisions if isinstance(d, dict) and d.get("id")}
            by_id[tid] = {"id": tid, "action": "dismiss"}
            st.session_state.low_confidence_decisions = list(by_id.values())
            st.session_state.low_confidence_review_txn = None
            st.info("Transaction dismissed. You can review more or click **Resume** in the sidebar.")
            st.rerun()


# ── Past runs (from DB) ───────────────────────────────────────────────────────


@st.cache_data(ttl=60)
def _cached_filter_values() -> dict[str, list[str]]:
    """Return distinct accounts and categories from DB (cached 60 s)."""
    return DatabaseService().get_distinct_filter_values()


def render_past_runs() -> None:
    """Show past reconciliation runs from DB with summaries and drill-down to transactions."""
    db = DatabaseService()
    runs = db.get_runs()

    if not runs:
        st.info("No past runs yet. Run a reconciliation from the **Run reconciliation** tab.")
        return

    selected_run_id = st.session_state.get("past_runs_selected_run_id")

    if selected_run_id:
        # Drill-down: show transactions for the selected run
        run = next((r for r in runs if r.get("run_id") == selected_run_id), None)
        if run:
            if st.button("← Back to runs", key="past_runs_back"):
                st.session_state.past_runs_selected_run_id = None
                st.rerun()
            st.markdown("---")
            _render_run_detail(run, db)
        else:
            st.session_state.past_runs_selected_run_id = None
            st.rerun()
        return

    st.subheader("Past reconciliation runs")
    st.caption("Click **View** to see transactions for a run.")

    # ── Cross-run search ──────────────────────────────────────────────────
    filter_vals = _cached_filter_values()
    with st.expander("🔍 Search transactions across all runs"):
        sc1, sc2, sc3, sc4 = st.columns(4)
        with sc1:
            s_date_from = st.date_input("Date from", value=None, key="s_date_from")
            s_date_to = st.date_input("Date to", value=None, key="s_date_to")
        with sc2:
            s_accounts = st.multiselect("Account", filter_vals.get("accounts", []), key="s_accounts")
        with sc3:
            s_categories = st.multiselect("Category", filter_vals.get("categories", []), key="s_categories")
        with sc4:
            s_amt_min = st.number_input("Amount min", value=0.0, min_value=0.0, step=1.0, key="s_amt_min")
            s_amt_max = st.number_input("Amount max", value=0.0, min_value=0.0, step=1.0, key="s_amt_max")

        if st.button("Search", key="s_search", type="primary"):
            results = db.query_transactions(
                date_from=str(s_date_from) if s_date_from else None,
                date_to=str(s_date_to) if s_date_to else None,
                accounts=s_accounts or None,
                categories=s_categories or None,
                amount_min=s_amt_min if s_amt_min > 0 else None,
                amount_max=s_amt_max if s_amt_max > 0 else None,
            )
            st.session_state["search_results"] = results

        search_results = st.session_state.get("search_results")
        if search_results is not None:
            st.caption(f"Results: {len(search_results)} transaction(s)")
            if search_results:
                st.dataframe(
                    _transactions_df(search_results).drop(columns=["_id"]),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.info("No transactions match the selected filters.")

    for r in runs:
        run_id = r.get("run_id", "")
        created = r.get("created_at") or ""
        completed = r.get("completed_at") or ""
        status = r.get("status", "")
        total_txn = r.get("total_transactions") or 0
        total_dup = r.get("total_duplicates") or 0
        total_sus = r.get("total_suspicious") or 0
        base_ccy = r.get("base_currency") or "USD"
        source_paths = _parse_source_paths(r.get("source_paths"))

        with st.container():
            col1, col2 = st.columns([1, 0.2])
            with col1:
                st.markdown(f"**Run** `{run_id[:12]}…` · **{status}**")
                st.caption(f"Started: {created}")
                if completed:
                    st.caption(f"Completed: {completed}")
                if source_paths:
                    st.caption(f"Sources: {', '.join(source_paths[:5])}{'…' if len(source_paths) > 5 else ''}")
                st.markdown(
                    f"Transactions: **{total_txn}** · Duplicates: **{total_dup}** · "
                    f"Suspicious: **{total_sus}** · {base_ccy}"
                )
            with col2:
                if st.button("View", key=f"view_run_{run_id}", type="primary"):
                    st.session_state.past_runs_selected_run_id = run_id
                    st.rerun()
            st.markdown("---")


def _render_run_detail(run: dict[str, Any], db: DatabaseService) -> None:
    """Render summary and transactions table for a single run."""
    run_id = run.get("run_id", "")
    st.subheader(f"Run: {run_id[:16]}…")
    created = run.get("created_at") or ""
    status = run.get("status", "")
    total_txn = run.get("total_transactions") or 0
    total_dup = run.get("total_duplicates") or 0
    total_sus = run.get("total_suspicious") or 0
    source_paths = _parse_source_paths(run.get("source_paths"))

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Transactions", total_txn)
    m2.metric("Duplicates", total_dup)
    m3.metric("Suspicious", total_sus)
    m4.metric("Status", status)
    st.caption(f"Started: {created}")
    if source_paths:
        st.caption(f"Sources: {', '.join(source_paths)}")

    transactions = db.get_run_transactions(run_id)
    if not transactions:
        st.info("No transactions stored for this run.")
        return

    df = _transactions_df(transactions)

    # ── In-memory filters ─────────────────────────────────────────────────
    all_accounts = sorted({t.get("account", "") for t in transactions if t.get("account")})
    all_categories = sorted({t.get("category", "") for t in transactions if t.get("category")})
    all_dates = [t.get("date", "") for t in transactions if t.get("date")]
    default_date_min = datetime.date.fromisoformat(min(all_dates)) if all_dates else datetime.date.today()
    default_date_max = datetime.date.fromisoformat(max(all_dates)) if all_dates else datetime.date.today()
    amounts = [t.get("amount_original", 0) for t in transactions]
    default_amount_max = float(max(amounts)) if amounts else 0.0

    fc1, fc2, fc3, fc4 = st.columns(4)
    with fc1:
        date_from = st.date_input("Date from", value=default_date_min, key=f"rdf_{run_id}")
        date_to = st.date_input("Date to", value=default_date_max, key=f"rdt_{run_id}")
    with fc2:
        acct_filter = st.multiselect("Account", all_accounts, key=f"rac_{run_id}")
    with fc3:
        cat_filter = st.multiselect("Category", all_categories, key=f"rca_{run_id}")
    with fc4:
        amt_min = st.number_input("Amount min", value=0.0, min_value=0.0, step=1.0, key=f"ramin_{run_id}")
        amt_max = st.number_input("Amount max", value=default_amount_max, min_value=0.0, step=1.0, key=f"ramax_{run_id}")

    filtered = df.copy()
    filtered = filtered[filtered["Date"] >= str(date_from)]
    filtered = filtered[filtered["Date"] <= str(date_to)]
    if acct_filter:
        filtered = filtered[filtered["Account"].isin(acct_filter)]
    if cat_filter:
        filtered = filtered[filtered["Category"].isin(cat_filter)]
    filtered = filtered[filtered["Amount"] >= amt_min]
    if amt_max > 0:
        filtered = filtered[filtered["Amount"] <= amt_max]

    st.subheader(f"Transactions ({len(filtered)} / {len(df)})")
    st.dataframe(filtered.drop(columns=["_id"]), use_container_width=True, hide_index=True)


# ── Tabs ─────────────────────────────────────────────────────────────────────


def render_main() -> None:
    """Render the main tab content (overview, transactions, duplicates, suspicious)."""
    state = st.session_state.graph_state

    if state is None:
        st.markdown("## Financial Reconciliation Agent")
        st.markdown("Drop files or use the file picker in the sidebar, then click **Run Agent** to start.")
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
        elif st.session_state.get("interrupt_at") == "review_low_confidence_transactions":
            threshold = getattr(DEFAULT_CONFIG, "image_low_confidence_threshold", 0.95)
            low_conf: list[dict[str, Any]] = []
            for t in transactions:
                d = t if isinstance(t, dict) else (t.model_dump() if hasattr(t, "model_dump") else {})
                conf = d.get("confidence") if isinstance(d, dict) else None
                if conf is not None and conf < threshold:
                    low_conf.append(d)

            # Deduplicate by id so each transaction has exactly one Review button (avoids duplicate key)
            seen_lc_ids: set[str] = set()
            unique_low_conf: list[dict[str, Any]] = []
            for d in low_conf:
                tid = d.get("id", "") if isinstance(d, dict) else ""
                if tid and tid not in seen_lc_ids:
                    seen_lc_ids.add(tid)
                    unique_low_conf.append(d if isinstance(d, dict) else (d.model_dump() if hasattr(d, "model_dump") else {}))

            if unique_low_conf:
                # If a transaction is open for review, show the panel first
                review_txn = st.session_state.get("low_confidence_review_txn")
                if review_txn:
                    _render_low_confidence_review_panel(
                        review_txn,
                        review_txn.get("source_file", ""),
                    )

                st.markdown("**Low-confidence transactions** — click **Review** to edit or dismiss.")
                decisions_by_id = {
                    d["id"]: d for d in (st.session_state.get("low_confidence_decisions") or [])
                    if isinstance(d, dict) and d.get("id")
                }
                for i, t in enumerate(unique_low_conf):
                    d = t if isinstance(t, dict) else t
                    tid = d.get("id", "")
                    action = decisions_by_id.get(tid, {}).get("action")
                    label = f"{d.get('date')} · {d.get('merchant')} · {d.get('amount_original')} {d.get('currency')}"
                    col1, col2 = st.columns([0.85, 0.15])
                    with col1:
                        st.markdown(f"**{label}**")
                        if action:
                            st.caption(f"→ {action}")
                    with col2:
                        if st.button("Review", key=f"lc_review_{i}_{tid}", type="primary"):
                            st.session_state.low_confidence_review_txn = d
                            st.rerun()
                st.markdown("---")
                st.caption("Click **Resume** in the sidebar when done to continue the agent.")
            else:
                st.success("No low-confidence transactions to review.")
                st.caption("Click **Resume** in the sidebar to continue.")
        else:
            needs_review = [
                t for t in transactions
                if (t if isinstance(t, dict) else t.model_dump()).get("needs_review")
            ]
            suspicious_txns = [
                t for t in transactions
                if (t if isinstance(t, dict) else t.model_dump()).get("suspicious")
            ]
            from_duplicates = duplicates or []
            from_suspicious = suspicious or []
            seen_ids: set[str] = set()
            unique_needs_review: list[Any] = []
            for t in needs_review + suspicious_txns + from_duplicates + from_suspicious:
                d = t if isinstance(t, dict) else t.model_dump()
                tid = d.get("id", "")
                if tid and tid not in seen_ids:
                    seen_ids.add(tid)
                    unique_needs_review.append(t)

            if not unique_needs_review:
                st.success("Nothing requires review.")
            else:
                st.markdown(f"**{len(unique_needs_review)} transactions need your review.** Check the ones you want to confirm, leave unchecked to reject.")
                st.markdown("---")

                decisions = st.session_state.review_decisions.copy()

                for i, t in enumerate(unique_needs_review):
                    d = t if isinstance(t, dict) else t.model_dump()
                    tid = d.get("id", "")
                    label = f"{d.get('date')} · {d.get('merchant')} · {d.get('amount_original')} {d.get('currency')} · {d.get('account')}"
                    reason = d.get("review_reason") or d.get("suspicious_reason") or "—"

                    col1, col2 = st.columns([0.05, 0.95])
                    with col1:
                        checked = st.checkbox(
                            "Confirm",
                            key=f"review_{i}_{tid}",
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


# ── Insights tab ─────────────────────────────────────────────────────────────

_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


def _format_currency(value: float | None) -> str:
    """Format a number as USD for display."""
    return f"${value:,.2f}" if value is not None else "—"


def _as_list_of_dicts(value: Any) -> list[dict[str, Any]]:
    """Normalize a value to a list of dicts (for habits, suggestions, month_deltas, recurring_charges)."""
    if value is None:
        return []
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if isinstance(value, dict):
        return [x for x in value.values() if isinstance(x, dict)]
    return []


def _as_dict(value: Any) -> dict[str, Any]:
    """Normalize a value to a dict (for aggregations, transfer_fees_summary when not a list)."""
    if value is None:
        return {}
    return value if isinstance(value, dict) else {}


def _spending_by_category_pairs(spending: Any) -> list[tuple[str, float]]:
    """Return (category, total) pairs whether spending is a dict or a list."""
    if not spending:
        return []
    if isinstance(spending, dict):
        return [(str(k), float(v)) for k, v in spending.items()]
    if isinstance(spending, list):
        pairs: list[tuple[str, float]] = []
        for item in spending:
            if isinstance(item, dict):
                cat = item.get("category") or item.get("name") or ""
                tot = item.get("total") or item.get("amount") or 0
                pairs.append((str(cat), float(tot)))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                pairs.append((str(item[0]), float(item[1])))
        return pairs
    return []


def _render_statistics(aggregations: dict[str, Any]) -> None:
    """Render aggregations with titles and lists (no raw JSON)."""
    aggregations = _as_dict(aggregations)
    # Spending by category: dict[category -> total] or list of same
    spending_raw = aggregations.get("spending_by_category")
    spending_pairs = _spending_by_category_pairs(spending_raw)
    st.markdown("**Spending by category**")
    if not spending_pairs:
        st.caption("No spending by category.")
    else:
        spending_pairs.sort(key=lambda x: -x[1])
        items = [f"- **{html.escape(cat)}**: {_format_currency(tot)}" for cat, tot in spending_pairs]
        st.markdown("\n".join(items))

    # Month-over-month deltas: list[{month, total, delta_pct, avg_baseline}]
    month_deltas = _as_list_of_dicts(aggregations.get("month_deltas"))
    st.markdown("**Month-over-month**")
    if not month_deltas:
        st.caption("No month-over-month data.")
    else:
        lines = []
        for row in month_deltas:
            month = row.get("month", "")
            total = row.get("total")
            delta_pct = row.get("delta_pct")
            avg_baseline = row.get("avg_baseline")
            part = f"- **{html.escape(str(month))}**: {_format_currency(total)}"
            if delta_pct is not None:
                part += f" (Δ {delta_pct:+.1f}% vs prior)"
            if avg_baseline is not None:
                part += f", baseline avg {_format_currency(avg_baseline)}"
            lines.append(part)
        st.markdown("\n".join(lines))

    # Recurring charges: list[{merchant_normalized, months_seen, avg_amount, cv}]
    recurring = _as_list_of_dicts(aggregations.get("recurring_charges"))
    st.markdown("**Recurring charges**")
    if not recurring:
        st.caption("No recurring charges detected.")
    else:
        items = [f"- **{html.escape(str(r.get('merchant_normalized', '')))}**: {_format_currency(r.get('avg_amount'))} avg ({r.get('months_seen', 0)} months)" for r in recurring]
        st.markdown("\n".join(items))

    # Transfer fees summary: {count, total, avg_per_transfer, transactions: [...]} or list of txns
    fees_raw = aggregations.get("transfer_fees_summary")
    fees: dict[str, Any]
    if isinstance(fees_raw, list):
        fee_txns_raw = fees_raw
        fee_total_raw = sum(float(t.get("amount_base") or t.get("amount") or 0) for t in fee_txns_raw if isinstance(t, dict))
        fee_count_raw = len(fee_txns_raw)
        fees = {"count": fee_count_raw, "total": fee_total_raw, "avg_per_transfer": fee_total_raw / fee_count_raw if fee_count_raw else None, "transactions": fee_txns_raw}
    else:
        fees = _as_dict(fees_raw)
    st.markdown("**Transfer fees**")
    fee_count = int(fees.get("count") or 0)
    fee_total: float | None = float(fees["total"]) if fees.get("total") is not None else None
    fee_avg_pt: float | None = float(fees["avg_per_transfer"]) if fees.get("avg_per_transfer") is not None else None
    if fee_count == 0:
        st.caption("No transfer fees in this period.")
    else:
        st.caption(f"Total: {_format_currency(fee_total)} across {fee_count} transaction(s), avg {_format_currency(fee_avg_pt)} per transfer.")
        fee_txns: list[Any] = list(fees.get("transactions") or [])
        if fee_txns:
            lines = []
            for t in fee_txns:
                if not isinstance(t, dict):
                    continue
                amt = t.get('amount_base') or t.get('amount')
                lines.append(f"- {t.get('date', '')} · {html.escape(str(t.get('merchant', '')))} · {_format_currency(float(amt) if amt is not None else None)} ({t.get('category', '')})")
            if lines:
                st.markdown("\n".join(lines))


def _render_insights_dashboard(state: dict[str, Any]) -> None:
    """Render Statistics, Spending habits, and Observations sections from insights state."""
    state = _as_dict(state)
    aggregations = _as_dict(state.get("aggregations"))
    habits = _as_list_of_dicts(state.get("habits"))
    suggestions = _as_list_of_dicts(state.get("suggestions"))

    st.subheader("Statistics")
    _render_statistics(aggregations)

    st.subheader("Spending habits")
    if not habits:
        st.caption("No habits identified.")
    else:
        sorted_habits = sorted(habits, key=lambda h: _SEVERITY_ORDER.get(h.get("severity", "info"), 2))
        lines = []
        for h in sorted_habits:
            sev = h.get("severity", "info")
            cat = html.escape(str(h.get("category", "")))
            obs = html.escape(str(h.get("observation", "")))
            lines.append(f'<li class="severity-{sev}"><strong>{cat}</strong> {obs}</li>')
        st.markdown(f"<ul>{''.join(lines)}</ul>", unsafe_allow_html=True)

    st.subheader("Observations")
    if not suggestions:
        st.caption("No observations.")
    else:
        sorted_suggestions = sorted(suggestions, key=lambda s: _SEVERITY_ORDER.get(s.get("severity", "info"), 2))
        lines = []
        for s in sorted_suggestions:
            sev = s.get("severity", "info")
            title = html.escape(str(s.get("title", "")))
            body = html.escape(str(s.get("body", "")))
            lines.append(f'<li class="severity-{sev}"><strong>{title}</strong> — {body}</li>')
        st.markdown(f"<ul>{''.join(lines)}</ul>", unsafe_allow_html=True)

    with st.expander("Chat (coming soon)"):
        st.caption("Chat with your insights will be available here in a future update.")


def render_insights_tab() -> None:
    """Render the Insights tab: run controls, optional load cached, dashboard, chat placeholder."""
    # When no insights in session, show latest cache if one exists
    if st.session_state.insights_state is None:
        latest = DatabaseService().get_latest_insights_cache()
        if latest is not None:
            st.session_state.insights_state = latest
            st.rerun()

    filter_vals = _cached_filter_values()
    accounts_options = filter_vals.get("accounts", [])

    st.subheader("Insights")
    st.caption("Run the insights agent to see statistics, spending habits, and observations. Optionally load cached results.")

    date_from = st.date_input("Date from", value=None, key="insights_date_from")
    date_to = st.date_input("Date to", value=None, key="insights_date_to")
    accounts = st.multiselect("Accounts", options=accounts_options, default=None, key="insights_accounts")
    force_recompute = st.checkbox("Force recompute", value=False, key="insights_force_recompute")
    col_run, col_load = st.columns(2)
    with col_run:
        if st.button("Run Insights", type="primary", key="insights_run_btn"):
            _run_insights_graph(date_from, date_to, accounts or None, force_recompute)
    with col_load:
        if st.button("Load cached insights", key="insights_load_btn"):
            if date_from is None or date_to is None:
                st.warning("Select both Date from and Date to to load cached insights.")
            else:
                db = DatabaseService()
                cached = db.get_insights_cache(str(date_from), str(date_to), accounts or None)
                if cached is None:
                    st.warning("No cached insights for this date range and accounts.")
                else:
                    st.session_state.insights_state = {
                        "date_from": str(date_from),
                        "date_to": str(date_to),
                        "aggregations": cached["aggregations"],
                        "habits": cached.get("habits") or [],
                        "suggestions": cached.get("suggestions") or [],
                    }
                    st.rerun()

    st.markdown("---")

    state = st.session_state.insights_state
    if state is None:
        st.info("Run the insights agent or load cached insights to see the dashboard.")
    else:
        _render_insights_dashboard(state)


# ── Settings tab ─────────────────────────────────────────────────────────────

def render_settings_tab() -> None:
    """Render the Settings tab: user goals (add, list, remove)."""
    st.subheader("Settings")
    st.subheader("User goals")
    st.caption("Goals are used by the insights agent when generating habits and suggestions. Add, edit, or remove goals below.")

    db = DatabaseService()
    goals = db.get_active_goals()

    for goal in goals:
        gid = goal.get("id")
        content = goal.get("content", "")
        col_text, col_btn = st.columns([1, 0.12])
        with col_text:
            st.text(content)
        with col_btn:
            if gid is not None and st.button("Remove", key=f"goal_remove_{gid}", type="secondary"):
                db.deactivate_goal(int(gid))
                st.rerun()
    st.markdown("---")

    new_goal = st.text_input("New goal", placeholder="e.g. Save 20% of income each month", key="settings_new_goal")
    if st.button("Add goal", key="settings_add_goal", type="primary"):
        if (new_goal or "").strip():
            db.upsert_goal((new_goal or "").strip())
            st.rerun()
        else:
            st.warning("Enter a goal before adding.")


def main() -> None:
    """Initialize session, render sidebar and main content."""
    _init_session()
    render_sidebar()

    # Run resume in this cycle so the sidebar has already rendered with Resume disabled
    if st.session_state.get("pending_resume") and st.session_state.get("interrupted"):
        _resume_graph()
        return

    tab_run, tab_past, tab_insights, tab_settings = st.tabs([
        "Run reconciliation", "Past runs", "Insights", "Settings",
    ])
    with tab_run:
        render_main()
    with tab_past:
        render_past_runs()
    with tab_insights:
        render_insights_tab()
    with tab_settings:
        render_settings_tab()


if __name__ == "__main__":
    main()