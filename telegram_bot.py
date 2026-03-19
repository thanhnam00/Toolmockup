"""
Telegram Bot for Google Flow Image Generation
Receives image + prompt from Telegram, calls flow_server, returns generated images.
"""

import asyncio
import base64
import io
import logging
import os
import time

import httpx
from telegram import Update, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8799245561:AAHFVQeUXAfUEYWL22oTu2wB-Z4QsR3k1yM")
FLOW_SERVER_URL = os.environ.get("FLOW_SERVER_URL", "http://127.0.0.1:5000")
ALLOWED_USERS = os.environ.get("ALLOWED_USERS", "")  # comma-separated user IDs, empty = allow all

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("telegram_bot")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def is_allowed(user_id: int) -> bool:
    """Check if user is allowed to use the bot."""
    if not ALLOWED_USERS:
        return True
    allowed = [int(x.strip()) for x in ALLOWED_USERS.split(",") if x.strip()]
    return user_id in allowed


async def call_flow_server(prompt: str, image_base64: str = None) -> dict:
    """Call the flow_server /generate endpoint."""
    payload = {"prompt": prompt}
    if image_base64:
        payload["image_base64"] = image_base64

    async with httpx.AsyncClient(timeout=200.0) as client:
        resp = await client.post(f"{FLOW_SERVER_URL}/generate", json=payload)
        resp.raise_for_status()
        return resp.json()


async def download_image(url: str) -> bytes:
    """Download image from flow_server proxy."""
    # The URL from flow_server is like /api/image?url=...
    # We call flow_server directly
    if url.startswith("/api/"):
        url = f"{FLOW_SERVER_URL}/{url[5:]}"  # /api/image?url=... -> http://127.0.0.1:5000/image?url=...

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    await update.message.reply_text(
        "Xin chao! Toi la bot tao anh Google Flow.\n\n"
        "Cach su dung:\n"
        "1. Gui anh mau kem caption (mo ta) de tao anh moi\n"
        "2. Gui text de tao anh khong can anh mau\n\n"
        "Vi du: Gui anh con ga + caption 'doi mau sang do'\n\n"
        "Lenh:\n"
        "/start - Hien thi huong dan\n"
        "/status - Kiem tra trang thai server\n"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check flow_server health."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{FLOW_SERVER_URL}/health")
            data = resp.json()
            status = "OK" if data.get("browser_ready") else "Chua san sang"
            await update.message.reply_text(f"Server: {status}\nBrowser: {'Ready' if data.get('browser_ready') else 'Not ready'}")
    except Exception as e:
        await update.message.reply_text(f"Server khong phan hoi: {e}")


# ---------------------------------------------------------------------------
# Message handlers
# ---------------------------------------------------------------------------
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photo messages (with optional caption as prompt)."""
    user = update.effective_user
    if not is_allowed(user.id):
        await update.message.reply_text("Ban khong co quyen su dung bot nay.")
        return

    prompt = update.message.caption or ""
    if not prompt.strip():
        await update.message.reply_text(
            "Vui long gui anh kem caption (mo ta).\n"
            "Vi du: Gui anh + caption 'doi mau sang do'"
        )
        return

    # Get the highest resolution photo
    photo = update.message.photo[-1]
    status_msg = await update.message.reply_text(
        f"Dang tao anh voi Google Flow...\nPrompt: {prompt}\nVui long doi 30-90 giay..."
    )

    try:
        # Download photo from Telegram
        file = await context.bot.get_file(photo.file_id)
        photo_bytes = await file.download_as_bytearray()
        image_base64 = base64.b64encode(bytes(photo_bytes)).decode()

        log.info(f"User {user.id} ({user.username}): photo + prompt='{prompt[:50]}...'")

        # Call flow_server
        start_time = time.time()
        result = await call_flow_server(prompt, image_base64)
        elapsed = time.time() - start_time

        images = result.get("images", [])
        if not images:
            await status_msg.edit_text("Khong nhan duoc anh tu Google Flow. Thu lai sau.")
            return

        await status_msg.edit_text(f"Da tao {len(images)} anh trong {result.get('elapsed_seconds', elapsed):.0f}s. Dang gui...")

        # Download and send each image
        for i, img_url in enumerate(images):
            try:
                img_data = await download_image(img_url)
                await update.message.reply_photo(
                    photo=img_data,
                    caption=f"Ket qua {i+1}/{len(images)} - {prompt[:100]}"
                )
            except Exception as e:
                log.error(f"Failed to send image {i+1}: {e}")
                await update.message.reply_text(f"Loi gui anh {i+1}: {e}")

        await status_msg.delete()

    except httpx.HTTPStatusError as e:
        error_detail = e.response.text[:200] if e.response else str(e)
        log.error(f"Flow server error: {error_detail}")
        await status_msg.edit_text(f"Loi server: {error_detail}")
    except Exception as e:
        log.error(f"Generation error: {e}")
        await status_msg.edit_text(f"Loi: {str(e)[:200]}")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text-only messages (generate without reference image)."""
    user = update.effective_user
    if not is_allowed(user.id):
        await update.message.reply_text("Ban khong co quyen su dung bot nay.")
        return

    prompt = update.message.text.strip()
    if not prompt:
        return

    # Ignore commands
    if prompt.startswith("/"):
        return

    status_msg = await update.message.reply_text(
        f"Dang tao anh voi Google Flow...\nPrompt: {prompt}\nVui long doi 30-90 giay..."
    )

    try:
        log.info(f"User {user.id} ({user.username}): text prompt='{prompt[:50]}...'")

        start_time = time.time()
        result = await call_flow_server(prompt)
        elapsed = time.time() - start_time

        images = result.get("images", [])
        if not images:
            await status_msg.edit_text("Khong nhan duoc anh tu Google Flow. Thu lai sau.")
            return

        await status_msg.edit_text(f"Da tao {len(images)} anh trong {result.get('elapsed_seconds', elapsed):.0f}s. Dang gui...")

        for i, img_url in enumerate(images):
            try:
                img_data = await download_image(img_url)
                await update.message.reply_photo(
                    photo=img_data,
                    caption=f"Ket qua {i+1}/{len(images)} - {prompt[:100]}"
                )
            except Exception as e:
                log.error(f"Failed to send image {i+1}: {e}")
                await update.message.reply_text(f"Loi gui anh {i+1}: {e}")

        await status_msg.delete()

    except httpx.HTTPStatusError as e:
        error_detail = e.response.text[:200] if e.response else str(e)
        log.error(f"Flow server error: {error_detail}")
        await status_msg.edit_text(f"Loi server: {error_detail}")
    except Exception as e:
        log.error(f"Generation error: {e}")
        await status_msg.edit_text(f"Loi: {str(e)[:200]}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    if TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        log.error("Please set TELEGRAM_BOT_TOKEN environment variable!")
        log.error("Get a token from @BotFather on Telegram")
        return

    log.info("Starting Telegram bot...")
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Register handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    log.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
