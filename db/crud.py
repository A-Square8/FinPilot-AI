from datetime import date
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from db.models import User, Transaction
import structlog

logger = structlog.get_logger()


async def get_or_create_user(session: AsyncSession, telegram_id: int, name: str | None = None) -> User:
    """Retrieve an existing user by telegram_id, or create them if they don't exist."""
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalars().first()

    if not user:
        user = User(telegram_id=telegram_id, name=name)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        logger.info("New user created", telegram_id=telegram_id, name=name)

    return user


async def add_transaction(
    session: AsyncSession,
    user_id: int,
    data: dict,
    raw_input: str,
    source: str = "manual",
) -> Transaction:
    """Add a new transaction to the database."""
    txn = Transaction(
        user_id=user_id,
        amount=data.get("amount", 0.0),
        type=data.get("type", "expense"),
        category=data.get("category"),
        description=data.get("description"),
        raw_input=raw_input,
        source=source,
    )
    session.add(txn)
    await session.commit()
    await session.refresh(txn)
    logger.info("Transaction added", user_id=user_id, amount=txn.amount, type=txn.type)
    return txn


async def get_transactions_by_date_range(
    session: AsyncSession, user_id: int, start: date, end: date
) -> list[Transaction]:
    """Return all transactions for a user within a date range (inclusive)."""
    result = await session.execute(
        select(Transaction)
        .where(
            and_(
                Transaction.user_id == user_id,
                Transaction.txn_date >= start,
                Transaction.txn_date <= end,
            )
        )
        .order_by(Transaction.txn_date.desc())
    )
    return list(result.scalars().all())


async def get_monthly_summary(
    session: AsyncSession, user_id: int, year: int, month: int
) -> dict:
    """Return total income, total expenses, and top 5 categories for a given month."""
    # Total income
    income_result = await session.execute(
        select(func.sum(Transaction.amount)).where(
            and_(
                Transaction.user_id == user_id,
                Transaction.type == "income",
                func.extract("year", Transaction.txn_date) == year,
                func.extract("month", Transaction.txn_date) == month,
            )
        )
    )
    # Total expenses
    expense_result = await session.execute(
        select(func.sum(Transaction.amount)).where(
            and_(
                Transaction.user_id == user_id,
                Transaction.type == "expense",
                func.extract("year", Transaction.txn_date) == year,
                func.extract("month", Transaction.txn_date) == month,
            )
        )
    )
    # Top categories by total spend
    category_result = await session.execute(
        select(Transaction.category, func.sum(Transaction.amount).label("total"))
        .where(
            and_(
                Transaction.user_id == user_id,
                Transaction.type == "expense",
                func.extract("year", Transaction.txn_date) == year,
                func.extract("month", Transaction.txn_date) == month,
            )
        )
        .group_by(Transaction.category)
        .order_by(func.sum(Transaction.amount).desc())
        .limit(5)
    )
    return {
        "income": float(income_result.scalar() or 0),
        "expenses": float(expense_result.scalar() or 0),
        "top_categories": [
            {"category": row.category, "total": float(row.total)}
            for row in category_result.all()
        ],
    }


async def get_emi_transactions(session: AsyncSession, user_id: int) -> list[Transaction]:
    """Return all EMI transactions for a user, most recent first."""
    result = await session.execute(
        select(Transaction)
        .where(
            and_(Transaction.user_id == user_id, Transaction.type == "emi")
        )
        .order_by(Transaction.txn_date.desc())
    )
    return list(result.scalars().all())


async def get_recent_history(
    session: AsyncSession, user_id: int, limit: int = 10
) -> list[Transaction]:
    """Return the most recent N transactions for a user."""
    result = await session.execute(
        select(Transaction)
        .where(Transaction.user_id == user_id)
        .order_by(Transaction.txn_date.desc(), Transaction.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
