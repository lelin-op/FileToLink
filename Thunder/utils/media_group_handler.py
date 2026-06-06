# Thunder/utils/media_group_handler.py

import asyncio
import datetime
from typing import Any, Dict, List, Optional
from Thunder.utils.database import db
from Thunder.utils.logger import logger
import uuid


class MediaGroupHandler:
    """Handles aggregation of files from Telegram media groups into a single collection link."""
    
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
        public_hash: str,
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
                "public_hash": public_hash,
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
    async def create_collection_link(
        cls,
        files_data: List[Dict[str, Any]],
        user_id: int,
        chat_id: int
    ) -> Optional[str]:
        """
        Create a single collection link for multiple files.
        Returns a collection ID that can be used to access all files through index page.
        """
        try:
            # Generate unique collection ID
            collection_id = str(uuid.uuid4())[:8]
            
            # Save to database
            collection_record = {
                "collection_id": collection_id,
                "user_id": user_id,
                "chat_id": chat_id,
                "files": files_data,
                "file_count": len(files_data),
                "created_at": datetime.datetime.now(datetime.timezone.utc),
                "total_size": sum(f.get("file_size", 0) for f in files_data)
            }
            
            await db.save_media_collection(collection_record)
            logger.debug(f"Created collection {collection_id} with {len(files_data)} files.")
            
            return collection_id
        except Exception as e:
            logger.error(f"Error creating collection link: {e}", exc_info=True)
            return None
    
    @classmethod
    async def get_collection_metadata(cls, collection_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve collection metadata from database."""
        try:
            return await db.get_media_collection(collection_id)
        except Exception as e:
            logger.error(f"Error getting collection metadata for {collection_id}: {e}", exc_info=True)
            return None
