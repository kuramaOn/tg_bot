#!/usr/bin/env python3
"""
Input validation for Video Downloader Bot
Validates and sanitizes URLs and user inputs
"""

import re
from typing import Optional, Tuple
from urllib.parse import urlparse, parse_qs, urlencode
import logging

logger = logging.getLogger(__name__)


class URLValidator:
    """Comprehensive URL validation and sanitization."""
    
    PLATFORM_PATTERNS = {
        'youtube': [
            r'(?:https?://)?(?:www\.)?youtube\.com/watch\?v=[\w-]+',
            r'(?:https?://)?(?:www\.)?youtu\.be/[\w-]+',
            r'(?:https?://)?(?:www\.)?youtube\.com/shorts/[\w-]+'
        ],
        'tiktok': [
            r'(?:https?://)?(?:www\.|vm\.|vt\.|m\.|lite\.)?tiktok\.com/[\w@/-]+',
        ],
        'instagram': [
            r'(?:https?://)?(?:www\.)?instagram\.com/(?:p|reel|stories)/[\w-]+',
        ]
    }
    
    @classmethod
    def validate(cls, url: str) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Validate URL and return (is_valid, platform, error_message).
        
        Args:
            url: URL string to validate
            
        Returns:
            Tuple of (is_valid: bool, platform: str|None, error_message: str|None)
        """
        if not url or not isinstance(url, str):
            return False, None, "URL must be a non-empty string"
        
        url = url.strip()
        
        # Check URL length
        if len(url) > 2048:
            return False, None, "URL too long (max 2048 characters)"
        
        # Check URL format
        try:
            parsed = urlparse(url)
            if not all([parsed.scheme, parsed.netloc]):
                return False, None, "Invalid URL format - missing scheme or domain"
            
            # Only allow http/https
            if parsed.scheme not in ('http', 'https'):
                return False, None, "Only HTTP/HTTPS URLs are supported"
                
        except Exception as e:
            logger.warning(f"URL parsing error: {e}")
            return False, None, f"URL parsing error: {str(e)}"
        
        # Detect platform
        for platform, patterns in cls.PLATFORM_PATTERNS.items():
            for pattern in patterns:
                if re.match(pattern, url, re.IGNORECASE):
                    return True, platform, None
        
        supported = ', '.join(cls.PLATFORM_PATTERNS.keys())
        return False, None, f"Unsupported platform. Supported: {supported}"
    
    @classmethod
    def sanitize(cls, url: str) -> str:
        """
        Remove tracking parameters and sanitize URL.
        
        Args:
            url: URL to sanitize
            
        Returns:
            Sanitized URL string
        """
        try:
            parsed = urlparse(url)
            clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            
            if parsed.query:
                # Keep only essential parameters
                essential_params = ['v', 'list', 't']  # For YouTube
                query_dict = parse_qs(parsed.query)
                filtered = {k: v for k, v in query_dict.items() if k in essential_params}
                
                if filtered:
                    clean_url += '?' + urlencode(filtered, doseq=True)
            
            return clean_url
        except Exception as e:
            logger.warning(f"Error sanitizing URL: {e}")
            return url
    
    @classmethod
    def extract_video_id(cls, url: str, platform: str) -> Optional[str]:
        """
        Extract video ID from URL.
        
        Args:
            url: Video URL
            platform: Platform name (youtube, tiktok, instagram)
            
        Returns:
            Video ID string or None
        """
        patterns = {
            'youtube': [
                r'(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})',
                r'shorts/([a-zA-Z0-9_-]{11})'
            ],
            'tiktok': [
                r'video/(\d+)',
                r'@[\w.]+/video/(\d+)'
            ],
            'instagram': [
                r'(?:p|reel)/([a-zA-Z0-9_-]+)'
            ]
        }
        
        if platform not in patterns:
            return None
        
        for pattern in patterns[platform]:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        
        return None


class InputSanitizer:
    """Sanitize user inputs for security."""
    
    @staticmethod
    def sanitize_filename(filename: str, max_length: int = 200) -> str:
        """
        Sanitize filename to prevent path traversal and invalid characters.
        
        Args:
            filename: Original filename
            max_length: Maximum length allowed
            
        Returns:
            Sanitized filename
        """
        if not filename:
            return 'download'
        
        # Remove path separators
        filename = filename.replace('/', '_').replace('\\', '_')
        
        # Remove null bytes and control characters
        filename = ''.join(char for char in filename if ord(char) >= 32)
        
        # Remove leading/trailing dots and spaces
        filename = filename.strip('. ')
        
        # Replace multiple spaces/underscores with single
        filename = re.sub(r'[\s_]+', '_', filename)
        
        # Remove invalid Windows filename characters
        invalid_chars = '<>:"|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '_')
        
        # Limit length while preserving extension
        if len(filename) > max_length:
            name, ext = filename.rsplit('.', 1) if '.' in filename else (filename, '')
            max_name_length = max_length - len(ext) - 1
            filename = name[:max_name_length] + ('.' + ext if ext else '')
        
        # Fallback for empty names
        if not filename or filename == '.':
            filename = 'download'
        
        return filename
    
    @staticmethod
    def sanitize_text(text: str, max_length: int = 4096) -> str:
        """
        Sanitize text for Telegram messages.
        
        Args:
            text: Text to sanitize
            max_length: Maximum length (Telegram limit is 4096)
            
        Returns:
            Sanitized text
        """
        if not text:
            return ""
        
        # Remove null bytes
        text = text.replace('\x00', '')
        
        # Remove other control characters except newlines and tabs
        text = ''.join(char for char in text if ord(char) >= 32 or char in '\n\t')
        
        # Limit length
        if len(text) > max_length:
            text = text[:max_length - 3] + "..."
        
        return text
    
    @staticmethod
    def validate_user_id(user_id: any) -> bool:
        """
        Validate Telegram user ID.
        
        Args:
            user_id: User ID to validate
            
        Returns:
            True if valid, False otherwise
        """
        try:
            uid = int(user_id)
            # Telegram user IDs are positive integers
            return uid > 0
        except (ValueError, TypeError):
            return False
