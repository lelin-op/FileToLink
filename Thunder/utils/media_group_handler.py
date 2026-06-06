# Thunder/utils/media_group_handler.py

import asyncio
import datetime
from typing import Any, Dict, List, Optional
from Thunder.utils.database import db
from Thunder.utils.logger import logger


class MediaGroupHandler:
    """Handles aggregation of files from Telegram media groups."""
    
    # Cache for in-flight media groups (media_group_id -> list of file info)
    _group_cache: Dict[str, List[Dict[str, Any]]] = {}
    _cache_timeout = 30  # seconds
    
    @classmethod
    async def add_media_to_group(
        cls,
        media_group_id: str,
        message_id: int,
        file_unique_id: str,
        file_name: str,
        file_size: int,
        chat_id: int,
        user_id: int
    ) -> None:
        """Add a file to a media group cache."""
        try:
            if media_group_id not in cls._group_cache:
                cls._group_cache[media_group_id] = []
                # Schedule cleanup
                asyncio.create_task(cls._cleanup_group_after_timeout(media_group_id))
            
            cls._group_cache[media_group_id].append({
                "message_id": message_id,
                "file_unique_id": file_unique_id,
                "file_name": file_name,
                "file_size": file_size,
                "chat_id": chat_id,
                "user_id": user_id,
                "timestamp": datetime.datetime.now(datetime.timezone.utc)
            })
            logger.debug(f"Added file {file_unique_id} to media group {media_group_id}. Total: {len(cls._group_cache[media_group_id])}")
        except Exception as e:
            logger.error(f"Error adding media to group {media_group_id}: {e}", exc_info=True)
    
    @classmethod
    async def get_group_files(cls, media_group_id: str) -> Optional[List[Dict[str, Any]]]:
        """Retrieve all files in a media group."""
        try:
            if media_group_id in cls._group_cache:
                return cls._group_cache[media_group_id]
            return None
        except Exception as e:
            logger.error(f"Error getting group files for {media_group_id}: {e}", exc_info=True)
            return None
    
    @classmethod
    async def clear_group(cls, media_group_id: str) -> None:
        """Clear a media group from cache."""
        try:
            if media_group_id in cls._group_cache:
                del cls._group_cache[media_group_id]
                logger.debug(f"Cleared media group {media_group_id} from cache.")
        except Exception as e:
            logger.error(f"Error clearing group {media_group_id}: {e}", exc_info=True)
    
    @classmethod
    async def _cleanup_group_after_timeout(cls, media_group_id: str) -> None:
        """Auto-cleanup media group after timeout."""
        try:
            await asyncio.sleep(cls._cache_timeout)
            if media_group_id in cls._group_cache:
                logger.debug(f"Auto-cleaning expired media group {media_group_id}.")
                await cls.clear_group(media_group_id)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in cleanup for media group {media_group_id}: {e}", exc_info=True)
    
    @classmethod
    async def save_group_metadata(
        cls,
        media_group_id: str,
        user_id: int,
        chat_id: int,
        file_links: List[str]
    ) -> None:
        """Save media group metadata to database for persistence."""
        try:
            group_record = {
                "media_group_id": media_group_id,
                "user_id": user_id,
                "chat_id": chat_id,
                "file_links": file_links,
                "created_at": datetime.datetime.now(datetime.timezone.utc),
                "link_count": len(file_links)
            }
            await db.save_media_group_metadata(group_record)
            logger.debug(f"Saved metadata for media group {media_group_id} with {len(file_links)} links.")
        except Exception as e:
            logger.error(f"Error saving group metadata for {media_group_id}: {e}", exc_info=True)
    
    @classmethod
    async def get_group_metadata(cls, media_group_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve media group metadata from database."""
        try:
            return await db.get_media_group_metadata(media_group_id)
        except Exception as e:
            logger.error(f"Error getting group metadata for {media_group_id}: {e}", exc_info=True)
            return None
