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
    MSG_BATCH_LINKS_READY, MSG_BUTTON_DOWNLOAD, MSG_BUTTON_START_CHAT,
    MSG_BUTTON_STREAM_NOW, MSG_CRITICAL_ERROR, MSG_DM_BATCH_PREFIX,
    MSG_PROCESSING_REQUEST, MSG_LINKS, MSG_ERROR_PROCESSING_MEDIA
)
from Thunder.utils.rate_limiter import handle_rate_limited_request
from Thunder.vars import Var
from Thunder.bot.plugins.stream import (fwd_media, get_link_buttons, send_link,
                                        safe_edit_message, safe_delete_message,
                                        send_dm_links, send_channel_links)

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
    """Handle media groups in private chats - aggregates files into single link."""
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

            # Add file to media group cache
            file_info = {
                "message_id": message.id,
                "file_unique_id": get_file_unique_id(message),
                "file_name": get_file_name(message),
                "file_size": get_file_size(message),
                "chat_id": message.chat.id,
                "user_id": message.from_user.id
            }

            await MediaGroupHandler.add_media_to_group(
                media_group_id=media_group_id,
                **file_info
            )

            # Wait a bit for more messages in the group
            await asyncio.sleep(1)

            # Check if more messages are coming (heuristic: wait a bit more)
            await asyncio.sleep(2)

            # Process all files in the group
            group_files = await MediaGroupHandler.get_group_files(media_group_id)

            if not group_files:
                logger.debug(f"Media group {media_group_id} has no files to process.")
                return

            try:
                status_msg = await message.reply_text(MSG_PROCESSING_REQUEST, quote=True)
            except FloodWait as e:
                await asyncio.sleep(e.value)
                status_msg = await message.reply_text(MSG_PROCESSING_REQUEST, quote=True)

            # Process all files and collect links
            links_list = []
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
                        links = await gen_canonical_links(
                            file_name=canonical_record["file_name"],
                            file_size=int(canonical_record.get("file_size", 0) or 0),
                            public_hash=canonical_record["public_hash"],
                            shortener=shortener_val
                        )
                    else:
                        if not stored_msg:
                            stored_msg = await fwd_media(file_msg)
                            if not stored_msg:
                                logger.error(f"Failed to forward media for message {file_msg.id}.")
                                failed += 1
                                continue
                        links = await gen_links(stored_msg, shortener=shortener_val)

                    links_list.append({
                        "file_name": links['media_name'],
                        "link": links['online_link']
                    })
                    processed += 1

                except Exception as e:
                    logger.error(f"Error processing file in media group {media_group_id}: {e}", exc_info=True)
                    failed += 1

            # Send aggregated links
            if links_list:
                chunk_text = MSG_BATCH_LINKS_READY.format(count=len(links_list))
                chunk_text += "\n\n"
                for item in links_list:
                    chunk_text += f"📄 <b>{item['file_name']}</b>\n<code>{item['link']}</code>\n\n"

                try:
                    await message.reply_text(
                        chunk_text,
                        quote=True,
                        disable_web_page_preview=True,
                        parse_mode=enums.ParseMode.HTML
                    )
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                    await message.reply_text(
                        chunk_text,
                        quote=True,
                        disable_web_page_preview=True,
                        parse_mode=enums.ParseMode.HTML
                    )

                # Save metadata to database
                file_links = [item['link'] for item in links_list]
                await MediaGroupHandler.save_group_metadata(
                    media_group_id=media_group_id,
                    user_id=message.from_user.id,
                    chat_id=message.chat.id,
                    file_links=file_links
                )

            if status_msg:
                try:
                    await safe_edit_message(
                        status_msg,
                        f"✅ Processed {processed} files\n❌ Failed: {failed}"
                    )
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


@StreamBot.on_message(
    filters.command("link") & ~filters.private & filters.reply,
    group=2
)
async def media_group_link_command(bot: Client, msg: Message, **kwargs):
    """Handle /link command with optional 'batch' or 'group' modifier for media groups."""
    async def _actual_media_group_link_handler(client: Client, message: Message, **handler_kwargs):
        shortener_val = await validate_request_common(client, message)
        if shortener_val is None:
            return

        if message.from_user and not await db.is_user_exist(message.from_user.id):
            invite_link = f"https://t.me/{client.me.username}?start=start"
            try:
                await message.reply_text(
                    f"Please start the bot first: {invite_link}",
                    disable_web_page_preview=True,
                    quote=True
                )
            except FloodWait as e:
                await asyncio.sleep(e.value)
                await message.reply_text(
                    f"Please start the bot first: {invite_link}",
                    disable_web_page_preview=True,
                    quote=True
                )
            return

        if (message.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]
                and not await is_admin(client, message.chat.id)):
            await reply_user_err(message, "You must be an admin to use this command.")
            return

        parts = message.text.split()
        is_batch_mode = len(parts) > 1 and parts[1].lower() in ['batch', 'group']

        if not message.reply_to_message:
            await reply_user_err(message, "Reply to a media group message with /link batch")
            return

        replied_msg = message.reply_to_message

        # Check if replied message is part of a media group
        if not replied_msg.media_group_id and not is_batch_mode:
            await reply_user_err(message, "This message is not part of a media group.")
            return

        notification_msg = handler_kwargs.get('notification_msg')

        try:
            status_msg = await message.reply_text(MSG_PROCESSING_REQUEST, quote=True)
        except FloodWait as e:
            await asyncio.sleep(e.value)
            status_msg = await message.reply_text(MSG_PROCESSING_REQUEST, quote=True)

        media_group_id = replied_msg.media_group_id if replied_msg.media_group_id else f"manual_{replied_msg.id}"

        # If batch mode, get multiple messages from reply
        if is_batch_mode:
            # Get messages starting from replied message going down
            batch_size = 10
            try:
                try:
                    messages = await client.get_messages(
                        message.chat.id,
                        range(replied_msg.id, replied_msg.id + batch_size)
                    )
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                    messages = await client.get_messages(
                        message.chat.id,
                        range(replied_msg.id, replied_msg.id + batch_size)
                    )
            except Exception as e:
                logger.error(f"Error getting batch messages: {e}", exc_info=True)
                messages = [replied_msg]
        else:
            # Get all messages in the media group
            try:
                # Telegram doesn't provide direct API to get all messages in a group,
                # so we fetch a range around the replied message
                msg_range = 20
                try:
                    messages = await client.get_messages(
                        message.chat.id,
                        range(max(1, replied_msg.id - msg_range), replied_msg.id + msg_range)
                    )
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                    messages = await client.get_messages(
                        message.chat.id,
                        range(max(1, replied_msg.id - msg_range), replied_msg.id + msg_range)
                    )

                # Filter to only media group messages
                if replied_msg.media_group_id:
                    messages = [m for m in messages if m and m.media_group_id == replied_msg.media_group_id]
                else:
                    messages = [replied_msg]
            except Exception as e:
                logger.error(f"Error getting media group messages: {e}", exc_info=True)
                messages = [replied_msg]

        # Process all messages
        links_list = []
        processed = 0
        failed = 0

        for m in messages:
            if m and m.media:
                try:
                    links = await process_single_file(client, m, shortener_val)
                    if links:
                        links_list.append({
                            "file_name": links['media_name'],
                            "link": links['online_link']
                        })
                        processed += 1
                    else:
                        failed += 1
                except Exception as e:
                    logger.error(f"Error processing message {m.id}: {e}", exc_info=True)
                    failed += 1
            else:
                failed += 1

        # Send results
        if links_list:
            chunk_text = f"✅ **{len(links_list)} Files Ready**\n\n"
            for item in links_list:
                chunk_text += f"📄 `{item['file_name']}`\n`{item['link']}`\n\n"

            try:
                await message.reply_text(
                    chunk_text,
                    quote=True,
                    disable_web_page_preview=True,
                    parse_mode=enums.ParseMode.MARKDOWN
                )
            except FloodWait as e:
                await asyncio.sleep(e.value)
                await message.reply_text(
                    chunk_text,
                    quote=True,
                    disable_web_page_preview=True,
                    parse_mode=enums.ParseMode.MARKDOWN
                )

            # Save to database
            file_links = [item['link'] for item in links_list]
            await MediaGroupHandler.save_group_metadata(
                media_group_id=media_group_id,
                user_id=message.from_user.id if message.from_user else 0,
                chat_id=message.chat.id,
                file_links=file_links
            )

        if status_msg:
            try:
                await safe_edit_message(
                    status_msg,
                    f"✅ Processed: {processed} | ❌ Failed: {failed}"
                )
            except:
                pass

    await handle_rate_limited_request(bot, msg, _actual_media_group_link_handler, **kwargs)


async def validate_request_common(client: Client, message: Message) -> Optional[bool]:
    """Common validation for all requests."""
    if not await check_banned(client, message):
        return None
    if not await require_token(client, message):
        return None
    if not await force_channel_check(client, message):
        return None
    return await get_shortener_status(client, message)


async def process_single_file(client: Client, msg: Message, shortener_val: bool) -> Optional[dict]:
    """Process a single file and return links."""
    try:
        canonical_record, stored_msg, reused_existing = await get_or_create_canonical_file(msg, fwd_media)
        if reused_existing and stored_msg:
            await safe_delete_message(stored_msg)
            stored_msg = None

        if canonical_record:
            links = await gen_canonical_links(
                file_name=canonical_record["file_name"],
                file_size=int(canonical_record.get("file_size", 0) or 0),
                public_hash=canonical_record["public_hash"],
                shortener=shortener_val
            )
        else:
            if not stored_msg:
                stored_msg = await fwd_media(msg)
                if not stored_msg:
                    logger.error(f"Failed to forward media for message {msg.id}.")
                    return None
            links = await gen_links(stored_msg, shortener=shortener_val)

        return links
    except Exception as e:
        logger.error(f"Error processing file for message {msg.id}: {e}", exc_info=True)
        return None


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
