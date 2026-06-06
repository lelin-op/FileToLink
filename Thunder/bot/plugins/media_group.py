# Thunder/bot/plugins/media_group.py

import asyncio
from typing import Optional
from pyrogram import Client, enums, filters
from pyrogram.errors import FloodWait
from pyrogram.types import Message

from Thunder.bot import StreamBot
from Thunder.utils.bot_utils import (gen_canonical_links, gen_links, is_admin,
                                     log_newusr, notify_own, reply_user_err)
from Thunder.utils.canonical_files import get_or_create_canonical_file
from Thunder.utils.database import db
from Thunder.utils.decorators import (check_banned, get_shortener_status,
                                      require_token)
from Thunder.utils.force_channel import force_channel_check
from Thunder.utils.logger import logger
from Thunder.utils.media_group_handler import MediaGroupHandler
from Thunder.utils.messages import (
    MSG_BUTTON_DOWNLOAD, MSG_BUTTON_START_CHAT,
    MSG_BUTTON_STREAM_NOW, MSG_CRITICAL_ERROR,
    MSG_PROCESSING_REQUEST, MSG_LINKS
)
from Thunder.utils.rate_limiter import handle_rate_limited_request
from Thunder.utils.shortener import shorten
from Thunder.vars import Var
from Thunder.bot.plugins.stream import fwd_media, safe_delete_message

import secrets


@StreamBot.on_message(
    filters.private &
    filters.incoming &
    filters.media_group &
    (filters.document | filters.video | filters.photo | filters.audio |
     filters.voice | filters.animation | filters.video_note),
    group=3
)
async def private_media_group_handler(bot: Client, msg: Message, **kwargs):
    """Handle media groups in private chats - creates single collection link."""
    async def _actual_media_group_handler(client: Client, message: Message, **handler_kwargs):
        try:
            shortener_val = await validate_request_common(client, message)
            if shortener_val is None:
                return
            if not message.from_user:
                return

            media_group_id = message.media_group_id
            if not media_group_id:
                return

            notification_msg = handler_kwargs.get('notification_msg')

            # Log new user
            await log_newusr(client, message.from_user.id, message.from_user.first_name or "")

            # Get file info
            file_data = {
                "message_id": message.id,
                "file_unique_id": get_file_unique_id(message),
                "file_name": get_file_name(message),
                "file_size": get_file_size(message),
            }

            # Add to cache
            await MediaGroupHandler.add_media_to_group(
                media_group_id=media_group_id,
                public_hash="",  # Will be filled after processing
                chat_id=message.chat.id,
                user_id=message.from_user.id,
                **file_data
            )

            # Wait for all media group files to arrive
            await asyncio.sleep(1)

            # Process all files in the group
            group_files = await MediaGroupHandler.get_group_files(media_group_id)

            if not group_files or len(group_files) == 0:
                logger.debug(f"Media group {media_group_id} has no files to process.")
                return

            try:
                status_msg = await message.reply_text(MSG_PROCESSING_REQUEST, quote=True)
            except FloodWait as e:
                await asyncio.sleep(e.value)
                status_msg = await message.reply_text(MSG_PROCESSING_REQUEST, quote=True)

            # Process all files and collect their info
            processed_files = []
            processed = 0
            failed = 0

            for file_info in group_files:
                try:
                    # Get the actual message object
                    file_msg = await client.get_messages(message.chat.id, file_info["message_id"])
                    if not file_msg or not file_msg.media:
                        failed += 1
                        continue

                    # Process single file
                    canonical_record, stored_msg, reused_existing = await get_or_create_canonical_file(
                        file_msg, fwd_media
                    )
                    if reused_existing and stored_msg:
                        await safe_delete_message(stored_msg)
                        stored_msg = None

                    if canonical_record:
                        public_hash = canonical_record["public_hash"]
                        file_name = canonical_record["file_name"]
                        file_size = int(canonical_record.get("file_size", 0) or 0)
                    else:
                        if not stored_msg:
                            stored_msg = await fwd_media(file_msg)
                            if not stored_msg:
                                logger.error(f"Failed to forward media for message {file_msg.id}.")
                                failed += 1
                                continue
                        # Extract info from stored message
                        public_hash = f"{get_file_unique_id(stored_msg)}{stored_msg.id}"
                        file_name = get_file_name(stored_msg)
                        file_size = get_file_size(stored_msg)

                    processed_files.append({
                        "file_name": file_name,
                        "file_size": file_size,
                        "public_hash": public_hash,
                        "message_id": file_msg.id
                    })
                    processed += 1

                except Exception as e:
                    logger.error(f"Error processing file in media group {media_group_id}: {e}", exc_info=True)
                    failed += 1

            # Create collection link
            if processed_files:
                collection_id = await MediaGroupHandler.create_collection_link(
                    files_data=processed_files,
                    user_id=message.from_user.id,
                    chat_id=message.chat.id
                )

                if collection_id:
                    collection_link = await build_collection_link(collection_id, shortener_val)

                    # Send single collection link
                    link_text = f"📦 **Your {len(processed_files)} Files Collection**\n\n"
                    link_text += f"🔗 [View Collection]({collection_link})\n\n"
                    link_text += f"📊 Files: {len(processed_files)} | ✅ Processed: {processed} | ❌ Failed: {failed}"

                    try:
                        await message.reply_text(
                            link_text,
                            quote=True,
                            disable_web_page_preview=False,
                            parse_mode=enums.ParseMode.MARKDOWN
                        )
                    except FloodWait as e:
                        await asyncio.sleep(e.value)
                        await message.reply_text(
                            link_text,
                            quote=True,
                            disable_web_page_preview=False,
                            parse_mode=enums.ParseMode.MARKDOWN
                        )

            if status_msg:
                try:
                    await safe_delete_message(status_msg)
                except:
                    pass

            # Clean up cache
            await MediaGroupHandler.clear_group(media_group_id)

        except Exception as e:
            logger.error(f"Error in _actual_media_group_handler: {e}", exc_info=True)
            await notify_own(bot, MSG_CRITICAL_ERROR.format(
                error=str(e),
                error_id=secrets.token_hex(6)
            ))

    await handle_rate_limited_request(bot, msg, _actual_media_group_handler, **kwargs)


async def validate_request_common(client: Client, message: Message) -> Optional[bool]:
    """Common validation for all requests."""
    if not await check_banned(client, message):
        return None
    if not await require_token(client, message):
        return None
    if not await force_channel_check(client, message):
        return None
    return await get_shortener_status(client, message)


async def build_collection_link(collection_id: str, shortener: bool = True) -> str:
    """Build collection URL."""
    base_url = Var.URL.rstrip("/")
    collection_link = f"{base_url}/collection/{collection_id}"

    if shortener and getattr(Var, "SHORTEN_MEDIA_LINKS", False):
        try:
            shortened = await shorten(collection_link)
            return shortened
        except Exception as e:
            logger.warning(f"Failed to shorten collection link: {e}")
            return collection_link
    
    return collection_link


def get_file_unique_id(message: Message) -> str:
    """Extract file_unique_id from message."""
    if message.document:
        return message.document.file_unique_id
    elif message.video:
        return message.video.file_unique_id
    elif message.audio:
        return message.audio.file_unique_id
    elif message.photo:
        return message.photo.file_unique_id
    elif message.voice:
        return message.voice.file_unique_id
    elif message.animation:
        return message.animation.file_unique_id
    elif message.video_note:
        return message.video_note.file_unique_id
    return ""


def get_file_name(message: Message) -> str:
    """Extract file name from message."""
    if message.document:
        return message.document.file_name or f"Document_{message.id}"
    elif message.video:
        return message.video.file_name or f"Video_{message.id}"
    elif message.audio:
        return message.audio.file_name or f"Audio_{message.id}"
    elif message.photo:
        return f"Photo_{message.id}"
    elif message.voice:
        return f"Voice_{message.id}"
    elif message.animation:
        return message.animation.file_name or f"Animation_{message.id}"
    elif message.video_note:
        return f"VideoNote_{message.id}"
    return f"File_{message.id}"


def get_file_size(message: Message) -> int:
    """Extract file size from message."""
    if message.document:
        return message.document.file_size or 0
    elif message.video:
        return message.video.file_size or 0
    elif message.audio:
        return message.audio.file_size or 0
    elif message.photo:
        return message.photo.file_size or 0
    elif message.voice:
        return message.voice.file_size or 0
    elif message.animation:
        return message.animation.file_size or 0
    elif message.video_note:
        return message.video_note.file_size or 0
    return 0
