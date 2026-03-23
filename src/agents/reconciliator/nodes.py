"""Reconciliation graph nodes: ingest, normalize, convert_currency, categorize, duplicates, suspicious, report."""

import base64
import hashlib
import json
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI

from agents.reconciliator.configuration import ReconciliationConfig
from agents.reconciliator.state import ReconciliationState
from agents.reconciliator.utils.parsers import load_documents
from shared.models import RawDocument, Receipt, ReceiptLine, Transaction
from shared.repositories.database_repository import (
    DatabaseRepository as DatabaseService,
)
from shared.repositories.exchange_repository import (
    ExchangeRepository as ExchangeService,
)

logger = logging.getLogger(__name__)


def _llm_content_str(content: Any) -> str:
    """Return string content from LLM response for JSON parsing."""
    if isinstance(content, str):
        return content
    return json.dumps(content) if content is not None else "[]"


def prepare_ingest(state: ReconciliationState) -> dict[str, Any]:
    """Expand source_paths (folders + files) into a deduplicated list of file paths."""
    source_paths = state.get("source_paths") or []
    seen: set[str] = set()
    source_files: list[str] = []
    exts = ["csv", "xlsx", "xls", "pdf", "jpg", "jpeg", "png"]
    for path in source_paths:
        p = Path(path)
        if p.is_dir():
            for ext in exts:
                for f in p.glob(f"*.{ext}"):
                    if not f.is_file():
                        continue
                    key = str(f.resolve())
                    if key not in seen:
                        seen.add(key)
                        source_files.append(key)
        elif p.is_file():
            key = str(p.resolve())
            if key not in seen:
                seen.add(key)
                source_files.append(key)
    return {"source_files": source_files}


def ingest(state: ReconciliationState) -> dict[str, Any]:
    """Load documents from source_files and return raw_documents."""
    source_files = state["source_files"]
    documents = load_documents(source_files)

    raw_documents = [
        RawDocument(
            source_file=doc.metadata["source"],
            file_type="xlsx"
            if doc.metadata["source"].endswith((".xlsx", ".xls"))
            else "pdf",
            content=doc.page_content,
            confidence=1.0,
        )
        for doc in documents
    ]

    return {"raw_documents": raw_documents, "receipts": []}


def make_ingest_images_node(
    config: ReconciliationConfig,
) -> Callable[[ReconciliationState], dict[str, Any]]:
    """Return a ingest_images node that loads images from source_folder and returns raw_images."""
    llm = ChatAnthropic(
        model=config.vision_model_name, max_tokens=config.vision_max_tokens
    )  # type: ignore[call-arg]

    def ingest_images(state: ReconciliationState) -> dict[str, Any]:
        source_files = state["source_files"]

        images = []

        img_ext = (".png", ".jpg", ".jpeg")
        for path in source_files:
            p = Path(path)
            if not p.is_file() or p.suffix.lower() not in img_ext:
                continue
            data = base64.standard_b64encode(p.read_bytes()).decode("utf-8")
            media_type = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
            images.append((path, data, media_type))
        msg_content: list[dict[str, Any]] = []

        for path, data, media_type in images:
            msg_content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": data,
                    },
                }
            )

        msg_content.append(
            {
                "type": "text",
                "text": """Extract the receipt summary and line items from this image.
                Include the date, currency, and confidence score.
                Do not include any other text in your response, no markdown blocks.
                If the receipt does not clearly state that a date is the date of the charge, set date to null.

                Return a single JSON object with this exact format:
                { "results": [ {
                    "merchant": "merchant name",
                    "date": "YYYY-MM-DD or null",
                    "currency": "currency code or null if not present",
                    "payment_method": "card or payment method shown (e.g. VISA ****1234) or null",
                    "total": total amount paid as number,
                    "lines": [
                        { "description": "line item description", "amount": number, "quantity": number or null }
                    ],
                    "confidence": 0-1
                } ] }

                Rules:
                - total is the final amount paid, not a sum you compute from lines
                - Include taxes, tips, and service charges as individual line items if shown
                - If multiple receipts are visible in the image, return one result object per receipt
                - Do not include any other text, no markdown blocks
                """,
            }
        )

        messages = [HumanMessage(content=msg_content)]  # type: ignore[arg-type]
        response = llm.invoke(messages)

        raw_text = response.content if hasattr(response, "content") else str(response)
        if not isinstance(raw_text, str):
            raw_text = str(raw_text)

        # Strip markdown code block if present
        text = raw_text.strip()
        if text.startswith("```"):
            first = text.find("\n")
            if first != -1:
                text = text[first + 1 : text.rfind("```")].strip()
            else:
                text = text.replace("```", "").strip()

        raw_documents = []
        receipts = []

        try:
            out = json.loads(text)
            results = out.get("results", out) if isinstance(out, dict) else out
            if not isinstance(results, list):
                results = [results]
        except json.JSONDecodeError as e:
            logger.warning(
                "Vision response was not valid JSON (error at pos %s): %s ... [tail] %s",
                e.pos,
                raw_text[:200],
                text[-300:] if len(text) > 300 else text,
            )
            results = []

        for i, (path, _, _) in enumerate(images):
            item = results[i] if i < len(results) else {}
            confidence = item.get("confidence", 0.0)
            merchant = item.get("merchant", "")
            lines_raw = item.get("lines") or []
            is_receipt = bool(merchant and lines_raw)

            if is_receipt:
                receipt = Receipt(
                    merchant=merchant,
                    date=item.get("date") or None,
                    currency=item.get("currency") or None,
                    total=float(item.get("total") or 0.0),
                    lines=[
                        ReceiptLine(
                            description=line.get("description", ""),
                            amount=float(line.get("amount", 0.0)),
                            quantity=float(line.get("quantity") or 1.0),
                        )
                        for line in lines_raw
                        if isinstance(line, dict)
                    ],
                    source_file=path,
                    confidence=confidence,
                )
                receipts.append(receipt)

            # Collapse to markdown table for normalize — always, regardless of type
            date = item.get("date") or ""
            total = item.get("total") or 0.0
            account = "Receipt" if is_receipt else "Statement"

            doc_content = (
                "| date | amount | description | account |\n"
                "|------|--------|-------------|----------|\n"
                f"| {date} | {total} | {merchant} | {account} |"
            )

            raw_documents.append(
                RawDocument(
                    source_file=path,
                    file_type="image",
                    content=doc_content,
                    confidence=confidence,
                )
            )

        return {"raw_documents": raw_documents, "receipts": receipts}

    return ingest_images


def make_normalize_node(
    config: ReconciliationConfig,
) -> Callable[[ReconciliationState], dict[str, Any]]:
    """Return a normalize node that extracts transactions from raw_documents (with cache)."""
    llm = ChatOpenAI(model=config.model_name, temperature=config.temperature)

    def normalize(state: ReconciliationState) -> dict[str, Any]:
        transactions = []
        database_service = DatabaseService()

        for doc in state["raw_documents"]:
            if isinstance(doc, dict):
                doc = RawDocument(**doc)

            content_hash = hashlib.sha256(doc.content.encode()).hexdigest()
            cached = database_service.get_cached_transactions(content_hash)
            if cached is not None:
                for t in cached:
                    t.setdefault("confidence", getattr(doc, "confidence", None))
                    t.setdefault("source_file", doc.source_file)
                    transactions.append(Transaction(**t))
                continue

            prompt = f"""
                Extract all transactions from the following financial document.
                This may be a bank statement or a receipt.
                - For bank statements: infer the account name from the document (bank name, account type, last 4 digits if present).
                - For receipts: the account and currency columns are already provided in the document — use them as-is.

                If a currency is explicitly stated in the document, use it.
                If no currency is stated, default to {config.base_currency}.


                Return ONLY a JSON array with this exact schema, no preamble, no markdown:
                [{{
                    "date": "YYYY-MM-DD",
                    "amount_original": 0.00,
                    "amount_base": null,
                    "currency": "currency code",
                    "merchant": "merchant name",
                    "account": "account name",
                    "source_file": "{doc.source_file}"
                }}]

                Document:
                {doc.content}
            """

            try:
                response = llm.invoke(prompt)
                raw_json = json.loads(_llm_content_str(response.content))
                for t in raw_json:
                    t["id"] = str(uuid.uuid4())
                    t.setdefault("source_file", doc.source_file)
                    t.setdefault("confidence", getattr(doc, "confidence", None))
                    transactions.append(Transaction(**t))
                database_service.save_normalized_document(
                    content_hash,
                    doc.source_file,
                    json.dumps(
                        [x.model_dump() for x in transactions[-len(raw_json) :]]
                    ),
                )
            except json.JSONDecodeError as e:
                logger.warning(
                    "Failed to parse LLM response for %s: %s", doc.source_file, e
                )
                continue
            except Exception as e:
                logger.warning("Failed to normalize %s: %s", doc.source_file, e)
                continue

        return {"transactions": transactions}

    return normalize


def make_review_low_confidence_transactions_node(
    config: ReconciliationConfig,
) -> Callable[[ReconciliationState], dict[str, Any]]:
    """Return a node that applies user decisions for low-confidence transactions (runs after interrupt)."""
    threshold = config.image_low_confidence_threshold

    def review_low_confidence_transactions(
        state: ReconciliationState,
    ) -> dict[str, Any]:
        transactions = [
            Transaction(**t) if isinstance(t, dict) else t
            for t in state["transactions"]
        ]
        low_conf = [
            t
            for t in transactions
            if t.confidence is not None and t.confidence < threshold
        ]
        if not low_conf:
            return {}

        decisions = state.get("low_confidence_decisions") or []
        if not decisions:
            return {}

        decisions_by_id = {
            d["id"]: d
            for d in decisions
            if isinstance(d, dict) and d.get("id") is not None
        }
        low_conf_ids = {t.id for t in low_conf}
        new_txns: list[Transaction] = []
        for t in transactions:
            if t.id not in low_conf_ids:
                new_txns.append(t)
                continue
            d = decisions_by_id.get(t.id)
            if d is None:
                new_txns.append(t)
                continue
            action = d.get("action", "approve")
            if action == "dismiss":
                continue
            if action == "edit" and "transaction" in d:
                new_txns.append(Transaction(**d["transaction"]))
            else:
                new_txns.append(t)

        # Update normalized_document_cache for affected source_files (dismissed or edited)
        source_files_to_update = {t.source_file for t in low_conf}
        raw_docs = state.get("raw_documents") or []
        database_service = DatabaseService()
        for doc in raw_docs:
            if isinstance(doc, dict):
                doc = RawDocument(**doc)
            if doc.source_file not in source_files_to_update:
                continue
            content_hash = hashlib.sha256(doc.content.encode()).hexdigest()
            transactions_for_doc = [
                t for t in new_txns if t.source_file == doc.source_file
            ]
            database_service.save_normalized_document(
                content_hash,
                doc.source_file,
                json.dumps([t.model_dump() for t in transactions_for_doc]),
            )

        return {
            "transactions": new_txns,
            "low_confidence_decisions": None,
        }

    return review_low_confidence_transactions


def make_convert_currency_node(
    config: ReconciliationConfig,
) -> Callable[[ReconciliationState], dict[str, Any]]:
    """Return a convert_currency node that converts amounts to base_currency."""
    exchange_service: ExchangeService = ExchangeService()

    def convert_currency(state: ReconciliationState) -> dict[str, Any]:

        transactions = []
        exchange_rates = dict(state["exchange_rates"])

        for t in state["transactions"]:
            if isinstance(t, dict):
                t = Transaction(**t)

            if t.currency == config.base_currency:
                t = t.model_copy(update={"amount_base": t.amount_original})
            else:
                cache_key = f"{t.date}-{t.currency}"
                if cache_key not in exchange_rates:
                    time.sleep(0.5)  # debounce — 2 requests/second max
                    rate = exchange_service.get_rate(
                        t.date, t.currency, config.base_currency
                    )
                    if rate:
                        exchange_rates[cache_key] = rate
                    else:
                        logger.warning(
                            "No exchange rate for %s on %s, amount_base null for transaction %s",
                            t.currency,
                            t.date,
                            t.id,
                        )

                rate = exchange_rates.get(cache_key)

                amount_base = round(t.amount_original * rate, 2) if rate else None
                t = t.model_copy(update={"amount_base": amount_base})

            transactions.append(t)

        return {"transactions": transactions, "exchange_rates": exchange_rates}

    return convert_currency


def make_categorize_node(
    config: ReconciliationConfig,
) -> Callable[[ReconciliationState], dict[str, Any]]:
    """Return a categorize node that checks merchant_categories cache before calling the LLM."""
    llm = ChatOpenAI(
        model=config.model_name,
        temperature=config.temperature,
        max_tokens=4096,  # type: ignore[call-arg]
    )

    def _chunk(transactions: list[Transaction], chunk_size: int = 50) -> Any:
        for i in range(0, len(transactions), chunk_size):
            yield transactions[i : i + chunk_size]

    def categorize(state: ReconciliationState) -> dict[str, Any]:
        transactions = [
            Transaction(**t) if isinstance(t, dict) else t
            for t in state["transactions"]
        ]
        database_service = DatabaseService()
        updated: list[Transaction] = []

        for batch in _chunk(transactions, 50):
            cache_hits: dict[str, str] = {}
            llm_needed: list[Transaction] = []

            for t in batch:
                m_norm = database_service.normalize_merchant(t.merchant)
                cached_cat = database_service.get_merchant_category(m_norm)
                if cached_cat is not None:
                    cache_hits[t.id] = cached_cat
                else:
                    llm_needed.append(t)

            logger.info(
                "categorize: %d cache hits, %d LLM calls needed in this batch",
                len(cache_hits),
                len(llm_needed),
            )

            llm_results: dict[str, str | None] = {}
            if llm_needed:
                transaction_list = "\n".join(
                    [
                        f"{t.id} | {t.merchant} | {t.amount_original} {t.currency}"
                        for t in llm_needed
                    ]
                )

                prompt = f"""
                        Categorize each transaction using a standard personal finance category.
                        Use consistent, common category names such as:
                        Groceries, Dining, Transport, Utilities, Healthcare, Entertainment,
                        Shopping, Travel, Education, Salary, Freelance, Transfer, Fees & Charges,
                        Rent, Other Income, Other.

                        You may use a different category if none of the above fit, but prefer
                        the list above for common transactions.
                        If you cannot confidently categorize a transaction, set category to null.

                        Transactions (id | merchant | amount currency):
                        {transaction_list}

                        Return ONLY a JSON array, no preamble, no markdown:
                        [{{"id": "transaction-id", "category": "Category or null"}}]
                    """

                try:
                    response = llm.invoke(prompt)
                    raw_json = json.loads(_llm_content_str(response.content))
                    llm_results = {item["id"]: item["category"] for item in raw_json}

                    # Write new mappings to merchant_categories cache
                    for t in llm_needed:
                        cat = llm_results.get(t.id)
                        if cat and cat != "null":
                            database_service.upsert_merchant_category(
                                database_service.normalize_merchant(t.merchant), cat
                            )

                except json.JSONDecodeError as e:
                    logger.warning("Failed to parse categorization response: %s", e)
                    # Mark all LLM-needed as needs_review
                    for t in llm_needed:
                        updated.append(
                            t.model_copy(
                                update={
                                    "needs_review": True,
                                    "review_reason": "Categorization batch failed",
                                }
                            )
                        )
                    # Still apply cache hits for the rest of the batch
                    for t in batch:
                        if t.id in cache_hits:
                            updated.append(
                                t.model_copy(update={"category": cache_hits[t.id]})
                            )
                    continue

            for t in batch:
                if t.id in cache_hits:
                    updated.append(t.model_copy(update={"category": cache_hits[t.id]}))
                    continue

                category = llm_results.get(t.id)
                if category is None or category == "null":
                    updated.append(
                        t.model_copy(
                            update={
                                "needs_review": True,
                                "review_reason": "Could not confidently categorize transaction",
                            }
                        )
                    )
                else:
                    updated.append(t.model_copy(update={"category": category}))

        return {"transactions": updated}

    return categorize


def make_detect_duplicates_node(
    config: ReconciliationConfig,
) -> Callable[[ReconciliationState], dict[str, Any]]:
    """Return a detect_duplicates node; checks duplicate_pairs cache before LLM for fuzzy matches."""
    llm = ChatOpenAI(model=config.model_name, temperature=config.temperature)

    def _dates_within(date_a: str, date_b: str, days: int) -> bool:
        d_a = datetime.fromisoformat(date_a)
        d_b = datetime.fromisoformat(date_b)
        return abs((d_a - d_b).days) <= days

    def _amounts_fuzzy_match(
        amount_a: float, amount_b: float, tolerance: float = 0.02
    ) -> bool:
        if amount_a == 0:
            return False
        return abs(amount_a - amount_b) / abs(amount_a) <= tolerance

    def _check_duplicate_with_llm(
        database_service: DatabaseService,
        t_a: Transaction,
        t_b: Transaction,
        fp_a: str,
        fp_b: str,
    ) -> tuple[bool, bool, str]:
        """Check duplicate_pairs cache first; fall back to LLM. Return (is_duplicate, needs_review, reason)."""
        cached = database_service.get_duplicate_pair(fp_a, fp_b)
        if cached is not None:
            logger.debug("duplicate_pairs cache hit: %s / %s", fp_a[:8], fp_b[:8])
            return cached["is_duplicate"], False, cached["reason"]

        prompt = f"""
        Are these two transactions likely the same transaction appearing in two different bank statements?
        Transaction A: {t_a.date} | {t_a.merchant} | {t_a.amount_original} {t_a.currency} | {t_a.account}
        Transaction B: {t_b.date} | {t_b.merchant} | {t_b.amount_original} {t_b.currency} | {t_b.account}

        Reply ONLY with JSON, no preamble, no markdown:
        {{"is_duplicate": true/false, "confidence": "high/medium/low", "reason": "brief reason"}}
        """

        try:
            response = llm.invoke(prompt)
            result = json.loads(_llm_content_str(response.content))
            is_duplicate = result["is_duplicate"]
            confidence = result["confidence"]
            reason = result.get("reason") or ""

            database_service.upsert_duplicate_pair(fp_a, fp_b, is_duplicate, reason)

            if is_duplicate:
                return True, False, reason
            elif confidence == "low":
                return False, True, reason
            else:
                return False, False, reason

        except Exception as e:
            logger.warning(
                "LLM duplicate check failed for %s vs %s: %s", t_a.id, t_b.id, e
            )
            return False, True, "LLM duplicate check failed"

    def detect_duplicates(state: ReconciliationState) -> dict[str, Any]:
        transactions = [
            Transaction(**t) if isinstance(t, dict) else t
            for t in state["transactions"]
        ]
        database_service = DatabaseService()
        transactions.sort(key=lambda t: t.date)

        matched_ids: set[str] = set()
        duplicates: list[Transaction] = []
        updated = {t.id: t for t in transactions}

        for i, t_a in enumerate(transactions):
            if t_a.id in matched_ids:
                continue

            fp_a = database_service.transaction_fingerprint(
                t_a.date, t_a.amount_original, t_a.currency, t_a.merchant
            )

            for t_b in transactions[i + 1 :]:
                if t_a.currency != t_b.currency:
                    continue
                if t_b.id in matched_ids:
                    continue
                if not _dates_within(t_a.date, t_b.date, days=3):
                    continue

                fp_b = database_service.transaction_fingerprint(
                    t_b.date, t_b.amount_original, t_b.currency, t_b.merchant
                )

                if t_a.amount_original == t_b.amount_original:
                    # Exact match — no LLM needed; persist to duplicate_pairs for future runs
                    database_service.upsert_duplicate_pair(
                        fp_a, fp_b, True, "exact amount and date match"
                    )
                    updated[t_b.id] = t_b.model_copy(update={"duplicate_of": t_a.id})
                    matched_ids.update([t_a.id, t_b.id])
                    duplicates.extend([updated[t_a.id], updated[t_b.id]])

                elif _amounts_fuzzy_match(t_a.amount_original, t_b.amount_original):
                    is_duplicate, needs_review, reason = _check_duplicate_with_llm(
                        database_service, t_a, t_b, fp_a, fp_b
                    )
                    if is_duplicate:
                        updated[t_b.id] = t_b.model_copy(
                            update={"duplicate_of": t_a.id}
                        )
                        matched_ids.update([t_a.id, t_b.id])
                        duplicates.extend([updated[t_a.id], updated[t_b.id]])
                    elif needs_review:
                        updated[t_b.id] = t_b.model_copy(
                            update={"needs_review": True, "review_reason": reason}
                        )

        return {
            "transactions": list(updated.values()),
            "duplicates": duplicates,
        }

    return detect_duplicates


def make_flag_suspicious_node(
    config: ReconciliationConfig,
) -> Callable[[ReconciliationState], dict[str, Any]]:
    """Return a flag_suspicious node that marks suspicious transactions."""
    llm = ChatOpenAI(
        model=config.model_name,
        temperature=config.temperature,
        max_tokens=4096,  # type: ignore[call-arg]
    )

    def _chunk(transactions: list[Transaction], chunk_size: int = 50) -> Any:
        for i in range(0, len(transactions), chunk_size):
            yield transactions[i : i + chunk_size]

    def flag_suspicious(state: ReconciliationState) -> dict[str, Any]:
        transactions = [
            Transaction(**t) if isinstance(t, dict) else t
            for t in state["transactions"]
        ]
        suspicious_map: dict[str, str] = {}

        for batch in _chunk(transactions, 50):
            transaction_list = "\n".join(
                [
                    f"{t.id} | {t.date} | {t.merchant} | {t.amount_original} {t.currency} | {t.account} | {t.category}"
                    for t in batch
                ]
            )

            prompt = f"""
            Analyze the following transactions and identify any suspicious or unusual activity.
            Only flag transactions that clearly warrant human review. When in doubt, do not flag.

            Consider:
            - Amounts that are clearly out of line (e.g. duplicate charge or 10x what you would expect for that category), not just the largest in the list
            - Same merchant charged multiple times on the same day (possible duplicate charge)
            - Recurring payments that changed amount significantly
            - Unexpected foreign currency charges
            - Other clear inconsistencies: e.g. duplicate charge for the same service, amounts that suggest a billing error or unauthorized use.

            Do not flag transactions with positive amounts — these are income or credits regardless of category. 
            Do not flag routine subscriptions (Netflix, Spotify, etc.), normal grocery/dining/transportation spending, utility bills, transfers between user's own accounts.
            Do not flag income for being high or variable. Lump-sum and variable income are normal.
            Do not flag large routine charges such as rent or utilities.

            Transactions (id | date | merchant | amount currency | account | category):
            {transaction_list}

            Return ONLY a JSON array of suspicious transaction ids, no preamble, no markdown.
            If nothing is clearly suspicious return []:
            [{{"id": "transaction-id", "reason": "brief reason"}}]
            In "reason" use only plain text; do not put double-quotes or newlines inside the value (so the JSON stays valid).
            """

            try:
                response = llm.invoke(prompt)
                raw_json = json.loads(_llm_content_str(response.content))
                suspicious_map.update(
                    {item["id"]: item.get("reason") or "" for item in raw_json}
                )
            except json.JSONDecodeError as e:
                logger.warning(
                    "Failed to parse suspicious transactions response: %s", e
                )

        updated: list[Transaction] = []
        suspicious: list[Transaction] = []

        for t in transactions:
            if t.id in suspicious_map:
                t = t.model_copy(
                    update={
                        "suspicious": True,
                        "suspicious_reason": suspicious_map[t.id],
                    }
                )
                suspicious.append(t)
            updated.append(t)

        return {"transactions": updated, "suspicious": suspicious}

    return flag_suspicious


def human_review(state: ReconciliationState) -> dict[str, Any]:
    """Return empty updates (placeholder for human-in-the-loop review)."""
    return {}


def generate_report(
    state: ReconciliationState, config: RunnableConfig
) -> dict[str, Any]:
    """Build a text report from transactions, duplicates, and suspicious lists."""
    transactions = [
        Transaction(**t) if isinstance(t, dict) else t for t in state["transactions"]
    ]

    total = len(transactions)
    duplicates = len(state["duplicates"])
    suspicious = len(state["suspicious"])
    needs_review = len([t for t in transactions if t.needs_review])

    by_category: dict[str, dict[str, Any]] = {}

    for t in transactions:
        cat = t.category or "Uncategorized"
        prev = by_category.get(cat, {"count": 0, "amount": 0.0})
        amount = abs(t.amount_base) if t.amount_base is not None else 0.0
        by_category[cat] = {
            "count": prev["count"] + 1,
            "amount": round(prev["amount"] + amount, 2),
        }

    report = f"""
    RECONCILIATION REPORT
    ---------------------
    Total transactions: {total}
    Duplicates found: {duplicates}
    Suspicious transactions: {suspicious}
    Needs review: {needs_review}

    By category: {by_category}
    """

    run_id = state.get("run_id")
    if run_id:
        thread_id = (config or {}).get("configurable", {}).get("thread_id", "")
        source_paths = state.get("source_paths") or []
        database_service = DatabaseService()
        database_service.insert_run(run_id, thread_id, source_paths)
        rows = [
            {
                "id": t.id,
                "run_id": run_id,
                "fingerprint": database_service.transaction_fingerprint(
                    t.date, t.amount_original, t.currency, t.merchant
                ),
                "date": t.date,
                "amount_original": t.amount_original,
                "amount_base": t.amount_base,
                "currency": t.currency,
                "merchant": t.merchant,
                "merchant_normalized": database_service.normalize_merchant(t.merchant),
                "account": t.account,
                "source_file": t.source_file,
                "category": t.category,
                "duplicate_of": t.duplicate_of,
                "suspicious": int(t.suspicious),
                "suspicious_reason": t.suspicious_reason,
                "needs_review": int(t.needs_review),
                "review_reason": t.review_reason,
                "review_status": t.review_status,
                "confidence": t.confidence,
            }
            for t in transactions
        ]
        database_service.upsert_transactions(rows)

        receipts = state.get("receipts") or []

        receipt_rows = []
        line_rows = []
        for receipt in receipts:
            if isinstance(receipt, dict):
                receipt = Receipt(**receipt)

            receipt_id = str(uuid.uuid4())
            merchant_norm = database_service.normalize_merchant(receipt.merchant)

            receipt_rows.append(
                {
                    "id": receipt_id,
                    "run_id": run_id,
                    "transaction_id": None,
                    "source_file": receipt.source_file,
                    "merchant": receipt.merchant,
                    "merchant_normalized": merchant_norm,
                    "date": receipt.date,
                    "currency": receipt.currency,
                    "subtotal": receipt.subtotal,
                    "tax_amount": receipt.tax_amount,
                    "tax_rate": receipt.tax_rate,
                    "total": receipt.total,
                    "receipt_number": receipt.receipt_number,
                    "confidence": receipt.confidence,
                    "raw_content": receipt.raw_content,
                }
            )

            for ln, line in enumerate(receipt.lines, start=1):
                line_rows.append(
                    {
                        "id": str(uuid.uuid4()),
                        "receipt_id": receipt_id,
                        "line_number": ln,
                        "description": line.description,
                        "quantity": line.quantity,
                        "unit_price": line.unit_price,
                        "amount": line.amount,
                        "category": None,
                    }
                )

        database_service.upsert_receipts(receipt_rows)
        database_service.upsert_receipt_lines(line_rows)
        database_service.auto_link_receipts(run_id)

        database_service.update_run(
            run_id,
            status="complete",
            total_transactions=total,
            total_duplicates=duplicates,
            total_suspicious=suspicious,
        )

    return {"report": report}


def passthrough(state: ReconciliationState) -> dict[str, Any]:
    """No state change; used for graph structure."""
    return {"receipts": []}
