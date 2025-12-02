#!/usr/bin/env python3
"""
Custom exceptions for Video Downloader Bot
Provides specific error types for better error handling
"""


class BotException(Exception):
    """Base exception for all bot errors."""
    pass


class DownloadError(BotException):
    """Raised when video download fails."""
    pass


class ValidationError(BotException):
    """Raised when input validation fails."""
    pass


class RateLimitError(BotException):
    """Raised when rate limit is exceeded."""
    pass


class FileSizeError(BotException):
    """Raised when file size exceeds limits."""
    pass


class PlatformError(BotException):
    """Raised for platform-specific errors."""
    pass


class TimeoutError(BotException):
    """Raised when operation times out."""
    pass


class ResourceError(BotException):
    """Raised when resource limits are reached."""
    pass


class ConfigurationError(BotException):
    """Raised when configuration is invalid."""
    pass
