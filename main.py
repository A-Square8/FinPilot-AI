import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application
import structlog

logger = structlog.get_logger()

# Placeholder for Bot Application
bot_app: Application = None # type: ignore

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle events for FastAPI."""
    global bot_app
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    webhook_url = os.getenv("TELEGRAM_WEBHOOK_URL", "")

    if bot_token:
        bot_app = Application.builder().token(bot_token).build()
        
        # Add basic /start handler for testing
        from telegram.ext import CommandHandler, MessageHandler, filters
        from bot.handlers import handle_text_message, handle_photo_message
        from bot.commands import (
            cmd_log, cmd_emi, cmd_summary,
            cmd_report, cmd_compare, cmd_export, cmd_history,
        )

        async def start_command(update: Update, context):
            await update.message.reply_text(
                "Hello, I'm FinPilot AI! I'm online and ready.\n"
                "Try saying: 'I spent 500 on dinner'\n\n"
                "Commands:\n"
                "/log <amount> <category> — manual entry\n"
                "/emi add <amount> <desc> — log an EMI\n"
                "/summary — this month's breakdown\n"
                "/report <start> <end> — custom date range\n"
                "/compare — income vs expenses\n"
                "/export — download CSV\n"
                "/history — last 10 transactions"
            )

        bot_app.add_handler(CommandHandler("start", start_command))
        bot_app.add_handler(CommandHandler("log", cmd_log))
        bot_app.add_handler(CommandHandler("emi", cmd_emi))
        bot_app.add_handler(CommandHandler("summary", cmd_summary))
        bot_app.add_handler(CommandHandler("report", cmd_report))
        bot_app.add_handler(CommandHandler("compare", cmd_compare))
        bot_app.add_handler(CommandHandler("export", cmd_export))
        bot_app.add_handler(CommandHandler("history", cmd_history))
        bot_app.add_handler(MessageHandler(filters.PHOTO, handle_photo_message))
        bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
        
        await bot_app.initialize()
        await bot_app.start()
        
        if webhook_url:
            await bot_app.bot.set_webhook(url=f"{webhook_url}/webhook/{bot_token}")
            logger.info("Webhook set successfully", url=webhook_url)
    else:
        logger.warning("TELEGRAM_BOT_TOKEN not set, bot disabled")

    yield

    # Teardown
    if bot_app:
        await bot_app.stop()
        await bot_app.shutdown()

app = FastAPI(title="FinPilot AI", lifespan=lifespan)

@app.get("/health")
async def health_check():
    """Health check endpoint for Koyeb/Uptime monitoring."""
    return {"status": "ok", "service": "FinPilot AI"}

@app.post("/webhook/{token}")
async def telegram_webhook(token: str, request: Request):
    """Endpoint for Telegram to send updates via Webhook."""
    if token != os.getenv("TELEGRAM_BOT_TOKEN"):
        return {"status": "error", "message": "Invalid token"}

    if not bot_app:
        return {"status": "error", "message": "Bot not initialized"}

    data = await request.json()
    update = Update.de_json(data, bot_app.bot)
    await bot_app.process_update(update)
    
    return {"status": "ok"}

@app.get("/")
async def root():
    return {"message": "Hello, I'm FinPilot AI! "}
