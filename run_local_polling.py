import sys
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from config.settings import settings
from bot.handlers import handle_text_message, handle_photo_message
from bot.commands import (
    cmd_log,
    cmd_emi,
    cmd_summary,
    cmd_report,
    cmd_compare,
    cmd_export,
    cmd_history,
)


def main():
    bot_token = settings.telegram_bot_token
    if not bot_token:
        print("Error: TELEGRAM_BOT_TOKEN not found in settings or .env file.")
        sys.exit(1)

    print("Initializing local polling bot...")

    # Build the Application
    bot_app = Application.builder().token(bot_token).build()

    # Define a custom start command for local testing
    async def start_command(update: Update, context):
        await update.message.reply_text(
            "Hello, I'm FinPilot AI! (Running locally via Polling Mode)\n"
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

    # Register handlers
    bot_app.add_handler(CommandHandler("start", start_command))
    bot_app.add_handler(CommandHandler("log", cmd_log))
    bot_app.add_handler(CommandHandler("emi", cmd_emi))
    bot_app.add_handler(CommandHandler("summary", cmd_summary))
    bot_app.add_handler(CommandHandler("report", cmd_report))
    bot_app.add_handler(CommandHandler("compare", cmd_compare))
    bot_app.add_handler(CommandHandler("export", cmd_export))
    bot_app.add_handler(CommandHandler("history", cmd_history))
    bot_app.add_handler(MessageHandler(filters.PHOTO, handle_photo_message))
    bot_app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message)
    )

    # Deleting webhook first is critical, otherwise Telegram won't allow polling
    async def delete_webhook_and_start():
        await bot_app.bot.delete_webhook()
        print("Telegram Webhook cleared successfully.")

    # Initialize the app and delete webhook before polling starts
    bot_app.post_init = lambda app: delete_webhook_and_start()

    print("Starting bot polling. Send messages in Telegram to test!")
    print("Press Ctrl+C to stop.")

    # run_polling handles the event loop and stays active
    bot_app.run_polling()


if __name__ == "__main__":
    main()
