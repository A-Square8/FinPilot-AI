import csv
import io
from datetime import date, datetime, timedelta
from telegram import Update
from telegram.ext import ContextTypes
from db.connection import async_session
from db.crud import (
    get_or_create_user,
    add_transaction,
    get_transactions_by_date_range,
    get_monthly_summary,
    get_emi_transactions,
    get_recent_history,
)
import structlog

logger = structlog.get_logger()


async def cmd_log(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /log <amount> <category> [description] — manual structured entry."""
    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text(
            "Usage: /log <amount> <category> [description]\n"
            "Example: /log 500 Food Zomato order"
        )
        return

    try:
        amount = float(args[0].replace(",", "").replace("\u20b9", ""))
    except ValueError:
        await update.message.reply_text("Invalid amount. Use a number, e.g. /log 500 Food")
        return

    category = args[1].capitalize()
    description = " ".join(args[2:]) if len(args) > 2 else category
    data = {"amount": amount, "type": "expense", "category": category, "description": description}

    async with async_session() as session:
        user = await get_or_create_user(session, update.message.from_user.id, update.message.from_user.first_name)
        txn = await add_transaction(session, user.id, data, raw_input=update.message.text, source="manual")

    await update.message.reply_text(
        f"Transaction Logged!\n"
        f"Amount: \u20b9{txn.amount}\n"
        f"Category: {txn.category}\n"
        f"Note: {txn.description}\n"
        f"Type: Expense"
    )


async def cmd_emi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /emi add <amount> <description> — log a recurring EMI."""
    args = context.args
    if not args or args[0].lower() != "add" or len(args) < 3:
        # List existing EMIs if no add keyword
        async with async_session() as session:
            user = await get_or_create_user(session, update.message.from_user.id, update.message.from_user.first_name)
            emis = await get_emi_transactions(session, user.id)

        if not emis:
            await update.message.reply_text(
                "No EMIs logged yet.\nTo add: /emi add <amount> <description>\n"
                "Example: /emi add 3200 Car Loan"
            )
            return

        lines = ["Your EMIs:"]
        for e in emis:
            lines.append(f"- {e.description}: \u20b9{e.amount} ({e.txn_date})")
        await update.message.reply_text("\n".join(lines))
        return

    try:
        amount = float(args[1].replace(",", "").replace("\u20b9", ""))
    except ValueError:
        await update.message.reply_text("Invalid amount. Example: /emi add 3200 Car Loan")
        return

    description = " ".join(args[2:])
    data = {"amount": amount, "type": "emi", "category": "EMI", "description": description}

    async with async_session() as session:
        user = await get_or_create_user(session, update.message.from_user.id, update.message.from_user.first_name)
        txn = await add_transaction(session, user.id, data, raw_input=update.message.text, source="manual")

    await update.message.reply_text(
        f"EMI Logged!\n"
        f"Amount: \u20b9{txn.amount}\n"
        f"Description: {txn.description}"
    )


async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /summary [month] [year] — monthly income vs expense breakdown."""
    today = date.today()
    year, month = today.year, today.month

    if context.args and len(context.args) >= 2:
        try:
            month = int(context.args[0])
            year = int(context.args[1])
        except ValueError:
            await update.message.reply_text("Usage: /summary [month] [year]\nExample: /summary 4 2026")
            return

    async with async_session() as session:
        user = await get_or_create_user(session, update.message.from_user.id, update.message.from_user.first_name)
        data = await get_monthly_summary(session, user.id, year, month)

    income = data["income"]
    expenses = data["expenses"]
    net = income - expenses
    net_label = "Saved" if net >= 0 else "Deficit"

    lines = [
        f"Summary for {datetime(year, month, 1).strftime('%B %Y')}",
        f"Income:   \u20b9{income:,.2f}",
        f"Expenses: \u20b9{expenses:,.2f}",
        f"{net_label}: \u20b9{abs(net):,.2f}",
    ]

    if data["top_categories"]:
        lines.append("\nTop Spending Categories:")
        for item in data["top_categories"]:
            lines.append(f"  {item['category']}: \u20b9{item['total']:,.2f}")

    await update.message.reply_text("\n".join(lines))


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /report <YYYY-MM-DD> <YYYY-MM-DD> — transactions for a custom date range."""
    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text(
            "Usage: /report <start-date> <end-date>\n"
            "Example: /report 2026-04-01 2026-04-30"
        )
        return

    try:
        start = date.fromisoformat(args[0])
        end = date.fromisoformat(args[1])
    except ValueError:
        await update.message.reply_text("Invalid date format. Use YYYY-MM-DD, e.g. 2026-04-01")
        return

    if start > end:
        await update.message.reply_text("Start date must be before end date.")
        return

    async with async_session() as session:
        user = await get_or_create_user(session, update.message.from_user.id, update.message.from_user.first_name)
        txns = await get_transactions_by_date_range(session, user.id, start, end)

    if not txns:
        await update.message.reply_text(f"No transactions found between {start} and {end}.")
        return

    total_in = sum(t.amount for t in txns if t.type == "income")
    total_out = sum(t.amount for t in txns if t.type in ("expense", "emi"))

    lines = [f"Report: {start} to {end}", f"Total Income:   \u20b9{total_in:,.2f}", f"Total Expenses: \u20b9{total_out:,.2f}", ""]
    for t in txns[:15]:
        sign = "+" if t.type == "income" else "-"
        lines.append(f"{t.txn_date}  {sign}\u20b9{t.amount:<8} {t.category or 'Other'} — {t.description or ''}")

    if len(txns) > 15:
        lines.append(f"\n...and {len(txns) - 15} more. Use /export for the full list.")

    await update.message.reply_text("\n".join(lines))


async def cmd_compare(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /compare [month|year] — income vs expense ratio for current period."""
    mode = (context.args[0].lower() if context.args else "month")
    today = date.today()

    if mode == "year":
        start = date(today.year, 1, 1)
        end = today
        label = f"Year {today.year}"
    else:
        start = date(today.year, today.month, 1)
        end = today
        label = today.strftime("%B %Y")

    async with async_session() as session:
        user = await get_or_create_user(session, update.message.from_user.id, update.message.from_user.first_name)
        txns = await get_transactions_by_date_range(session, user.id, start, end)

    income = sum(t.amount for t in txns if t.type == "income")
    expenses = sum(t.amount for t in txns if t.type in ("expense", "emi"))
    net = income - expenses
    ratio = (expenses / income * 100) if income > 0 else 0

    status = "On track" if ratio <= 80 else ("Warning: high spend" if ratio <= 100 else "Overspent")

    await update.message.reply_text(
        f"Income vs Expenses ({label})\n"
        f"Income:   \u20b9{income:,.2f}\n"
        f"Expenses: \u20b9{expenses:,.2f}\n"
        f"Net:      \u20b9{net:,.2f}\n"
        f"Spend ratio: {ratio:.1f}% of income\n"
        f"Status: {status}"
    )


async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /export — send last 90 days of transactions as a CSV file."""
    today = date.today()
    start = today - timedelta(days=90)

    async with async_session() as session:
        user = await get_or_create_user(session, update.message.from_user.id, update.message.from_user.first_name)
        txns = await get_transactions_by_date_range(session, user.id, start, today)

    if not txns:
        await update.message.reply_text("No transactions in the last 90 days to export.")
        return

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Type", "Category", "Amount", "Description", "Source"])
    for t in txns:
        writer.writerow([t.txn_date, t.type, t.category or "", t.amount, t.description or "", t.source])

    output.seek(0)
    filename = f"finpilot_export_{today}.csv"
    await update.message.reply_document(
        document=io.BytesIO(output.getvalue().encode("utf-8")),
        filename=filename,
        caption=f"Transactions from {start} to {today} ({len(txns)} entries)"
    )


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /history — show the last 10 transactions."""
    async with async_session() as session:
        user = await get_or_create_user(session, update.message.from_user.id, update.message.from_user.first_name)
        txns = await get_recent_history(session, user.id, limit=10)

    if not txns:
        await update.message.reply_text("No transactions found. Start by sending an expense!")
        return

    lines = ["Last 10 Transactions:"]
    for t in txns:
        sign = "+" if t.type == "income" else "-"
        lines.append(f"{t.txn_date}  {sign}\u20b9{t.amount:<8} {t.category or 'Other'} — {t.description or ''}")

    await update.message.reply_text("\n".join(lines))
