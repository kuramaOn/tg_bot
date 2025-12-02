#!/usr/bin/env python3
"""
Resource management for Video Downloader Bot
Manages concurrent downloads and system resources
"""

import asyncio
import logging
from typing import Dict, Optional
from contextlib import asynccontextmanager

from exceptions import ResourceError

logger = logging.getLogger(__name__)


class ResourceManager:
    """Manage bot resources and download limits."""
    
    def __init__(
        self,
        max_concurrent_downloads: int = 10,
        max_downloads_per_user: int = 2
    ):
        """
        Initialize resource manager.
        
        Args:
            max_concurrent_downloads: Maximum global concurrent downloads
            max_downloads_per_user: Maximum concurrent downloads per user
        """
        self.max_concurrent_downloads = max_concurrent_downloads
        self.max_downloads_per_user = max_downloads_per_user
        self.active_downloads: Dict[int, int] = {}  # task_id -> user_id
        self._lock = asyncio.Lock()
        
        logger.info(
            f"ResourceManager initialized: max_concurrent={max_concurrent_downloads}, "
            f"max_per_user={max_downloads_per_user}"
        )
    
    @asynccontextmanager
    async def download_slot(self, user_id: int):
        """
        Acquire a download slot with limits.
        
        Args:
            user_id: Telegram user ID
            
        Yields:
            None (context manager)
            
        Raises:
            ResourceError: If limits exceeded
        """
        task_id = None
        
        async with self._lock:
            # Check global limit
            active_count = len(self.active_downloads)
            if active_count >= self.max_concurrent_downloads:
                logger.warning(
                    f"Global download limit reached: {active_count}/{self.max_concurrent_downloads}"
                )
                raise ResourceError(
                    f"Server is busy ({active_count} active downloads). Please try again later."
                )
            
            # Check per-user limit
            user_active = sum(1 for uid in self.active_downloads.values() if uid == user_id)
            if user_active >= self.max_downloads_per_user:
                logger.info(
                    f"User {user_id} download limit reached: {user_active}/{self.max_downloads_per_user}"
                )
                raise ResourceError(
                    f"You have {user_active} active downloads. Please wait for them to complete."
                )
            
            # Allocate slot
            task_id = id(asyncio.current_task())
            self.active_downloads[task_id] = user_id
            logger.info(
                f"Download slot allocated for user {user_id}: "
                f"{len(self.active_downloads)}/{self.max_concurrent_downloads} active"
            )
        
        try:
            yield
        finally:
            # Release slot
            async with self._lock:
                if task_id in self.active_downloads:
                    self.active_downloads.pop(task_id)
                    logger.info(
                        f"Download slot released for user {user_id}: "
                        f"{len(self.active_downloads)}/{self.max_concurrent_downloads} active"
                    )
    
    async def get_status(self) -> Dict:
        """
        Get current resource usage status.
        
        Returns:
            Dict with status information
        """
        async with self._lock:
            active_count = len(self.active_downloads)
            user_counts = {}
            
            for user_id in self.active_downloads.values():
                user_counts[user_id] = user_counts.get(user_id, 0) + 1
            
            return {
                'active_downloads': active_count,
                'max_downloads': self.max_concurrent_downloads,
                'active_users': len(user_counts),
                'user_breakdown': user_counts
            }
    
    async def cancel_user_downloads(self, user_id: int) -> int:
        """
        Cancel all downloads for a specific user.
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            Number of downloads cancelled
        """
        cancelled = 0
        async with self._lock:
            tasks_to_cancel = [
                task_id for task_id, uid in self.active_downloads.items()
                if uid == user_id
            ]
            
            for task_id in tasks_to_cancel:
                self.active_downloads.pop(task_id, None)
                cancelled += 1
            
            if cancelled > 0:
                logger.info(f"Cancelled {cancelled} downloads for user {user_id}")
        
        return cancelled
    
    async def get_user_active_downloads(self, user_id: int) -> int:
        """
        Get number of active downloads for a user.
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            Number of active downloads
        """
        async with self._lock:
            return sum(1 for uid in self.active_downloads.values() if uid == user_id)


# Global resource manager instance
_resource_manager: Optional[ResourceManager] = None


def get_resource_manager() -> ResourceManager:
    """
    Get global resource manager instance.
    
    Returns:
        ResourceManager instance
    """
    global _resource_manager
    if _resource_manager is None:
        _resource_manager = ResourceManager()
    return _resource_manager


def init_resource_manager(max_concurrent: int = 10, max_per_user: int = 2):
    """
    Initialize global resource manager.
    
    Args:
        max_concurrent: Maximum concurrent downloads
        max_per_user: Maximum downloads per user
    """
    global _resource_manager
    _resource_manager = ResourceManager(max_concurrent, max_per_user)
    logger.info("Global resource manager initialized")
