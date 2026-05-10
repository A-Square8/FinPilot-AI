from telegram import Update
from telegram.ext import ContextTypes
from db.connection import async_session
from db.crud import get_or_create_user, add_transaction
from agents.collector_agent import parse_transaction_text
import structlog

logger = structlog.get_logger()

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle plain text messages to log expenses."""
    user_msg = update.message.text
    telegram_user = update.message.from_user
    
    # Let user know we are processing
    processing_msg = await update.message.reply_text("Processing your transaction...")
    
    try:
        async with async_session() as session:
            # Ensure user exists
            user = await get_or_create_user(
                session, 
                telegram_id=telegram_user.id, 
                name=telegram_user.first_name
            )
            
            # Parse text using Gemini
            parsed_data = await parse_transaction_text(user_msg)
            
            if not parsed_data:
                await processing_msg.edit_text("Sorry, I couldn't understand the transaction details. Please try rephrasing.")
                return
                
            # Save to database
            txn = await add_transaction(
                session, 
                user_id=user.id, 
                data=parsed_data, 
                raw_input=user_msg, 
                source="manual"
            )
            
            # Format a nice reply
            reply = (
                f"Transaction Logged!\n"
                f"Amount: {txn.amount}\n"
                f"Category: {txn.category}\n"
                f"Note: {txn.description}\n"
                f"Type: {txn.type.capitalize()}"
            )
            
            await processing_msg.edit_text(reply)
            
    except Exception as e:
        logger.error("Handler crashed", error=str(e))
        await processing_msg.edit_text(f"Error: {str(e)}")
