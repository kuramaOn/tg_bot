#!/usr/bin/env python3
"""
Utility functions for Video Downloader Bot
Retry logic, timeouts, and helper functions
"""

import asyncio
import os
import logging
from functools import wraps
from typing import Callable, Optional, Tuple, TypeVar, Any
from telegram.error import BadRequest, TimedOut, NetworkError

from exceptions import TimeoutError as BotTimeoutError

logger = logging.getLogger(__name__)

T = TypeVar('T')


def async_retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: Tuple[type, ...] = (Exception,)
):
    """
    Retry async function with exponential backoff.
    
    Args:
        max_attempts: Maximum number of retry attempts
        delay: Initial delay between retries in seconds
        backoff: Multiplier for delay after each retry
        exceptions: Tuple of exceptions to catch and retry
        
    Returns:
        Decorator function
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            current_delay = delay
            last_exception = None
            
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        logger.warning(
                            f"Attempt {attempt + 1}/{max_attempts} failed for "
                            f"{func.__name__}: {type(e).__name__}: {str(e)[:100]}"
                        )
                        await asyncio.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        logger.error(
                            f"All {max_attempts} attempts failed for {func.__name__}: "
                            f"{type(e).__name__}: {str(e)[:100]}"
                        )
            
            raise last_exception
        return wrapper
    return decorator


async def with_timeout(
    coro,
    timeout: float,
    error_message: str = "Operation timed out"
) -> Any:
    """
    Execute coroutine with timeout.
    
    Args:
        coro: Coroutine to execute
        timeout: Timeout in seconds
        error_message: Error message if timeout occurs
        
    Returns:
        Result of coroutine
        
    Raises:
        BotTimeoutError: If operation times out
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(f"Timeout after {timeout}s: {error_message}")
        raise BotTimeoutError(error_message)


def get_file_size(filepath: str) -> int:
    """
    Get file size in bytes safely.
    
    Args:
        filepath: Path to file
        
    Returns:
        File size in bytes, 0 if error
    """
    try:
        if not filepath or not os.path.exists(filepath):
            return 0
        return os.path.getsize(filepath)
    except (OSError, PermissionError, FileNotFoundError) as e:
        logger.error(f"Error getting file size for {filepath}: {e}")
        return 0


def find_downloaded_file(
    temp_dir: str,
    extensions: Tuple[str, ...] = ('.mp4', '.webm', '.mkv', '.m4a', '.mp3')
) -> Optional[str]:
    """
    Safely find downloaded file in directory.
    
    Args:
        temp_dir: Directory to search
        extensions: Tuple of allowed file extensions
        
    Returns:
        Path to downloaded file or None
    """
    try:
        if not os.path.exists(temp_dir):
            logger.error(f"Directory not found: {temp_dir}")
            return None
        
        if not os.path.isdir(temp_dir):
            logger.error(f"Not a directory: {temp_dir}")
            return None
        
        files = os.listdir(temp_dir)
        logger.debug(f"Found {len(files)} files in {temp_dir}")
        
        for file in files:
            if file.endswith(extensions):
                filepath = os.path.join(temp_dir, file)
                
                # Verify it's a valid file with content
                if os.path.isfile(filepath):
                    size = get_file_size(filepath)
                    if size > 0:
                        logger.info(f"Found downloaded file: {file} ({size} bytes)")
                        return filepath
                    else:
                        logger.warning(f"File {file} is empty")
        
        logger.warning(f"No valid downloaded file found in {temp_dir}")
        return None
        
    except (OSError, PermissionError) as e:
        logger.error(f"Error accessing directory {temp_dir}: {e}")
        return None


def format_bytes(bytes_size: int) -> str:
    """
    Format bytes to human-readable string.
    
    Args:
        bytes_size: Size in bytes
        
    Returns:
        Formatted string (e.g., "1.5 MB")
    """
    if bytes_size < 0:
        return "0 B"
    
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    size = float(bytes_size)
    unit_index = 0
    
    while size >= 1024.0 and unit_index < len(units) - 1:
        size /= 1024.0
        unit_index += 1
    
    if unit_index == 0:
        return f"{int(size)} {units[unit_index]}"
    else:
        return f"{size:.1f} {units[unit_index]}"


def format_duration(seconds: int) -> str:
    """
    Format duration in seconds to human-readable string.
    
    Args:
        seconds: Duration in seconds
        
    Returns:
        Formatted string (e.g., "1:30:45")
    """
    if seconds <= 0:
        return "0:00"
    
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes}:{secs:02d}"


async def safe_edit_message(message, text: str, **kwargs) -> bool:
    """
    Safely edit Telegram message with error handling.
    
    Args:
        message: Telegram message object
        text: New text content
        **kwargs: Additional arguments for edit_text
        
    Returns:
        True if successful, False otherwise
    """
    try:
        await message.edit_text(text, **kwargs)
        return True
    except BadRequest as e:
        if "message is not modified" in str(e).lower():
            logger.debug("Message content unchanged, skipping edit")
        else:
            logger.warning(f"BadRequest while editing message: {e}")
        return False
    except TimedOut as e:
        logger.warning(f"Timeout while editing message: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error editing message: {e}")
        return False


async def safe_delete_message(message) -> bool:
    """
    Safely delete Telegram message with error handling.
    
    Args:
        message: Telegram message object
        
    Returns:
        True if successful, False otherwise
    """
    try:
        await message.delete()
        return True
    except BadRequest as e:
        logger.warning(f"BadRequest while deleting message: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error deleting message: {e}")
        return False


def validate_quality(quality: str) -> bool:
    """
    Validate quality parameter.
    
    Args:
        quality: Quality string to validate
        
    Returns:
        True if valid, False otherwise
    """
    from config import QUALITY_FORMATS
    return quality in QUALITY_FORMATS
