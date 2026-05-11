from telegram import Update
from telegram.ext import ContextTypes
from db.connection import async_session
from db.crud import get_or_create_user, add_transaction
from agents.collector_agent import parse_transaction_text, parse_transaction_image
from tools.pdf import extract_text_from_pdf
import structlog

logger = structlog.get_logger()


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle plain text messages to log expenses."""
    user_msg = update.message.text
    telegram_user = update.message.from_user

    if not user_msg or len(user_msg.strip()) < 4:
        await update.message.reply_text("Message too short. Try something like: 'spent 150 on groceries'")
        return

    processing_msg = await update.message.reply_text("Processing your transaction...")

    try:
        parsed_data = await parse_transaction_text(user_msg)
    except Exception as e:
        logger.error("Gemini call failed", error=str(e))
        await processing_msg.edit_text(f"[Gemini Error] {str(e)}")
        return

    if not parsed_data:
        await processing_msg.edit_text("Sorry, I couldn't understand the transaction details. Please try rephrasing.")
        return

    if "error" in parsed_data:
        await processing_msg.edit_text(f"Notice: {parsed_data['error']}")
        return

    try:
        async with async_session() as session:
            user = await get_or_create_user(session, telegram_id=telegram_user.id, name=telegram_user.first_name)
            txn = await add_transaction(session, user_id=user.id, data=parsed_data, raw_input=user_msg, source="manual")

            reply = (
                f"Transaction Logged!\n"
                f"Date: {txn.txn_date}\n"
                f"Amount: \u20b9{float(txn.amount):,.2f}\n"
                f"Category: {txn.category}\n"
                f"Note: {txn.description}\n"
                f"Type: {txn.type.capitalize()}"
            )
            await processing_msg.edit_text(reply)
    except Exception as e:
        logger.error("Database operation failed", error=str(e))
        await processing_msg.edit_text(f"[Database Error] {str(e)}")


async def handle_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle photo messages — send directly to Gemini multimodal, log to DB."""
    telegram_user = update.message.from_user
    processing_msg = await update.message.reply_text("Scanning receipt with AI...")

    photo = update.message.photo[-1]
    try:
        file = await context.bot.get_file(photo.file_id)
        byte_array = await file.download_as_bytearray()
        image_bytes = bytes(byte_array)
    except Exception as e:
        logger.error("Photo download failed", error=str(e))
        await processing_msg.edit_text(f"[Error] Could not download the image: {str(e)}")
        return

    try:
        parsed_data = await parse_transaction_image(image_bytes)
    except Exception as e:
        logger.error("Gemini image parse failed", error=str(e))
        await processing_msg.edit_text(f"[Gemini Error] {str(e)}")
        return

    if not parsed_data:
        await processing_msg.edit_text("Could not extract transaction details from this image.\nTip: Ensure the image contains clear financial information.")
        return

    if "error" in parsed_data:
        await processing_msg.edit_text(f"Notice: {parsed_data['error']}")
        return

    try:
        async with async_session() as session:
            user = await get_or_create_user(session, telegram_id=telegram_user.id, name=telegram_user.first_name)
            txn = await add_transaction(session, user_id=user.id, data=parsed_data, raw_input="<image_upload>", source="ocr")

        reply = (
            f"Receipt Scanned and Logged!\n"
            f"Date: {txn.txn_date}\n"
            f"Amount: \u20b9{txn.amount}\n"
            f"Category: {txn.category}\n"
            f"Note: {txn.description}\n"
            f"Type: {txn.type.capitalize()}\n"
            f"Source: AI Vision"
        )
        await processing_msg.edit_text(reply)
    except Exception as e:
        logger.error("Database save failed after OCR", error=str(e))
        await processing_msg.edit_text(f"[Database Error] {str(e)}")


async def handle_pdf_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle PDF messages — extract text locally, parse with Gemini, log to DB."""
    telegram_user = update.message.from_user
    doc = update.message.document

    if doc.file_size and doc.file_size > 5 * 1024 * 1024:
        await update.message.reply_text("This PDF is too large (max 5MB). Please send a smaller file.")
        return

    processing_msg = await update.message.reply_text("Extracting text from PDF...")

    try:
        file = await context.bot.get_file(doc.file_id)
        byte_array = await file.download_as_bytearray()
        pdf_bytes = bytes(byte_array)
    except Exception as e:
        logger.error("PDF download failed", error=str(e))
        await processing_msg.edit_text(f"[Error] Could not download the document: {str(e)}")
        return

    extracted_text = await extract_text_from_pdf(pdf_bytes)
    
    if not extracted_text:
        await processing_msg.edit_text("Could not find any readable text in this PDF. It might be scanned or image-based.")
        return

    await processing_msg.edit_text("Parsing PDF data with AI...")

    try:
        parsed_data = await parse_transaction_text(extracted_text)
    except Exception as e:
        logger.error("Gemini PDF parse failed", error=str(e))
        await processing_msg.edit_text(f"[Gemini Error] {str(e)}")
        return

    if not parsed_data:
        await processing_msg.edit_text("Could not extract transaction details from the text in this PDF.")
        return

    if "error" in parsed_data:
        await processing_msg.edit_text(f"Notice: {parsed_data['error']}")
        return

    try:
        async with async_session() as session:
            user = await get_or_create_user(session, telegram_id=telegram_user.id, name=telegram_user.first_name)
            txn = await add_transaction(session, user_id=user.id, data=parsed_data, raw_input=extracted_text[:1000], source="pdf")

        reply = (
            f"PDF Bill Logged!\n"
            f"Date: {txn.txn_date}\n"
            f"Amount: \u20b9{txn.amount}\n"
            f"Category: {txn.category}\n"
            f"Note: {txn.description}\n"
            f"Type: {txn.type.capitalize()}\n"
            f"Source: PDF Extract"
        )
        await processing_msg.edit_text(reply)
    except Exception as e:
        logger.error("Database save failed after PDF parse", error=str(e))
        await processing_msg.edit_text(f"[Database Error] {str(e)}")


