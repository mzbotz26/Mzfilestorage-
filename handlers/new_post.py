# widhvans/store/widhvans-store-9eccd1e4991c3966a09275ea218d1ea1248ed0fe/handlers/new_post.py

import logging
from pyrogram import Client, filters
from database.db import find_owner_by_index_channel
from utils.helpers import notify_and_remove_invalid_channel
import asyncio
from config import Config

logger = logging.getLogger(__name__)

# 🔥 Concurrency Limit (Ultra Stable)
file_process_semaphore = asyncio.Semaphore(2)

# 🔥 Smart Batch System (Non-breaking)
pending_batches = {}
batch_lock = asyncio.Lock()
BATCH_WAIT_TIME = 5  # seconds


@Client.on_message(filters.channel & (filters.document | filters.video | filters.audio), group=2)
async def new_file_handler(client, message):
    """
    This handler listens for new files, finds the owner,
    and processes them using controlled batching + concurrency.
    """

    try:
        user_id = await find_owner_by_index_channel(message.chat.id)
        if not user_id:
            return

        if not await notify_and_remove_invalid_channel(client, user_id, message.chat.id, "Index DB"):
            logger.warning(f"Aborted processing from inaccessible channel {message.chat.id} for user {user_id}")
            return

        media = getattr(message, message.media.value, None)
        if not media or not getattr(media, 'file_name', None):
            return

        if not client.owner_db_channel:
            logger.warning("Owner Database Channel not set by admin. Ignoring file.")
            try:
                await client.send_message(
                    Config.ADMIN_ID,
                    "⚠️ **Configuration Alert**\n\nA file was received, but I cannot process it because the `OWNER_DB_CHANNEL` is not set."
                )
            except Exception as e:
                logger.error(f"Failed to send configuration alert to admin: {e}")
            return

        # 🔥 SMART BATCH LOGIC (Safe Add-On)
        async with batch_lock:
            key = (message.chat.id, user_id)

            if key not in pending_batches:
                pending_batches[key] = []

            pending_batches[key].append(message)

        # If first file in batch → start timer
        if len(pending_batches[key]) == 1:

            async def process_batch():
                await asyncio.sleep(BATCH_WAIT_TIME)

                async with batch_lock:
                    messages = pending_batches.pop(key, [])

                # Controlled concurrency
                async with file_process_semaphore:
                    for msg in messages:
                        try:
                            await client.process_new_file(msg, user_id)
                        except Exception as e:
                            logger.exception(f"Error processing file in batch: {e}")

            asyncio.create_task(process_batch())

        logger.info(f"Added file '{media.file_name}' to smart batch for user {user_id}")

    except Exception as e:
        logger.exception(f"Error in new_file_handler before batching: {e}")
