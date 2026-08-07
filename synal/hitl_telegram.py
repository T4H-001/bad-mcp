import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from synal.ledger import get_pending_hitl, approve_chunk, reject_chunk

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
AUTHORIZED_USER_ID = int(os.getenv("AUTHORIZED_USER_ID", "6972032328"))

def is_authorized(update: Update) -> bool:
    user = update.effective_user
    if not user or user.id != AUTHORIZED_USER_ID:
        logging.warning(f"Unauthorized access attempt by ID: {user.id if user else 'Unknown'}")
        return False
    return True

async def poll_pending_chunks(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        pending = get_pending_hitl()
        for chunk in pending:
            msg = (
                f"🚨 *CHUNK APPROVAL REQUIRED*\n\n"
                f"• *Chunk ID:* `{chunk['id']}`\n"
                f"• *Workflow:* `{chunk.get('workflow_id')}`\n"
                f"• *Payload Hash:* `{chunk.get('payload_hash')}`\n"
                f"• *Status:* `{chunk.get('status')}`\n\n"
                f"To approve: `/approve {chunk['id']}`\n"
                f"To reject: `/reject {chunk['id']} <reason>`"
            )
            await context.bot.send_message(chat_id=AUTHORIZED_USER_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Error checking pending chunks: {e}")

async def approve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update) or not update.message:
        return
    if not context.args:
        await update.message.reply_text("Usage: /approve <chunk_id>")
        return
    chunk_id = context.args[0]
    result = approve_chunk(chunk_id)
    if result:
        await update.message.reply_text(f"✅ Chunk `{chunk_id}` APPROVED.")
    else:
        await update.message.reply_text(f"❌ Failed to approve chunk `{chunk_id}`.")

async def reject_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update) or not update.message:
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /reject <chunk_id> <reason>")
        return
    chunk_id = context.args[0]
    reason = " ".join(context.args[1:])
    result = reject_chunk(chunk_id, reason)
    if result:
        await update.message.reply_text(f"🚫 Chunk `{chunk_id}` REJECTED. Reason: {reason}")
    else:
        await update.message.reply_text(f"❌ Failed to reject chunk `{chunk_id}`.")

def main():
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("Missing TELEGRAM_BOT_TOKEN environment variable.")
    
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("approve", approve_cmd))
    app.add_handler(CommandHandler("reject", reject_cmd))
    
    job_queue = app.job_queue
    if job_queue:
        job_queue.run_repeating(poll_pending_chunks, interval=10, first=1)
        
    logging.info("Synal Telegram HITL Daemon listening...")
    app.run_polling()

if __name__ == "__main__":
    main()
