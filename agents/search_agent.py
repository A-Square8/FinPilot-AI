"""
Search Agent — Hybrid Retrieval Pipeline for /search

Architecture:
  1. Intent Classification (Gemini)  → structured vs semantic
  2. Retrieval                       → SQL (exact, complete) or Vector (fuzzy, top-N)
  3. Stats Computation (Python)      → totals, counts, breakdowns (never trust LLM for math)
  4. Synthesis (Gemini)              → natural language answer using pre-computed stats
"""

import json
import google.generativeai as genai
from datetime import date, timedelta
from config.settings import settings
from db.crud import (
    search_transactions_by_filters,
    get_distinct_categories,
    get_transactions_by_ids,
)
from db.models import Transaction
from vector_store.chroma_client import search_transactions as vector_search
import structlog

logger = structlog.get_logger()

genai.configure(api_key=settings.gemini_api_key)

FALLBACK_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-flash-latest",
]

# ---------------------------------------------------------------------------
# Phase 1: Intent Classification
# ---------------------------------------------------------------------------

_INTENT_PROMPT = """\
You are a financial query classifier. Given a user's search query about their transactions,
determine whether it can be answered with a structured database query or needs fuzzy semantic search.

Today's date: {today}
Available categories in user's data: {categories}

User query: "{query}"

Return ONLY a valid JSON object (no markdown, no backticks):
{{
  "strategy": "structured" or "semantic",
  "categories": ["Food", "Groceries"] or null,
  "date_from": "YYYY-MM-DD" or null,
  "date_to": "YYYY-MM-DD" or null,
  "type": "expense" or "income" or "emi" or null,
  "description_keyword": "cookies" or null,
  "semantic_query": "rephrased search query" or null
}}

Decision rules:
- Prefer "structured" whenever possible — it returns ALL matching transactions, not just a few.
- Use "structured" when the query mentions categories, dates, transaction types, or specific items/keywords.
- For specific items like "cookies", "pizza", "uber" → use structured with description_keyword.
- Map the user's words to the closest available categories. "food" → ["Food", "Groceries"]. Be generous.
- Extract date ranges from temporal words: "last week" = 7 days ago to today, "yesterday" = yesterday, "this month" = 1st of month to today.
- Use "semantic" ONLY for truly vague/conceptual queries like "that big purchase", "stuff I regret", "fun things".
- If "semantic", also fill in any date/type filters you can extract — they help narrow results.
- Always set "semantic_query" when strategy is "semantic" (a rephrased, clear version of the query).
"""


async def _call_gemini(prompt: str, parse_json: bool = False):
    """Call Gemini with model fallback. Optionally parse JSON response."""
    for model_name in FALLBACK_MODELS:
        try:
            model = genai.GenerativeModel(model_name)
            response = await model.generate_content_async(prompt)
            text = response.text.strip()

            if not parse_json:
                return text

            # Clean markdown wrappers
            if text.startswith("```json"):
                text = text[7:]
            if text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]
            return json.loads(text.strip())

        except Exception as e:
            logger.warning("Gemini model failed", model=model_name, error=str(e))
            continue

    logger.error("All Gemini models failed")
    return None


async def classify_search_intent(
    query: str, available_categories: list[str]
) -> dict:
    """Phase 1: Classify the user's search query into structured filters or semantic."""
    today = date.today()
    prompt = _INTENT_PROMPT.format(
        today=today.strftime("%Y-%m-%d (%A)"),
        categories=", ".join(available_categories) if available_categories else "None yet",
        query=query,
    )

    intent = await _call_gemini(prompt, parse_json=True)

    if not intent or not isinstance(intent, dict):
        # Fallback: treat everything as semantic
        logger.warning("Intent classification failed, falling back to semantic")
        return {"strategy": "semantic", "semantic_query": query}

    # Parse dates from strings to date objects
    for key in ("date_from", "date_to"):
        if intent.get(key):
            try:
                intent[key] = date.fromisoformat(intent[key])
            except (ValueError, TypeError):
                intent[key] = None

    logger.info("Search intent classified", strategy=intent.get("strategy"), query=query)
    return intent


# ---------------------------------------------------------------------------
# Phase 2: Retrieval
# ---------------------------------------------------------------------------

async def retrieve_structured(session, user_id: int, intent: dict) -> list[Transaction]:
    """Retrieve via SQL using structured filters."""
    txns = await search_transactions_by_filters(
        session,
        user_id,
        categories=intent.get("categories"),
        date_from=intent.get("date_from"),
        date_to=intent.get("date_to"),
        txn_type=intent.get("type"),
        description_keyword=intent.get("description_keyword"),
    )
    logger.info("Structured retrieval", results=len(txns))
    return txns


async def retrieve_semantic(session, user_id: int, intent: dict) -> list[Transaction]:
    """Retrieve via vector search, then hydrate with full Transaction objects from DB."""
    semantic_query = intent.get("semantic_query") or ""
    txn_ids, _ = vector_search(user_id, semantic_query, n_results=10)

    if not txn_ids:
        return []

    txns = await get_transactions_by_ids(session, [i for i in txn_ids if i > 0])
    logger.info("Semantic retrieval", results=len(txns))
    return txns


# ---------------------------------------------------------------------------
# Phase 3: Stats Computation (Python — accurate, no LLM math)
# ---------------------------------------------------------------------------

def compute_stats(transactions: list[Transaction]) -> dict:
    """Pre-compute all numeric stats so the synthesis LLM never has to do math."""
    if not transactions:
        return {"count": 0, "total": 0.0}

    total = sum(float(t.amount) for t in transactions)
    by_category: dict[str, float] = {}
    for t in transactions:
        cat = t.category or "Other"
        by_category[cat] = by_category.get(cat, 0.0) + float(t.amount)

    dates = [t.txn_date for t in transactions]

    return {
        "count": len(transactions),
        "total": round(total, 2),
        "by_category": {k: round(v, 2) for k, v in sorted(by_category.items(), key=lambda x: -x[1])},
        "earliest_date": str(min(dates)),
        "latest_date": str(max(dates)),
    }


# ---------------------------------------------------------------------------
# Phase 4: Synthesis
# ---------------------------------------------------------------------------

_SYNTHESIS_PROMPT = """\
You are KharchaBOT, a smart personal finance assistant. Answer the user's question using the data below.

Today's date: {today}
User Question: "{query}"

PRE-COMPUTED STATISTICS (these are exact — use them directly, do NOT recalculate):
- Total transactions found: {count}
- Total amount: ₹{total:,.2f}
- By category: {by_category}
- Date range: {earliest_date} to {latest_date}

TRANSACTION DETAILS (most recent first):
{transactions}

Instructions:
- Lead with the direct answer (total, count, or list — whatever the user asked for).
- Use ₹ symbol. Format amounts with commas (e.g. ₹1,632.00).
- Keep it conversational and concise (under 10 lines ideally).
- If the data doesn't fully answer the question, say so and suggest /report or /summary.
- Do NOT dump raw IDs or internal fields. Present only what's useful.
- If there are many transactions, summarise — don't list every single one unless asked.
"""


def _format_txn_for_prompt(t: Transaction) -> str:
    """Convert a Transaction object into a clean string for the LLM prompt."""
    return (
        f"• {t.txn_date} | {t.type.upper()} | {t.category or 'Other'} | "
        f"₹{float(t.amount):,.2f} — {t.description or 'No description'}"
    )


async def synthesize_answer(
    query: str, transactions: list[Transaction], stats: dict
) -> str:
    """Phase 4: Generate a natural language answer from transactions + stats."""
    if stats["count"] == 0:
        return (
            "I couldn't find any transactions matching your search. 🤔\n\n"
            "Tips:\n"
            "• Try different keywords (e.g. /search groceries)\n"
            "• Use /history to see recent transactions\n"
            "• Use /report <start> <end> for exact date ranges"
        )

    today = date.today().strftime("%d %B %Y")

    # Cap transactions in prompt to avoid token overflow (stats are already complete)
    display_txns = transactions[:25]
    txn_text = "\n".join(_format_txn_for_prompt(t) for t in display_txns)
    if len(transactions) > 25:
        txn_text += f"\n... and {len(transactions) - 25} more transactions"

    prompt = _SYNTHESIS_PROMPT.format(
        today=today,
        query=query,
        count=stats["count"],
        total=stats["total"],
        by_category=", ".join(f"{k}: ₹{v:,.2f}" for k, v in stats["by_category"].items()),
        earliest_date=stats["earliest_date"],
        latest_date=stats["latest_date"],
        transactions=txn_text,
    )

    answer = await _call_gemini(prompt)
    if answer:
        return answer

    # Graceful fallback if all models fail: format stats ourselves
    lines = [f"📊 Results for: \"{query}\"\n"]
    lines.append(f"Found {stats['count']} transactions totalling ₹{stats['total']:,.2f}")
    lines.append(f"Period: {stats['earliest_date']} to {stats['latest_date']}\n")
    for cat, amount in stats["by_category"].items():
        lines.append(f"  {cat}: ₹{amount:,.2f}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main Pipeline
# ---------------------------------------------------------------------------

async def handle_search_query(query: str, user_id: int, session) -> str:
    """
    Main entry point for /search.
    
    Pipeline: Intent Classification → Retrieval → Stats → Synthesis
    """
    # Get available categories for better intent classification
    available_categories = await get_distinct_categories(session, user_id)

    # Phase 1: Classify intent
    intent = await classify_search_intent(query, available_categories)
    strategy = intent.get("strategy", "semantic")

    # Phase 2: Retrieve transactions
    if strategy == "structured":
        transactions = await retrieve_structured(session, user_id, intent)
        # If structured returned nothing, fall back to semantic
        if not transactions and intent.get("description_keyword"):
            logger.info("Structured search empty, falling back to semantic")
            intent["semantic_query"] = intent["description_keyword"]
            transactions = await retrieve_semantic(session, user_id, intent)
    else:
        transactions = await retrieve_semantic(session, user_id, intent)

    # Phase 3: Compute stats (in Python — accurate)
    stats = compute_stats(transactions)

    # Phase 4: Synthesize natural language answer
    return await synthesize_answer(query, transactions, stats)
