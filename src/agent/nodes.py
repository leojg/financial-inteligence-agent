import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from langchain_openai import ChatOpenAI

from agent.configuration import ReconciliationConfig
from agent.db import get_connection
from agent.services.exchange_service import ExchangeService
from agent.state import ReconciliationState, RawDocument, Transaction
from agent.utils.parsers import load_documents

logger = logging.getLogger(__name__) 

def ingest(state: ReconciliationState) -> dict:

    documents = load_documents(Path(state["source_folder"]))

    raw_documents = [
        RawDocument(
            source_file=doc.metadata["source"],
            file_type="xlsx" if doc.metadata["source"].endswith((".xlsx", ".xls")) else "pdf",
            content=doc.page_content
        )
        for doc in documents
    ]

    return {
        "raw_documents": raw_documents
    }

def make_normalize_node(config: ReconciliationConfig):
    llm = ChatOpenAI(model=config.model_name, temperature=config.temperature)

    def normalize(state: ReconciliationState) -> dict:
        transactions = []
        conn = get_connection()

        for doc in state["raw_documents"]:
            if isinstance(doc, dict):
                doc = RawDocument(**doc)

            content_hash = hashlib.sha256(doc.content.encode()).hexdigest()
            cur = conn.execute(
                "SELECT transactions_json FROM normalized_document_cache WHERE source_file = ? AND content_hash = ?",
                (doc.source_file, content_hash),
            )
            row = cur.fetchone()
            cur.close()
            if row is not None:
                cached = json.loads(row[0])
                for t in cached:
                    transactions.append(Transaction(**t))
                continue # Transaction already present in the cache

            prompt = f"""
                Extract all transactions from the following bank statement.
                Infer the account name from the document (bank name, account type, last 4 digits if present).

                Return ONLY a JSON array with this exact schema, no preamble, no markdown:
                [{{
                    "date": "YYYY-MM-DD",
                    "amount_original": 0.00,
                    "amount_base": null,
                    "currency": "UYU or USD",
                    "merchant": "merchant name",
                    "account": "inferred account name",
                    "source_file": "{doc.source_file}"
                }}]

                Document:
                {doc.content}
            """

            try:
                response = llm.invoke(prompt)
                raw_json = json.loads(response.content)
                for t in raw_json:
                    t["id"] = str(uuid.uuid4())
                    transactions.append(Transaction(**t))
                conn.execute(
                    "INSERT OR REPLACE INTO normalized_document_cache (source_file, content_hash, transactions_json) VALUES (?, ?, ?)",
                    (doc.source_file, content_hash, json.dumps([x.model_dump() for x in transactions[-len(raw_json):]])),
                )
                conn.commit()
            except json.JSONDecodeError as e:
                logger.warning("Failed to parse LLM response for %s: %s", doc.source_file, e)
                continue
            except Exception as e:
                logger.warning("Failed to normalize %s: %s", doc.source_file, e)
                continue

        return {
            "transactions": transactions
        }

    return normalize


def make_convert_currency_node(config: ReconciliationConfig):

    exchange_service = ExchangeService()

    def convert_currency(state: ReconciliationState) -> dict:

        transactions = []
        exchange_rates = dict(state["exchange_rates"])

        for t in state["transactions"]:
            if isinstance(t, dict):
                t = Transaction(**t)
            
            if t.currency == config.base_currency:
                t = t.model_copy(
                    update={
                        "amount_base": t.amount_original
                    }
                )
            else:
                cache_key = f"{t.date}-{t.currency}"
                if cache_key not in exchange_rates:
                    time.sleep(0.5)  # debounce — 2 requests/second max
                    rate = exchange_service.get_rate(t.date, t.currency, config.base_currency)
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
        
        return {
            "transactions": transactions,
            "exchange_rates": exchange_rates
        }
    
    return convert_currency

def make_categorize_node(config: ReconciliationConfig):

    # Categorization is a heavy task, so we need to increase the max tokens
    llm = ChatOpenAI(model=config.model_name, temperature=config.temperature, max_tokens=4096)

    # Chunk the transactions into smaller batches to avoid context window errors
    def _chunk(transactions: list[Transaction], chunk_size: int = 50):
        for i in range(0, len(transactions), chunk_size):
            yield transactions[i:i+chunk_size]

    def categorize(state: ReconciliationState) -> dict:
        transactions = [
            Transaction(**t) if isinstance(t, dict) else t for t in state["transactions"]
        ]

        updated = []

        for batch in _chunk(transactions, 50):
            transaction_list = "\n".join([
                f"{t.id} | {t.merchant} | {t.amount_original} {t.currency}"
                for t in batch
            ])

            prompt = f"""
                Categorize each transaction using ONLY the categories listed below.
                If you cannot confidently categorize a transaction, set category to null.

                Categories: {", ".join(config.categories)}

                Transactions (id | merchant | amount currency):
                {transaction_list}

                Return ONLY a JSON array, no preamble, no markdown:
                [{{"id": "transaction-id", "category": "Category or null"}}]
            """

            try:
                response = llm.invoke(prompt)
                raw_json = json.loads(response.content)
                category_map = {item["id"]: item["category"] for item in raw_json}

                for t in batch:
                    category = category_map.get(t.id)
                    if category is None or category == "null":
                        t = t.model_copy(
                            update={
                                "needs_review": True,
                                "review_reason": "Could not confidently categorize transaction"
                            }
                        )
                    else:
                        t = t.model_copy(update={"category": category})
                    updated.append(t)
                
            except json.JSONDecodeError as e:
                logger.warning("Failed to parse categorization response: %s", e)
                # Mark all as needs_review if batch fails
                for t in batch:
                    updated.append(t.model_copy(update={
                        "needs_review": True,
                        "review_reason": "Categorization batch failed"
                    }))

        return {"transactions": updated}

    return categorize

def make_detect_duplicates_node(config: ReconciliationConfig):

    llm = ChatOpenAI(model=config.model_name, temperature=config.temperature)

    def _dates_within(date_a: str, date_b: str, days: int) -> bool:
        d_a = datetime.fromisoformat(date_a)
        d_b = datetime.fromisoformat(date_b)
        return abs((d_a - d_b).days) <= days

    def _amounts_fuzzy_match(amount_a: float, amount_b: float, tolerance: float = 0.02) -> bool:
        if amount_a == 0:
            return False
        return abs(amount_a - amount_b) / abs(amount_a) <= tolerance

    def _check_duplicate_with_llm(t_a: Transaction, t_b: Transaction) -> tuple[bool, bool, str]:
        """
            Returns: (is_duplicate: bool, needs_review: bool, reason: str)
        """

        prompt = f"""
        Are these two transactions likely the same transaction appearing in two different bank statements?
        Transaction A: {t_a.date} | {t_a.merchant} | {t_a.amount_original} {t_a.currency} | {t_a.account}
        Transaction B: {t_b.date} | {t_b.merchant} | {t_b.amount_original} {t_b.currency} | {t_b.account}

        Reply ONLY with JSON, no preamble, no markdown:
        {{"is_duplicate": true/false, "confidence": "high/medium/low", "reason": "brief reason"}}
        """

        try:
            response = llm.invoke(prompt)
            result = json.loads(response.content)
            is_duplicate = result["is_duplicate"]
            confidence = result["confidence"]
            reason = result.get("reason") or ""

            if is_duplicate:
                return True, False, reason
            elif confidence == "low":
                return False, True, reason
            else:
                return False, False, reason

        except Exception as e:
            logger.warning("LLM duplicate check failed for %s vs %s: %s", t_a.id, t_b.id, e)
            return False, True, "LLM duplicate check failed"

    def detect_duplicates(state: ReconciliationState) -> dict:

        transactions = [
            Transaction(**t) if isinstance(t, dict) else t
            for t in state["transactions"]
        ]

        # Sort transactions by date ascending
        transactions.sort(key=lambda t: t.date)

        matched_ids = set()
        duplicates = []
        updated = {t.id: t for t in transactions}

        for i, t_a in enumerate(transactions):
            if t_a.id in matched_ids:
                continue
                
            for t_b in transactions[i+1:]:

                if t_a.currency != t_b.currency:
                    continue

                if t_b.id in matched_ids:
                    continue

                if not _dates_within(t_a.date, t_b.date, days=3):
                    continue

                if t_a.amount_original == t_b.amount_original:
                    updated[t_b.id] = t_b.model_copy(
                        update={"duplicate_of": t_a.id}
                    )
                    matched_ids.add(t_a.id)
                    matched_ids.add(t_b.id)
                    duplicates.extend([updated[t_a.id], updated[t_b.id]])

                elif _amounts_fuzzy_match(t_a.amount_original, t_b.amount_original):
                    is_duplicate, needs_review, reason = _check_duplicate_with_llm(t_a, t_b)

                    if is_duplicate:
                        updated[t_b.id] = t_b.model_copy(
                            update={"duplicate_of": t_a.id}
                        )
                        matched_ids.add(t_a.id)
                        matched_ids.add(t_b.id)
                        duplicates.extend([updated[t_a.id], updated[t_b.id]])
                    elif needs_review:
                        updated[t_b.id] = t_b.model_copy(
                            update={"needs_review": True, "review_reason": reason}
                        )
                
        return {
            "transactions": list(updated.values()),
            "duplicates": duplicates
        }
        
    return detect_duplicates

def make_flag_suspicious_node(config: ReconciliationConfig):

    # Suspicious detection is a heavy task, so we need to increase the max tokens
    llm = ChatOpenAI(model=config.model_name, temperature=config.temperature, max_tokens=4096)

    def _chunk(transactions: list[Transaction], chunk_size: int = 50):
        for i in range(0, len(transactions), chunk_size):
            yield transactions[i:i+chunk_size]

    def flag_suspicious(state: ReconciliationState) -> dict:
        transactions = [
            Transaction(**t) if isinstance(t, dict) else t
            for t in state["transactions"]
        ]

        suspicious_map = {}

        for batch in _chunk(transactions, 50):

            transaction_list = "\n".join([
                f"{t.id} | {t.date} | {t.merchant} | {t.amount_original} {t.currency} | {t.account} | {t.category}"
                for t in batch
            ])

            prompt = f"""
            Analyze the following transactions and identify any suspicious or unusual activity.
            
            Consider:
            - Unusually large amounts compared to similar transactions
            - Same merchant charged multiple times on the same day
            - Recurring payments that changed amount significantly
            - Unexpected foreign currency charges
            - Any other patterns that seem anomalous
            
            Transactions (id | date | merchant | amount currency | account | category):
            {transaction_list}

            Return ONLY a JSON array of suspicious transaction ids, no preamble, no markdown.
            If nothing is suspicious return []:
            [{{"id": "transaction-id", "reason": "brief reason"}}]
            """

            try:
                response = llm.invoke(prompt)
                raw_json = json.loads(response.content)
                suspicious_map.update({item["id"]: item.get("reason") or "" for item in raw_json})
            except json.JSONDecodeError as e:
                logger.warning("Failed to parse suspicious transactions response: %s", e)
                updated = transactions
                suspicious = []


        updated = []
        suspicious = []

        for t in transactions:
            if t.id in suspicious_map:
                t = t.model_copy(update={"suspicious": True, "suspicious_reason": suspicious_map[t.id]})
                suspicious.append(t)
            updated.append(t)

        return {
            "transactions": updated,
            "suspicious": suspicious
        }

    return flag_suspicious

def human_review(state: ReconciliationState) -> dict:
    return {}

def generate_report(state: ReconciliationState) -> dict:
    transactions = [
        Transaction(**t) if isinstance(t, dict) else t 
        for t in state["transactions"]
    ]

    total = len(transactions)
    duplicates = len(state["duplicates"])
    suspicious = len(state["suspicious"])
    needs_review = len([t for t in transactions if t.needs_review])

    by_category = {}

    for t in transactions:
        cat = t.category or "Uncategorized"
        prev = by_category.get(cat, {"count": 0, "amount": 0.0})
        amount = abs(t.amount_base) if t.amount_base is not None else 0.0
        by_category[cat] = {
            "count": prev["count"] + 1,
            "amount": round(prev["amount"] + amount, 2)
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
    
    return {"report": report}

