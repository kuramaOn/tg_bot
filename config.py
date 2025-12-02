#!/usr/bin/env python3
"""
Configuration management for Video Downloader Bot
Handles environment variables and settings
"""

import os
import sys
import logging
from typing import List, Optional
from dataclasses import dataclass

from exceptions import ConfigurationError

logger = logging.getLogger(__name__)


@dataclass
class BotConfig:
    """Bot configuration from environment variables."""
    
    token: str
    max_file_size: int = 209715200  # 200MB
    download_timeout: int = 30
    max_retries: int = 3
    retry_delay: int = 5
    rate_limit_requests: int = 5
    rate_limit_period: int = 10
    max_concurrent_downloads: int = 10
    max_downloads_per_user: int = 2
    admin_ids: List[int] = None
    log_level: str = "INFO"
    
    def __post_init__(self):
        """Validate configuration after initialization."""
        if self.admin_ids is None:
            self.admin_ids = []
        
        # Validate values
        if self.max_file_size <= 0:
            raise ConfigurationError("MAX_FILE_SIZE must be positive")
        
        if self.download_timeout <= 0:
            raise ConfigurationError("DOWNLOAD_TIMEOUT must be positive")
        
        if self.max_retries < 0:
            raise ConfigurationError("MAX_RETRIES must be non-negative")
        
        # Telegram bot file limit is 50MB
        self.telegram_file_limit = 50 * 1024 * 1024
    
    @classmethod
    def from_env(cls) -> 'BotConfig':
        """
        Load configuration from environment variables.
        
        Returns:
            BotConfig instance
            
        Raises:
            ConfigurationError: If required config is missing or invalid
        """
        # Required configuration
        token = os.getenv("BOT_TOKEN")
        if not token:
            logger.critical("BOT_TOKEN environment variable is not set!")
            raise ConfigurationError(
                "BOT_TOKEN is required. Set it as an environment variable."
            )
        
        # Optional configuration with defaults
        try:
            config = cls(
                token=token,
                max_file_size=cls._get_env_int("MAX_FILE_SIZE", 209715200),
                download_timeout=cls._get_env_int("DOWNLOAD_TIMEOUT", 30),
                max_retries=cls._get_env_int("MAX_RETRIES", 3),
                retry_delay=cls._get_env_int("RETRY_DELAY", 5),
                rate_limit_requests=cls._get_env_int("RATE_LIMIT_REQUESTS", 5),
                rate_limit_period=cls._get_env_int("RATE_LIMIT_PERIOD", 10),
                max_concurrent_downloads=cls._get_env_int("MAX_CONCURRENT_DOWNLOADS", 10),
                max_downloads_per_user=cls._get_env_int("MAX_DOWNLOADS_PER_USER", 2),
                admin_ids=cls._get_env_int_list("ADMIN_IDS"),
                log_level=os.getenv("LOG_LEVEL", "INFO").upper()
            )
            
            logger.info("Configuration loaded successfully")
            return config
            
        except ConfigurationError:
            raise
        except Exception as e:
            logger.critical(f"Failed to load configuration: {e}")
            raise ConfigurationError(f"Configuration error: {e}")
    
    @staticmethod
    def _get_env_int(key: str, default: int) -> int:
        """
        Safely parse integer from environment variable.
        
        Args:
            key: Environment variable name
            default: Default value if not set or invalid
            
        Returns:
            Integer value
        """
        value_str = os.getenv(key)
        if value_str is None:
            return default
        
        try:
            value = int(value_str)
            logger.debug(f"Loaded {key}={value}")
            return value
        except (ValueError, TypeError) as e:
            logger.warning(
                f"Invalid {key}='{value_str}', using default {default}: {e}"
            )
            return default
    
    @staticmethod
    def _get_env_int_list(key: str) -> List[int]:
        """
        Parse comma-separated list of integers from environment variable.
        
        Args:
            key: Environment variable name
            
        Returns:
            List of integers
        """
        value_str = os.getenv(key, "")
        if not value_str:
            return []
        
        result = []
        for item in value_str.split(","):
            item = item.strip()
            if not item:
                continue
            try:
                result.append(int(item))
            except (ValueError, TypeError) as e:
                logger.warning(f"Invalid integer in {key}: '{item}': {e}")
        
        if result:
            logger.debug(f"Loaded {key}={result}")
        
        return result
    
    def get_log_level(self) -> int:
        """
        Get logging level as integer.
        
        Returns:
            Logging level constant
        """
        levels = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL
        }
        return levels.get(self.log_level, logging.INFO)


# Quality format mappings for YouTube
QUALITY_FORMATS = {
    '360p': 'best[height<=360][ext=mp4]/best[height<=360]/best[ext=mp4]/best',
    '480p': 'best[height<=480][ext=mp4]/best[height<=480]/best[ext=mp4]/best',
    'audio': 'bestaudio[ext=m4a]/bestaudio',
}

# Supported platforms
SUPPORTED_PLATFORMS = [
    'youtube', 'tiktok', 'instagram'
]
