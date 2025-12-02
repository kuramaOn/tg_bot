#!/usr/bin/env python3
"""
Rate limiting for Video Downloader Bot
Token bucket implementation for per-user and global rate limiting
"""

import time
import logging
from collections import defaultdict
from typing import Dict, Tuple

logger = logging.getLogger(__name__)


class TokenBucket:
    """Token bucket rate limiter implementation."""
    
    def __init__(self, capacity: int, refill_rate: float):
        """
        Initialize token bucket.
        
        Args:
            capacity: Maximum number of tokens
            refill_rate: Tokens per second to add
        """
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = float(capacity)
        self.last_refill = time.time()
    
    def consume(self, tokens: int = 1) -> bool:
        """
        Try to consume tokens.
        
        Args:
            tokens: Number of tokens to consume
            
        Returns:
            True if tokens consumed successfully, False otherwise
        """
        self._refill()
        
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        
        return False
    
    def _refill(self):
        """Refill tokens based on elapsed time."""
        now = time.time()
        elapsed = now - self.last_refill
        tokens_to_add = elapsed * self.refill_rate
        
        self.tokens = min(self.capacity, self.tokens + tokens_to_add)
        self.last_refill = now
    
    def time_until_ready(self) -> float:
        """
        Calculate time in seconds until next token is available.
        
        Returns:
            Seconds until ready, 0.0 if ready now
        """
        self._refill()
        
        if self.tokens >= 1:
            return 0.0
        
        tokens_needed = 1 - self.tokens
        return tokens_needed / self.refill_rate
    
    def reset(self):
        """Reset the bucket to full capacity."""
        self.tokens = float(self.capacity)
        self.last_refill = time.time()


class RateLimiter:
    """Per-user rate limiter with global limits."""
    
    def __init__(
        self,
        user_capacity: int = 5,
        user_refill_rate: float = 0.1,
        global_capacity: int = 100,
        global_refill_rate: float = 1.0
    ):
        """
        Initialize rate limiter.
        
        Args:
            user_capacity: Max requests per user
            user_refill_rate: User tokens per second (0.1 = 1 per 10 sec)
            global_capacity: Max global requests
            global_refill_rate: Global tokens per second
        """
        self.user_capacity = user_capacity
        self.user_refill_rate = user_refill_rate
        self.user_buckets: Dict[int, TokenBucket] = defaultdict(
            lambda: TokenBucket(user_capacity, user_refill_rate)
        )
        self.global_bucket = TokenBucket(global_capacity, global_refill_rate)
        logger.info(
            f"RateLimiter initialized: user={user_capacity}/{user_refill_rate}, "
            f"global={global_capacity}/{global_refill_rate}"
        )
    
    def check_limit(self, user_id: int) -> Tuple[bool, float]:
        """
        Check if user can make a request.
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            Tuple of (allowed: bool, wait_time: float in seconds)
        """
        # Check global limit first
        if not self.global_bucket.consume():
            wait_time = self.global_bucket.time_until_ready()
            logger.warning(f"Global rate limit hit, wait: {wait_time:.1f}s")
            return False, wait_time
        
        # Check user-specific limit
        user_bucket = self.user_buckets[user_id]
        if not user_bucket.consume():
            wait_time = user_bucket.time_until_ready()
            logger.info(f"User {user_id} rate limited, wait: {wait_time:.1f}s")
            return False, wait_time
        
        logger.debug(f"User {user_id} request allowed")
        return True, 0.0
    
    def reset_user(self, user_id: int):
        """
        Reset rate limit for specific user.
        
        Args:
            user_id: Telegram user ID
        """
        if user_id in self.user_buckets:
            self.user_buckets[user_id].reset()
            logger.info(f"Reset rate limit for user {user_id}")
    
    def reset_global(self):
        """Reset global rate limit."""
        self.global_bucket.reset()
        logger.info("Reset global rate limit")
    
    def get_user_status(self, user_id: int) -> Dict[str, float]:
        """
        Get current rate limit status for user.
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            Dict with tokens and wait_time
        """
        user_bucket = self.user_buckets[user_id]
        user_bucket._refill()
        
        return {
            'tokens': user_bucket.tokens,
            'capacity': user_bucket.capacity,
            'wait_time': user_bucket.time_until_ready()
        }
    
    def cleanup_old_buckets(self, max_age: float = 3600):
        """
        Remove old inactive user buckets to save memory.
        
        Args:
            max_age: Remove buckets older than this many seconds
        """
        current_time = time.time()
        users_to_remove = []
        
        for user_id, bucket in self.user_buckets.items():
            if current_time - bucket.last_refill > max_age:
                users_to_remove.append(user_id)
        
        for user_id in users_to_remove:
            del self.user_buckets[user_id]
        
        if users_to_remove:
            logger.info(f"Cleaned up {len(users_to_remove)} old rate limit buckets")
