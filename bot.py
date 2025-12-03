#!/usr/bin/env python3
"""
🎬 Video Downloader Bot - Enhanced Version with Error Handling
Complete YouTube quality callback handler with security improvements
"""

import os
import re
import asyncio
import tempfile
import subprocess
import signal
import sys
import psutil
import logging
from logging.handlers import RotatingFileHandler
from typing import Optional
from functools import partial
import threading
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.error import Conflict, NetworkError, TimedOut, BadRequest
import yt_dlp

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed, use system env vars

# Import custom modules
from config import BotConfig, QUALITY_FORMATS
from exceptions import (
    DownloadError, ValidationError, RateLimitError, 
    FileSizeError, PlatformError, ConfigurationError
)
from validators import URLValidator, InputSanitizer
from rate_limiter import RateLimiter
from resource_manager import get_resource_manager, init_resource_manager
from utils import (
    async_retry, with_timeout, get_file_size, find_downloaded_file,
    format_bytes, format_duration, safe_edit_message, safe_delete_message,
    validate_quality
)

# Load configuration
try:
    config = BotConfig.from_env()
except ConfigurationError as e:
    print(f"CRITICAL: {e}")
    print("Please set BOT_TOKEN environment variable and try again.")
    sys.exit(1)

# Configure logging with rotating file handler to prevent huge log files
logger = logging.getLogger(__name__)
logger.setLevel(config.get_log_level())

# Create formatters
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Create rotating file handler (max 5MB per file, keep 3 backup files)
file_handler = RotatingFileHandler(
    'bot.log',
    maxBytes=5 * 1024 * 1024,  # 5MB
    backupCount=3,
    encoding='utf-8'
)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# Create console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# Initialize rate limiter and resource manager
rate_limiter = RateLimiter(
    user_capacity=config.rate_limit_requests,
    user_refill_rate=1.0 / config.rate_limit_period
)
init_resource_manager(
    max_concurrent=config.max_concurrent_downloads,
    max_per_user=config.max_downloads_per_user
)

# In-memory storage for user preferences
user_notifications = {}  # {user_id: True/False}
scheduled_downloads = []  # [{user_id, chat_id, url, time, quality}]

LOCK_FILE = 'bot_instance.lock'

logger.info("Bot initialization completed successfully")
logger.info(f"Max file size: {format_bytes(config.max_file_size)}")
logger.info(f"Download timeout: {config.download_timeout}s")
logger.info(f"Rate limit: {config.rate_limit_requests} requests per {config.rate_limit_period}s")

def is_valid_url(url: str) -> bool:
    """Check if URL is valid."""
    if not url or not isinstance(url, str):
        return False
    is_valid, _, _ = URLValidator.validate(url)
    return is_valid

def is_youtube_url(url: str) -> bool:
    """Check if URL is from YouTube."""
    is_valid, platform, _ = URLValidator.validate(url)
    return is_valid and platform == 'youtube'

def is_tiktok_url(url: str) -> bool:
    """Check if URL is from TikTok."""
    is_valid, platform, _ = URLValidator.validate(url)
    return is_valid and platform == 'tiktok'

def is_instagram_url(url: str) -> bool:
    """Check if URL is from Instagram."""
    is_valid, platform, _ = URLValidator.validate(url)
    return is_valid and platform == 'instagram'

class DownloadProgress:
    """Track download progress for status updates."""
    def __init__(self):
        self.last_update = 0
        self.downloaded = 0
        self.total = 0
        self.speed = 0
        self.eta = 0
        self.percent = 0.0
        self.status = 'starting'

    def progress_hook(self, d):
        """Update progress from yt-dlp download hook."""
        try:
            if d['status'] == 'downloading':
                self.status = 'downloading'
                self.downloaded = d.get('downloaded_bytes', 0)
                self.total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                self.speed = d.get('speed', 0) or 0
                self.eta = d.get('eta', 0) or 0
                
                # Safe division with bounds checking
                if self.total > 0:
                    self.percent = min(100.0, max(0.0, (self.downloaded / self.total) * 100))
                else:
                    self.percent = 0.0
                    
            elif d['status'] == 'finished':
                self.status = 'finished'
                self.percent = 100.0
        except Exception as e:
            logger.warning(f"Error in progress_hook: {e}")

def format_speed(speed_bytes):
    """Format download speed in human readable format."""
    if not speed_bytes or speed_bytes <= 0:
        return "-- KB/s"
    if speed_bytes >= 1024 * 1024:
        return f"{speed_bytes / (1024 * 1024):.1f} MB/s"
    else:
        return f"{speed_bytes / 1024:.0f} KB/s"

def format_eta(seconds):
    """Format ETA in human readable format."""
    if not seconds or seconds <= 0:
        return "--"
    if seconds >= 3600:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    elif seconds >= 60:
        return f"{seconds // 60}m {seconds % 60}s"
    else:
        return f"{seconds}s"

def create_progress_bar(percent, length=10):
    """Create a visual progress bar with emoji."""
    filled = int(length * percent / 100)
    bar = '🟩' * filled + '⬜' * (length - filled)
    return bar

async def download_tiktok_instagram(update: Update, url: str):
    """Download TikTok or Instagram video."""
    user_id = update.effective_user.id
    username = update.effective_user.username or "Unknown"

    platform = "TikTok" if is_tiktok_url(url) else "Instagram"
    platform_emoji = "🎵" if is_tiktok_url(url) else "📸"

    # Send animated loading message
    status_message = await update.message.reply_text(
        f"{platform_emoji} **{platform} Detected**\n\n"
        f"▪️▪️▪️▪️▪️ 0%\n\n"
        f"⏳ Connecting...",
        parse_mode='Markdown'
    )

    # Animate loading steps
    loading_steps = [
        ("🟦▪️▪️▪️▪️ 20%\n\n🔗 Validating URL...", 0.2),
        ("🟦🟦▪️▪️▪️ 40%\n\n📡 Connecting to server...", 0.2),
        ("🟦🟦🟦▪️▪️ 60%\n\n📊 Fetching video...", 0.2),
    ]

    for step_text, delay in loading_steps:
        await safe_edit_message(
            status_message,
            f"{platform_emoji} **{platform} Detected**\n\n{step_text}",
            parse_mode='Markdown'
        )
        await asyncio.sleep(delay)

    try:
        # Create temp directory for download
        with tempfile.TemporaryDirectory() as temp_dir:
            # Use sanitized filename to avoid special character issues on Windows
            output_template = os.path.join(temp_dir, 'video.%(ext)s')

            # Progress tracker
            progress = DownloadProgress()

            ydl_opts = {
                'outtmpl': output_template,
                'format': 'best[ext=mp4]/best',
                'quiet': True,
                'no_warnings': True,
                'socket_timeout': config.download_timeout,
                'retries': config.max_retries,
                'progress_hooks': [progress.progress_hook],
                'merge_output_format': 'mp4',
                # Preserve original video and audio streams without re-encoding
                'postprocessor_args': ['-c:v', 'copy', '-c:a', 'copy'],
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.5',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'DNT': '1',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                    'Sec-Fetch-Dest': 'document',
                    'Sec-Fetch-Mode': 'navigate',
                    'Sec-Fetch-Site': 'none',
                    'Sec-Fetch-User': '?1',
                },
                'extractor_args': {
                    'instagram': {
                        'api_type': 'graphql'
                    }
                },
            }

            # Update status
            await status_message.edit_text(
                f"⬇️ {platform} ဗီဒီယို ဒေါင်းလုဒ်လုပ်နေပါသည်...\n"
                f"📥 ဖိုင်ကို ရယူနေပါသည်..."
            )

            # Download video with progress updates
            async def update_progress():
                last_percent = -1
                update_count = 0
                while progress.status != 'finished':
                    # Update every 10% or every 2 seconds
                    if progress.percent > last_percent + 10 or (update_count % 2 == 0 and progress.percent > 0):
                        last_percent = progress.percent
                        progress_bar = create_progress_bar(progress.percent)
                        speed_str = format_speed(progress.speed)
                        eta_str = format_eta(progress.eta)

                        await safe_edit_message(
                            status_message,
                            f"⬇️ {platform} ဗီဒီယို ဒေါင်းလုဒ်လုပ်နေပါသည်...\n\n"
                            f"{progress_bar} {progress.percent:.0f}%\n"
                            f"📥 အမြန်နှုန်း: {speed_str}\n"
                            f"⏳ ကျန်အချိန်: {eta_str}"
                        )
                    update_count += 1
                    await asyncio.sleep(0.5)

            # Start progress update task
            progress_task = asyncio.create_task(update_progress())

            # Download video in separate thread to allow progress updates
            def do_download():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(url, download=True)

            info = await asyncio.to_thread(do_download)

            # Cancel progress task
            progress_task.cancel()
            try:
                await progress_task
            except asyncio.CancelledError:
                pass

            if not info:
                await status_message.edit_text(
                    f"❌ {platform} ဗီဒီယို အချက်အလက် ရယူ၍ မရပါ။\n\n"
                    "ဖြစ်နိုင်သော အကြောင်းရင်းများ:\n"
                    "• ဗီဒီယို ပုဂ္ဂလိက ဖြစ်နေသည်\n"
                    "• လင့်ခ် မမှန်ကန်ပါ\n"
                    "• ပလက်ဖောင်း ကန့်သတ်ချက်များ"
                )
                return

            title = InputSanitizer.sanitize_text(info.get('title', 'video'), max_length=50)

            # Find downloaded file safely
            downloaded_file = find_downloaded_file(temp_dir)

            if not downloaded_file:
                await status_message.edit_text(
                    f"❌ ဒေါင်းလုဒ် ဖိုင် မတွေ့ပါ။\n"
                    "ထပ်ကြိုးစားပါ။"
                )
                return

            # Check file size
            file_size = get_file_size(downloaded_file)
            file_size_mb = file_size / (1024 * 1024)

            logger.info(f"Downloaded {platform} video: {title}, size: {file_size_mb:.2f}MB")

            if file_size > config.max_file_size:
                await status_message.edit_text(
                    f"❌ ဖိုင်အရွယ်အစား ({file_size_mb:.1f}MB) သည် ကန့်သတ်ချက် "
                    f"({config.max_file_size / (1024*1024):.0f}MB) ထက် ကြီးပါသည်။"
                )
                return

            if file_size > config.telegram_file_limit:
                await status_message.edit_text(
                    f"❌ ဖိုင်အရွယ်အစား ({file_size_mb:.1f}MB) သည် Telegram ကန့်သတ်ချက် (50MB) ထက် ကြီးပါသည်။\n\n"
                    "Telegram Bot များသည် 50MB ထက်ကြီးသော ဖိုင်များကို ပို့၍ မရပါ။"
                )
                return

            # Update status before sending
            await status_message.edit_text(
                f"📤 {platform} ဗီဒီယို ပို့နေပါသည်...\n"
                f"📁 အရွယ်အစား: {file_size_mb:.1f}MB\n"
                f"⏳ ခေတ္တစောင့်ပါ..."
            )

            # Send video to user with retry logic for large files
            max_retries = 2
            for attempt in range(max_retries):
                try:
                    with open(downloaded_file, 'rb') as video_file:
                        await update.message.reply_video(
                            video=video_file,
                            caption=f"🎬 {title}\n\n📱 {platform} မှ ဒေါင်းလုဒ်\n📁 {file_size_mb:.1f}MB",
                            supports_streaming=True,
                            read_timeout=90,
                            write_timeout=180,
                            connect_timeout=60,
                            pool_timeout=15
                        )
                    break  # Success, exit retry loop
                except TimedOut as e:
                    if attempt < max_retries - 1:
                        logger.warning(f"Upload timed out (attempt {attempt + 1}/{max_retries}), retrying...")
                        await asyncio.sleep(2)  # Wait before retry
                    else:
                        raise  # Final attempt failed, raise the error

            # Delete status message
            await safe_delete_message(status_message)

            logger.info(f"Successfully sent {platform} video to user {user_id} ({username})")

    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e)
        logger.error(f"{platform} download error: {error_msg}")

        if 'private' in error_msg.lower() or 'unavailable' in error_msg.lower():
            await status_message.edit_text(
                f"❌ {platform} ဗီဒီယို ရယူ၍ မရပါ။\n\n"
                "ဖြစ်နိုင်သော အကြောင်းရင်းများ:\n"
                "• ဗီဒီယို ပုဂ္ဂလိက ဖြစ်နေသည်\n"
                "• အကောင့် လိုအပ်သည်\n"
                "• နိုင်ငံ ကန့်သတ်ချက် ရှိသည်"
            )
        else:
            await status_message.edit_text(
                f"❌ {platform} ဒေါင်းလုဒ် မအောင်မြင်ပါ။\n\n"
                f"အမှား: {error_msg[:100]}\n\n"
                "ထပ်ကြိုးစားပါ သို့မဟုတ် အခြားလင့်ခ် သုံးပါ။"
            )

    except Exception as e:
        logger.error(f"Error downloading {platform} video: {e}", exc_info=True)
        await status_message.edit_text(
            f"❌ {platform} ဒေါင်းလုဒ် ပြုလုပ်ရာတွင် အမှားရှိပါသည်။\n\n"
            f"အမှားအမျိုးအစား: {type(e).__name__}\n\n"
            "ထပ်ကြိုးစားပါ။"
        )

def extract_url(text: str) -> Optional[str]:
    """Extract URL from message text."""
    if not text or not isinstance(text, str):
        return None
    url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
    match = re.search(url_pattern, text)
    return match.group(0) if match else None

def estimate_video_size(info_dict: dict, quality: str = None) -> dict:
    """Estimate video file size before download with enhanced validation."""
    size_info = {
        'estimated_size': None,
        'size_mb': None,
        'over_limit': False,
        'over_telegram_limit': False,
        'warning': None
    }
    
    try:
        if not info_dict or not isinstance(info_dict, dict):
            return size_info
            
        if 'formats' not in info_dict:
            return size_info
        
        # Try to find size information from formats
        formats = info_dict.get('formats', [])
        duration = info_dict.get('duration', 0)
        
        if not formats:
            return size_info
        
        # Look for filesize in selected format
        for fmt in formats:
            if not isinstance(fmt, dict):
                continue
                
            if quality:
                # Check if this format matches our quality preference
                height = fmt.get('height', 0)
                vcodec = fmt.get('vcodec', '')
                acodec = fmt.get('acodec', '')
                
                if quality == '360p':
                    # For 360p, prefer video formats with height <= 360
                    if not height or height > 360:
                        continue
                elif quality == '480p':
                    # For 480p, prefer video formats with height <= 480
                    if not height or height > 480:
                        continue
                elif quality == 'audio':
                    # For audio, look for audio-only formats or formats without video
                    if vcodec != 'none' and height:
                        continue  # Skip formats with video
                else:
                    continue
            
            # Get file size from format
            file_size = fmt.get('filesize') or fmt.get('filesize_approx')
            if file_size and file_size > 0:
                size_info['estimated_size'] = file_size
                size_info['size_mb'] = file_size / (1024 * 1024)
                size_info['over_limit'] = file_size > config.max_file_size
                size_info['over_telegram_limit'] = file_size > config.telegram_file_limit
                break
        
        # If no size found, estimate based on duration and quality
        if not size_info['estimated_size'] and duration and duration > 0:
            # Conservative estimates (higher bitrates for safety)
            bitrate_estimates = {
                '360p': 1.0,   # MB per minute (increased for safety)
                '480p': 1.5,   # MB per minute for 480p
                'audio': 1.2   # MB per minute for audio
            }
            
            minutes = duration / 60
            estimated_mb = minutes * bitrate_estimates.get(quality, 1.5)
            
            size_info['estimated_size'] = int(estimated_mb * 1024 * 1024)
            size_info['size_mb'] = estimated_mb
            size_info['over_limit'] = estimated_mb > (config.max_file_size / (1024 * 1024))
            size_info['over_telegram_limit'] = estimated_mb > (config.telegram_file_limit / (1024 * 1024))
            size_info['warning'] = "ခန့်မှန်းချက်သာ"
            
            # Additional warning for long videos
            if duration > 1800:  # 30 minutes
                size_info['warning'] = "ရှည်လျားသောဗီဒီယို - အသံသီးသန့် ရွေးချယ်ပါ"
        
        return size_info
        
    except Exception as e:
        logger.error(f"Error in size estimation: {e}")
        return size_info

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command with enhanced information."""
    try:
        user_id = update.effective_user.id
        username = update.effective_user.username or "Unknown"
        logger.info(f"User {user_id} ({username}) started the bot")
        
        await update.message.reply_text(
            "🎬 မင်္ဂလာပါ! Video Downloader Bot မှ ကြိုဆိုပါတယ်!\n\n"
            "📱 **ပံ့ပိုးပေးသော ပလပ်ဖောင်းများ:**\n"
            "• YouTube (360p video, MP3 audio)\n"
            "• TikTok (original quality)\n"
            "• Instagram (posts & reels)\n\n"
            "📏 **ကန့်သတ်ချက်များ:**\n"
            "• ဒေါင်းလုဒ် ကန့်သတ်ချက်: 200MB\n"
            "• Telegram ပို့ခြင်း ကန့်သတ်ချက်: 50MB\n"
            "• ကြာချိန်: ၃၀ စက္ကန့်\n\n"
            "🎵 **အင်္ဂါရပ်များ:**\n"
            "• အရည်အသွေး ရွေးချယ်မှု\n"
            "• ဖိုင်အရွယ်အစား ကြိုတင်ခန့်မှန်းမှု\n"
            "• လုံခြုံသော ဒေါင်းလုဒ် လုပ်ငန်းစဉ်\n\n"
            "🚀 **စတင်ရန်:** ဗီဒီယို လင့်ခ် ပို့ပေးပါ!\n\n"
            "ℹ️ **/help** - အသုံးပြုပုံ လမ်းညွှန်",
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error in start_command: {e}")
        await update.message.reply_text(
            "🎬 မင်္ဂလာပါ! Video Downloader Bot မှ ကြိုဆိုပါတယ်!\n\n"
            "🚀 စတင်ရန် ဗီဒီယို လင့်ခ် ပို့ပေးပါ!"
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    try:
        help_text = (
            "📖 **Video Downloader Bot - အသုံးပြုပုံ လမ်းညွှန်**\n\n"
            "🎯 **ဘယ်လို သုံးရမလဲ:**\n"
            "1️⃣ ဗီဒီယို လင့်ခ်ကို ပို့ပါ\n"
            "2️⃣ အရည်အသွေး ရွေးပါ (YouTube အတွက်)\n"
            "3️⃣ ဒေါင်းလုဒ် ပြီးမြောက်ရန် စောင့်ပါ\n\n"
            "🔗 **ပံ့ပိုးပေးသော URL ပုံစံများ:**\n"
            "• `youtube.com/watch?v=...`\n"
            "• `youtu.be/...`\n"
            "• `tiktok.com/@.../video/...`\n"
            "• `vm.tiktok.com/...`\n"
            "• `instagram.com/p/...`\n"
            "• `instagram.com/reel/...`\n\n"
            "⚙️ **အရည်အသွေး ရွေးချယ်မှုများ:**\n"
            "• 📱 Video (360p) - မိုဘိုင်းအတွက်\n"
            "• 🎵 Music Only (MP3) - အသံသီးသန့်\n\n"
            "⚠️ **သတိပေးချက်များ:**\n"
            "• ရှည်လျားသောဗီဒီယိုများအတွက် MP3 ရွေးပါ\n"
            "• တချို့ဗီဒီယို ပုဂ္ဂလိက သို့မဟုတ် ကန့်သတ်ထားနိုင်သည်\n"
            "• ဖိုင်ကြီးများ အချိန်ပိုကြာနိုင်သည်\n\n"
            "🆘 **ပြဿနာရှိပါက:** /start နှိပ်၍ ပြန်စတင်ပါ"
        )
        
        await update.message.reply_text(help_text, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Error in help_command: {e}")
        await update.message.reply_text(
            "📖 အသုံးပြုပုံ: ဗီဒီယို လင့်ခ် ပို့ပေးပါ။"
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming messages with video URLs and enhanced validation."""
    try:
        user_id = update.effective_user.id
        username = update.effective_user.username or "Unknown"
        message_text = update.message.text
        
        logger.info(f"User {user_id} ({username}) sent message: {message_text[:100]}")
        
        # Rate limiting with token bucket
        allowed, wait_time = rate_limiter.check_limit(user_id)
        if not allowed:
            await update.message.reply_text(
                f"⏳ ခေတ္တစောင့်ပါ။ နောက် {int(wait_time)} စက္ကန့်အကြာမှ ထပ်ပို့နိုင်ပါသည်။"
            )
            return
        
        # Validate message
        if not message_text or len(message_text.strip()) == 0:
            await update.message.reply_text("❌ ဗီဒီယို လင့်ခ် ပို့ပေးပါ။")
            return
            
        # Extract and validate URL
        url = extract_url(message_text)
        
        if not url:
            await update.message.reply_text(
                "❌ စာတွင် လင့်ခ် မတွေ့ပါ။\n\n"
                "✅ ပံ့ပိုးသည့် ပလက်ဖောင်းများ:\n"
                "🎵 TikTok (tiktok.com, vm.tiktok.com)\n"
                "📸 Instagram (posts & reels)\n" 
                "📺 YouTube (videos & shorts)\n\n"
                "💡 လင့်ခ် ပါသော စာကို ပို့ပေးပါ!"
            )
            return
        
        # Validate URL using URLValidator
        is_valid, platform, error_msg = URLValidator.validate(url)
        if not is_valid:
            logger.info(f"Invalid URL attempted by user {user_id}: {url}")
            await update.message.reply_text(
                f"❌ {error_msg}\n\n"
                "✅ ပံ့ပိုးသည့် ပလက်ဖောင်းများ:\n"
                "🎵 TikTok (tiktok.com, vm.tiktok.com)\n"
                "📸 Instagram (posts & reels)\n"
                "📺 YouTube (videos & shorts)\n\n"
                "📝 ဥပမာ:\n"
                "• https://youtube.com/watch?v=...\n"
                "• https://tiktok.com/@user/video/...\n"
                "• https://instagram.com/p/...\n\n"
                "💡 လင့်ခ် မှန်ကန်ပါက ထပ်ကြိုးစားပါ!"
            )
            return
        
        # Sanitize URL
        url = URLValidator.sanitize(url)
        logger.info(f"Processing valid {platform} URL: {url}")

        # Check if YouTube URL - show quality options with rich metadata
        if is_youtube_url(url):
            # Get video info for enhanced display with pixel art loading
            info_message = await update.message.reply_text(
                "📺 **YouTube Detected**\n\n"
                "▪️▪️▪️▪️▪️ 0%\n\n"
                "⏳ Connecting...",
                parse_mode='Markdown'
            )

            # Animate loading steps
            loading_steps = [
                ("🟦▪️▪️▪️▪️ 20%\n\n🔗 Validating URL...", 0.3),
                ("🟦🟦▪️▪️▪️ 40%\n\n📡 Fetching metadata...", 0.3),
                ("🟦🟦🟦▪️▪️ 60%\n\n📊 Analyzing video...", 0.3),
                ("🟦🟦🟦🟦▪️ 80%\n\n📏 Calculating sizes...", 0.3),
            ]

            for step_text, delay in loading_steps:
                await safe_edit_message(
                    info_message,
                    f"📺 **YouTube Detected**\n\n{step_text}",
                    parse_mode='Markdown'
                )
                await asyncio.sleep(delay)
            
            try:
                ydl_opts = {
                    'quiet': True,
                    'no_warnings': True,
                    'socket_timeout': 15,
                    'extract_flat': False,
                }
                
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                
                if info:
                    # Extract comprehensive video metadata
                    title = info.get('title', 'Unknown Video')
                    uploader = info.get('uploader', 'Unknown Channel')
                    duration = info.get('duration', 0)
                    view_count = info.get('view_count', 0)
                    like_count = info.get('like_count', 0)
                    upload_date = info.get('upload_date', '')
                    description = info.get('description', '')
                    thumbnail_url = info.get('thumbnail', '')
                    
                    # Format duration
                    if duration:
                        hours = duration // 3600
                        minutes = (duration % 3600) // 60
                        seconds = duration % 60
                        if hours > 0:
                            duration_str = f"{hours}:{minutes:02d}:{seconds:02d}"
                        else:
                            duration_str = f"{minutes}:{seconds:02d}"
                    else:
                        duration_str = "Unknown"
                    
                    # Format view count
                    if view_count >= 1000000:
                        view_str = f"{view_count/1000000:.1f}M"
                    elif view_count >= 1000:
                        view_str = f"{view_count/1000:.1f}K"
                    else:
                        view_str = str(view_count) if view_count > 0 else "Unknown"
                    
                    # Format like count
                    if like_count >= 1000000:
                        like_str = f"{like_count/1000000:.1f}M"
                    elif like_count >= 1000:
                        like_str = f"{like_count/1000:.1f}K"
                    else:
                        like_str = str(like_count) if like_count > 0 else "Unknown"
                    
                    # Format upload date
                    if upload_date and len(upload_date) >= 8:
                        try:
                            year = upload_date[:4]
                            month = upload_date[4:6]
                            day = upload_date[6:8]
                            upload_str = f"{day}/{month}/{year}"
                        except:
                            upload_str = "Unknown"
                    else:
                        upload_str = "Unknown"
                    
                    # Determine content type for smart recommendations
                    content_type = "video"
                    recommendation = ""
                    
                    if duration and duration > 1800:  # 30+ minutes
                        content_type = "long_video"
                        recommendation = "💡 ရှည်လျားသော ဗီဒီယို → 🎵 အသံသာ အကြံပြုပါသည်"
                    elif any(word in title.lower() for word in ['music', 'song', 'audio', 'playlist']):
                        content_type = "music"
                        recommendation = "🎵 ဂီတဗီဒီယို → အသံ နှုတ်ယူမှု အကောင်းဆုံး"
                    elif any(word in title.lower() for word in ['tutorial', 'how to', 'lesson', 'guide']):
                        content_type = "tutorial"
                        recommendation = "📚 သင်ခန်းစာဗီဒီယို → ဗီဒီယို ကြည့်ရှုရန် အကြံပြုပါသည်"
                    
                    # Estimate sizes for available qualities
                    size_360p = estimate_video_size(info, '360p')
                    size_audio = estimate_video_size(info, 'audio')
                    
                    # Create enhanced buttons with detailed info
                    buttons = []
                    
                    # 360p button - clean and simple
                    if size_360p['size_mb']:
                        # Keep time estimates for info display
                        if size_360p['size_mb'] < 20:
                            time_est = "~1-2 မိနစ်"
                        elif size_360p['size_mb'] < 50:
                            time_est = "~2-4 မိနစ်"
                        else:
                            time_est = "~5-8 မိနစ်"
                    else:
                        time_est = "မသိ"
                    
                    video_button_text = "📱 ဗီဒီယို (360p)"
                    
                    buttons.append([InlineKeyboardButton(
                        video_button_text,
                        callback_data=f"quality:360p:{url}"
                    )])
                    
                    # 480p button - clean and simple
                    size_480p = estimate_video_size(info, '480p')
                    # Still need size_480p for info display, but button is clean
                    if size_480p['size_mb']:
                        if size_480p['size_mb'] < 25:
                            time_est_480p = "~1-3 မိနစ်"
                        elif size_480p['size_mb'] < 50:
                            time_est_480p = "~3-5 မိနစ်"
                        else:
                            time_est_480p = "~6-10 မိနစ်"
                    else:
                        time_est_480p = "မသိ"
                    
                    video_480p_button_text = "📺 ဗီဒီယို (480p)"
                    
                    buttons.append([InlineKeyboardButton(
                        video_480p_button_text,
                        callback_data=f"quality:480p:{url}"
                    )])
                    
                    # Audio button - clean and simple
                    if size_audio['size_mb']:
                        # Keep time estimates for info display
                        if size_audio['size_mb'] < 10:
                            audio_time_est = "~30စက္ကန့်-1မိနစ်"
                        else:
                            audio_time_est = "~1-2 မိနစ်"
                    else:
                        audio_time_est = "မသိ"
                    
                    audio_button_text = "🎵 အသံသီးသန့် (M4A)"
                    
                    buttons.append([InlineKeyboardButton(
                        audio_button_text,
                        callback_data=f"quality:audio:{url}"
                    )])
                    
                    # Add info and refresh buttons
                    buttons.append([
                        InlineKeyboardButton("ℹ️ အသေးစိတ်", callback_data=f"info:{url}"),
                        InlineKeyboardButton("🔄 ပြန်စစ်", callback_data=f"refresh:{url}")
                    ])
                    
                    reply_markup = InlineKeyboardMarkup(buttons)
                    
                    # Create rich preview text with comprehensive information
                    preview_text = (
                        f"🎬 **{title[:60]}{'...' if len(title) > 60 else ''}**\n"
                        f"{'=' * 35}\n\n"
                        f"👤 **ချန်နယ်:** {uploader[:30]}{'...' if len(uploader) > 30 else ''}\n"
                        f"👀 **ကြည့်ရှုမှု:** {view_str} views\n"
                        f"💖 **နှစ်သက်မှု:** {like_str} likes\n"
                        f"⏰ **ကြာချိန်:** {duration_str}\n"
                        f"📅 **တင်သည့်ရက်:** {upload_str}\n\n"
                        f"📊 **ဖိုင်အရွယ်အစား ခန့်မှန်းချက်:**\n"
                        f"├─ 📱 360p: ~{size_360p['size_mb']:.0f}MB ({time_est})\n" if size_360p['size_mb'] else f"├─ 📱 360p: အရွယ်အစား မသိ\n"
                        f"├─ 📺 480p: ~{size_480p['size_mb']:.0f}MB ({time_est_480p})\n" if size_480p['size_mb'] else f"├─ 📺 480p: အရွယ်အစား မသိ\n"
                        f"└─ 🎵 အသံ: ~{size_audio['size_mb']:.0f}MB ({audio_time_est})\n\n" if size_audio['size_mb'] else f"└─ 🎵 အသံ: အရွယ်အစား မသိ\n\n"
                        f"📏 **ကန့်သတ်ချက်များ:**\n"
                        f"• ဒေါင်းလုဒ်: 200MB အထိ\n"
                        f"• Telegram ပို့ခြင်း: 50MB အထိ\n\n"
                    )
                    
                    # Add warnings if files are large
                    if size_360p.get('over_telegram_limit') or size_audio.get('over_telegram_limit'):
                        preview_text += "⚠️ **သတိ:** တချို့ ဖိုင်များသည် Telegram ကန့်သတ်ချက် (50MB) ထက် ကြီးနိုင်သည်\n\n"
                    
                    if size_360p.get('over_limit') or size_audio.get('over_limit'):
                        preview_text += "🚨 **သတိ:** တချို့ ဖိုင်များသည် ဒေါင်းလုဒ် ကန့်သတ်ချက် (200MB) ထက် ကြီးနိုင်သည်\n\n"
                    
                    # Add smart recommendation
                    if recommendation:
                        preview_text += f"{recommendation}\n\n"
                    
                    preview_text += "🎯 **အရည်အသွေး ရွေးချယ်ပါ:**"

                    # Delete the loading message
                    await safe_delete_message(info_message)

                    # Send thumbnail with video info and buttons
                    if thumbnail_url:
                        try:
                            await update.message.reply_photo(
                                photo=thumbnail_url,
                                caption=preview_text,
                                reply_markup=reply_markup,
                                parse_mode='Markdown'
                            )
                            return
                        except BadRequest as e:
                            logger.warning(f"Failed to send thumbnail: {e}")
                        except Exception as e:
                            logger.error(f"Unexpected error sending thumbnail: {e}", exc_info=True)

                    # Fallback to text if thumbnail fails
                    await update.message.reply_text(preview_text, reply_markup=reply_markup, parse_mode='Markdown')
                    return
                    
            except Exception as e:
                logger.error(f"Size estimation error: {e}", exc_info=True)
                
            # Fallback to basic quality selection if size estimation fails
            await safe_delete_message(info_message)
            
            keyboard = [
                [InlineKeyboardButton("📱 Video (360p)", callback_data=f"quality:360p:{url}")],
                [InlineKeyboardButton("🎵 Music Only (MP3)", callback_data=f"quality:audio:{url}")],
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                "🎬 အရည်အသွေး ရွေးပါ:",
                reply_markup=reply_markup
            )
            return

        # For non-YouTube platforms (TikTok, Instagram), download directly
        await download_tiktok_instagram(update, url)

    except Exception as e:
        logger.error(f"Error in handle_message: {e}", exc_info=True)
        try:
            await update.message.reply_text(
                "❌ စာကို ပြုစုရာတွင် ပြဿနာရှိပါသည်။\n\n"
                f"အမှားအမျိုးအစား: {type(e).__name__}\n\n"
                "ထပ်ကြိုးစားပါ။"
            )
        except Exception as reply_error:
            logger.error(f"Failed to send error message: {reply_error}")

async def show_detailed_info(query, url: str):
    """Show detailed technical information about the video."""
    try:
        await query.answer("🔍 အသေးစိတ် အချက်အလက်များ ရယူနေပါသည်...")
        
        # Edit message to show loading
        await query.message.edit_text(
            "🔍 **အသေးစိတ် အချက်အလက်များ ရယူနေပါသည်...**\n\n"
            "📊 ဗီဒီယို ဖော်မတ်များ စစ်ဆေးနေပါသည်\n"
            "🎵 အသံ ဖော်မတ်များ စစ်ဆေးနေပါသည်\n"
            "📏 ဖိုင်အရွယ်အစားများ တွက်ချက်နေပါသည်",
            parse_mode='Markdown'
        )
        
        # Extract detailed video information
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 20,
            'listformats': True,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        
        if info:
            title = info.get('title', 'Unknown Video')
            uploader = info.get('uploader', 'Unknown Channel')
            duration = info.get('duration', 0)
            formats = info.get('formats', [])
            
            # Format duration
            if duration:
                hours = duration // 3600
                minutes = (duration % 3600) // 60
                seconds = duration % 60
                if hours > 0:
                    duration_str = f"{hours}:{minutes:02d}:{seconds:02d}"
                else:
                    duration_str = f"{minutes}:{seconds:02d}"
            else:
                duration_str = "Unknown"
            
            # Analyze available formats
            video_formats = []
            audio_formats = []
            
            for fmt in formats:
                if isinstance(fmt, dict):
                    format_id = fmt.get('format_id', 'unknown')
                    ext = fmt.get('ext', 'unknown')
                    resolution = fmt.get('resolution', 'unknown')
                    filesize = fmt.get('filesize') or fmt.get('filesize_approx')
                    vcodec = fmt.get('vcodec', 'unknown')
                    acodec = fmt.get('acodec', 'unknown')
                    
                    if fmt.get('height'):  # Video format
                        size_mb = f"{filesize/(1024*1024):.1f}MB" if filesize else "Unknown"
                        video_formats.append(f"• {resolution} ({ext}) - {size_mb}")
                    elif acodec != 'none':  # Audio format
                        size_mb = f"{filesize/(1024*1024):.1f}MB" if filesize else "Unknown"
                        audio_formats.append(f"• {acodec} ({ext}) - {size_mb}")
            
            # Limit display to avoid message length issues
            video_formats = video_formats[:5]
            audio_formats = audio_formats[:3]
            
            detailed_text = (
                f"ℹ️ **အသေးစိတ် အချက်အလက်များ**\n"
                f"{'=' * 40}\n\n"
                f"🎬 **ခေါင်းစဉ်:** {title[:50]}{'...' if len(title) > 50 else ''}\n"
                f"👤 **ချန်နယ်:** {uploader[:30]}{'...' if len(uploader) > 30 else ''}\n"
                f"⏰ **ကြာချိန်:** {duration_str}\n\n"
                f"📹 **ရရှိနိုင်သော ဗီဒီယို ဖော်မတ်များ:**\n"
            )
            
            if video_formats:
                detailed_text += "\n".join(video_formats) + "\n\n"
            else:
                detailed_text += "• မတွေ့ရှိပါ\n\n"
            
            detailed_text += f"🎵 **ရရှိနိုင်သော အသံ ဖော်မတ်များ:**\n"
            
            if audio_formats:
                detailed_text += "\n".join(audio_formats) + "\n\n"
            else:
                detailed_text += "• မတွေ့ရှိပါ\n\n"
            
            detailed_text += (
                f"🔧 **နည်းပညာ အချက်အလက်:**\n"
                f"• ဗီဒီယို ကုဒက်: H.264/AVC\n"
                f"• အသံ ကုဒက်: AAC/MP3\n"
                f"• ကွန်တိန်နာ: MP4\n"
                f"• ရင်းမြစ်: YouTube\n\n"
                f"⚠️ **သတိပေးချက်:**\n"
                f"• ဖော်မတ် ရရှိနိုင်မှု ပြောင်းလဲနိုင်သည်\n"
                f"• ဖိုင်အရွယ်အစားများသည် ခန့်မှန်းချက်သာ\n"
                f"• အသေးစိတ်များ ယာယီ ပြောင်းလဲနိုင်သည်"
            )
            
            # Create back button
            back_button = InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ ပြန်သွား", callback_data=f"refresh:{url}")]
            ])
            
            await query.message.edit_text(
                detailed_text,
                reply_markup=back_button,
                parse_mode='Markdown'
            )
        else:
            await query.message.edit_text(
                "❌ **အသေးစိတ် အချက်အလက် ရယူ၍ မရပါ**\n\n"
                "ဗီဒီယို အချက်အလက်များ ရယူရာတွင် ပြဿနာ ရှိပါသည်။\n"
                "ကျေးဇူးပြု၍ နောက်မှ ပြန်ကြိုးစားပါ။",
                parse_mode='Markdown'
            )
            
    except Exception as e:
        logger.error(f"Error in show_detailed_info: {e}", exc_info=True)
        await safe_edit_message(
            query.message,
            "❌ **အချက်အလက် ပြသရာတွင် အမှား**\n\n"
            "အသေးစိတ် အချက်အလက်များ ပြသရာတွင် ပြဿနာ ရှိပါသည်။\n"
            f"အမှားအမျိုးအစား: {type(e).__name__}\n\n"
            "ကျေးဇူးပြု၍ ပြန်ကြိုးစားပါ။",
            parse_mode='Markdown'
        )

async def refresh_video_info(query, url: str):
    """Refresh and reload video information."""
    try:
        await query.answer("🔄 အချက်အလက်များ ပြန်လည် ရယူနေပါသည်...")
        
        # Show refreshing message
        await query.message.edit_text(
            "🔄 **အချက်အလက်များ ပြန်လည် ရယူနေပါသည်...**\n\n"
            "📊 ဗီဒီယို အချက်အလက်များ စစ်ဆေးနေပါသည်\n"
            "📏 ဖိုင်အရွယ်အစားများ ပြန်တွက်နေပါသည်\n"
            "🎯 အရည်အသွေး ရွေးချယ်မှုများ ပြင်ဆင်နေပါသည်",
            parse_mode='Markdown'
        )
        
        # Re-extract video information (similar to handle_message but for refresh)
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 15,
            'extract_flat': False,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        
        if info:
            # Extract same information as in handle_message
            title = info.get('title', 'Unknown Video')
            uploader = info.get('uploader', 'Unknown Channel')
            duration = info.get('duration', 0)
            view_count = info.get('view_count', 0)
            like_count = info.get('like_count', 0)
            upload_date = info.get('upload_date', '')
            
            # Format duration
            if duration:
                hours = duration // 3600
                minutes = (duration % 3600) // 60
                seconds = duration % 60
                if hours > 0:
                    duration_str = f"{hours}:{minutes:02d}:{seconds:02d}"
                else:
                    duration_str = f"{minutes}:{seconds:02d}"
            else:
                duration_str = "Unknown"
            
            # Format view and like counts
            if view_count >= 1000000:
                view_str = f"{view_count/1000000:.1f}M"
            elif view_count >= 1000:
                view_str = f"{view_count/1000:.1f}K"
            else:
                view_str = str(view_count) if view_count > 0 else "Unknown"
            
            if like_count >= 1000000:
                like_str = f"{like_count/1000000:.1f}M"
            elif like_count >= 1000:
                like_str = f"{like_count/1000:.1f}K"
            else:
                like_str = str(like_count) if like_count > 0 else "Unknown"
            
            # Format upload date
            if upload_date and len(upload_date) >= 8:
                try:
                    year = upload_date[:4]
                    month = upload_date[4:6]
                    day = upload_date[6:8]
                    upload_str = f"{day}/{month}/{year}"
                except:
                    upload_str = "Unknown"
            else:
                upload_str = "Unknown"
            
            # Smart recommendations
            recommendation = ""
            if duration and duration > 1800:
                recommendation = "💡 ရှည်လျားသော ဗီဒီယို → 🎵 အသံသာ အကြံပြုပါသည်"
            elif any(word in title.lower() for word in ['music', 'song', 'audio', 'playlist']):
                recommendation = "🎵 ဂီတဗီဒီယို → အသံ နှုတ်ယူမှု အကောင်းဆုံး"
            elif any(word in title.lower() for word in ['tutorial', 'how to', 'lesson', 'guide']):
                recommendation = "📚 သင်ခန်းစာဗီဒီယို → ဗီဒီယို ကြည့်ရှုရန် အကြံပြုပါသည်"
            
            # Re-estimate sizes
            size_360p = estimate_video_size(info, '360p')
            size_audio = estimate_video_size(info, 'audio')
            
            # Recreate buttons
            buttons = []
            
            if size_360p['size_mb']:
                video_size = f"{size_360p['size_mb']:.0f}MB"
                video_emoji = "⚠️" if size_360p['over_limit'] else "📱"
                if size_360p['size_mb'] < 20:
                    time_est = "~1-2 မိနစ်"
                elif size_360p['size_mb'] < 50:
                    time_est = "~2-4 မိနစ်"
                else:
                    time_est = "~5-8 မိနစ်"
                video_button_text = f"{video_emoji} ဗီဒီယို (360p) - {video_size}"
            else:
                video_button_text = "📱 ဗီဒီယို (360p) - အရွယ်အစား မသိ"
                time_est = "မသိ"
            
            buttons.append([InlineKeyboardButton(
                video_button_text,
                callback_data=f"quality:360p:{url}"
            )])
            
            if size_audio['size_mb']:
                audio_size = f"{size_audio['size_mb']:.0f}MB"
                audio_emoji = "⚠️" if size_audio['over_limit'] else "🎵"
                if size_audio['size_mb'] < 10:
                    audio_time_est = "~30စက္ကန့်-1မိနစ်"
                else:
                    audio_time_est = "~1-2 မိနစ်"
                audio_button_text = f"{audio_emoji} အသံသီးသန့် (MP3) - {audio_size}"
            else:
                audio_button_text = "🎵 အသံသီးသန့် (MP3) - အရွယ်အစား မသိ"
                audio_time_est = "မသိ"
            
            buttons.append([InlineKeyboardButton(
                audio_button_text,
                callback_data=f"quality:audio:{url}"
            )])
            
            buttons.append([
                InlineKeyboardButton("ℹ️ အသေးစိတ်", callback_data=f"info:{url}"),
                InlineKeyboardButton("🔄 ပြန်စစ်", callback_data=f"refresh:{url}")
            ])
            
            reply_markup = InlineKeyboardMarkup(buttons)
            
            # Create refreshed preview text
            preview_text = (
                f"🔄 **ပြန်လည်ရယူပြီး** - {title[:50]}{'...' if len(title) > 50 else ''}\n"
                f"{'=' * 35}\n\n"
                f"👤 **ချန်နယ်:** {uploader[:30]}{'...' if len(uploader) > 30 else ''}\n"
                f"👀 **ကြည့်ရှုမှု:** {view_str} views\n"
                f"💖 **နှစ်သက်မှု:** {like_str} likes\n"
                f"⏰ **ကြာချိန်:** {duration_str}\n"
                f"📅 **တင်သည့်ရက်:** {upload_str}\n\n"
                f"📊 **ဖိုင်အရွယ်အစား ခန့်မှန်းချက်:**\n"
                f"├─ 📱 ဗီဒီယို: ~{size_360p['size_mb']:.0f}MB ({time_est})\n" if size_360p['size_mb'] else f"├─ 📱 ဗီဒီယို: အရွယ်အစား မသိ\n"
                f"└─ 🎵 အသံ: ~{size_audio['size_mb']:.0f}MB ({audio_time_est})\n\n" if size_audio['size_mb'] else f"└─ 🎵 အသံ: အရွယ်အစား မသိ\n\n"
                f"📏 **ကန့်သတ်ချက်များ:**\n"
                f"• ဒေါင်းလုဒ်: 200MB အထိ\n"
                f"• Telegram ပို့ခြင်း: 50MB အထိ\n\n"
            )
            
            if size_360p.get('over_telegram_limit') or size_audio.get('over_telegram_limit'):
                preview_text += "⚠️ **သတိ:** တချို့ ဖိုင်များသည် Telegram ကန့်သတ်ချက် (50MB) ထက် ကြီးနိုင်သည်\n\n"
            
            if size_360p.get('over_limit') or size_audio.get('over_limit'):
                preview_text += "🚨 **သတိ:** တချို့ ဖိုင်များသည် ဒေါင်းလုဒ် ကန့်သတ်ချက် (200MB) ထက် ကြီးနိုင်သည်\n\n"
            
            if recommendation:
                preview_text += f"{recommendation}\n\n"
            
            preview_text += "🎯 **အရည်အသွေး ရွေးချယ်ပါ:**"
            
            await query.message.edit_text(
                preview_text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            await query.message.edit_text(
                "❌ **အချက်အလက်များ ပြန်လည်ရယူ၍ မရပါ**\n\n"
                "ဗီဒီယို အချက်အလက်များ ပြန်လည်ရယူရာတွင် ပြဿနာ ရှိပါသည်။\n"
                "လင့်ခ် မှန်ကန်ကြောင်း စစ်ဆေး၍ ပြန်ကြိုးစားပါ။"
            )
            
    except Exception as e:
        logger.error(f"Error in refresh_video_info: {e}")
        try:
            await query.message.edit_text(
                "❌ **အချက်အလက် ပြန်ရယူရာတွင် အမှား**\n\n"
                "ဗီဒီယို အချက်အလက်များ ပြန်လည်ရယူရာတွင် ပြဿနာ ရှိပါသည်။\n"
                "အင်တာနက် ချိတ်ဆက်မှု စစ်ဆေး၍ ပြန်ကြိုးစားပါ။"
            )
        except:
            pass

async def handle_quality_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle quality selection button presses with enhanced error handling."""
    try:
        query = update.callback_query
        user_id = update.effective_user.id
        username = update.effective_user.username or "Unknown"
        
        logger.info(f"User {user_id} ({username}) selected quality: {query.data}")
        
        await query.answer()

        # Parse callback data: "action:param:url" 
        data = query.data.split(":", 2)
        if len(data) < 2:
            logger.warning(f"Invalid callback data: {query.data}")
            await query.message.reply_text("❌ ဖတ်မထွက်သော ကမန်းဒ်။ /start နှိပ်၍ ပြန်စတင်ပါ။")
            return

        action = data[0]
        
        # Handle different callback actions
        if action == "quality":
            if len(data) != 3 or not data[1] or not data[2]:
                logger.warning(f"Invalid quality callback data: {query.data}")
                await query.message.reply_text("❌ ဖတ်မထွက်သော အရည်အသွေး ကမန်းဒ်။")
                return
            quality = data[1].strip()
            url = data[2].strip()
            
            # Validate URL length to prevent abuse
            if len(url) > 2000:
                logger.warning(f"URL too long: {len(url)} characters")
                await query.message.reply_text("❌ URL အလွန်ရှည်လျှက်ရှိသည်။")
                return
                
        elif action == "info":
            # Handle info button - show detailed technical information
            if len(data) != 2 or not data[1]:
                await query.answer("❌ လင့်ခ် အချက်အလက် မရှိပါ။")
                return
            url = data[1].strip()
            
            # Validate URL length
            if len(url) > 2000:
                logger.warning(f"URL too long in info callback: {len(url)} characters")
                await query.answer("❌ URL အလွန်ရှည်လျှက်ရှိသည်။")
                return
                
            await show_detailed_info(query, url)
            return
        elif action == "refresh":
            # Handle refresh button - reload video information
            if len(data) != 2 or not data[1]:
                await query.answer("❌ လင့်ခ် အချက်အလက် မရှိပါ။")
                return
            url = data[1].strip()
            
            # Validate URL length
            if len(url) > 2000:
                logger.warning(f"URL too long in refresh callback: {len(url)} characters")
                await query.answer("❌ URL အလွန်ရှည်လျှက်ရှိသည်။")
                return
                
            await refresh_video_info(query, url)
            return
        else:
            logger.warning(f"Unknown callback action: {action}")
            await query.message.reply_text("❌ မသိရှိသော ကမန်းဒ်။")
            return
        
        # Validate quality and URL
        if quality not in ['360p', '480p', 'audio']:
            logger.warning(f"Invalid quality selected: {quality}")
            await query.message.reply_text("❌ သိမ်းဆည်းမထားသော အရည်အသွေး။")
            return
            
        if not is_valid_url(url):
            logger.warning(f"Invalid URL in callback: {url}")
            await query.message.reply_text("❌ မမှန်ကန်သော လင့်ခ်။ ပြန်စမ်းကြည့်ပါ။")
            return

        quality_labels = {
            '360p': '📱 Video (360p)',
            'audio': '🎵 Music Only (MP3)'
        }

        # Delete the original message and create status message
        try:
            await query.message.delete()
        except Exception:
            pass

        # Send status message
        status_message = await query.message.chat.send_message(
            f"⬇️ {quality_labels.get(quality, quality)} ဒေါင်းလုဒ်လုပ်နေပါသည်..."
        )

        # Download YouTube video/audio
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                # Set format based on quality selection
                if quality == 'audio':
                    # Download best audio as m4a (no FFmpeg needed for conversion)
                    format_str = 'bestaudio[ext=m4a]/bestaudio/best'
                    is_audio = True
                elif quality == '480p':
                    format_str = QUALITY_FORMATS['480p']
                    is_audio = False
                else:  # 360p
                    format_str = QUALITY_FORMATS['360p']
                    is_audio = False

                # Use sanitized filename to avoid special character issues on Windows
                output_template = os.path.join(temp_dir, 'youtube_video.%(ext)s')

                # Progress tracker
                progress = DownloadProgress()

                ydl_opts = {
                    'outtmpl': output_template,
                    'format': format_str,
                    'quiet': True,
                    'no_warnings': True,
                    'socket_timeout': 60,
                    'retries': config.max_retries,
                    'progress_hooks': [progress.progress_hook],
                    'restrictfilenames': True,  # Sanitize filenames for Windows
                    'merge_output_format': 'mp4' if quality != 'audio' else None,
                    # Configure FFmpeg merger to preserve original streams when merging
                    'postprocessor_args': ['-c:v', 'copy', '-c:a', 'aac'] if quality != 'audio' else [],
                }

                # Update status
                await status_message.edit_text(
                    f"⬇️ YouTube {quality_labels.get(quality, quality)} ဒေါင်းလုဒ်လုပ်နေပါသည်...\n"
                    f"📥 ဖိုင်ကို ရယူနေပါသည်..."
                )

                # Download with progress updates
                async def update_progress():
                    last_percent = -1
                    update_count = 0
                    while progress.status != 'finished':
                        # Update every 10% or every 2 seconds
                        if progress.percent > last_percent + 10 or (update_count % 2 == 0 and progress.percent > 0):
                            last_percent = progress.percent
                            progress_bar = create_progress_bar(progress.percent)
                            speed_str = format_speed(progress.speed)
                            eta_str = format_eta(progress.eta)

                            try:
                                await status_message.edit_text(
                                    f"⬇️ YouTube {quality_labels.get(quality, quality)} ဒေါင်းလုဒ်လုပ်နေပါသည်...\n\n"
                                    f"{progress_bar} {progress.percent:.0f}%\n"
                                    f"📥 အမြန်နှုန်း: {speed_str}\n"
                                    f"⏳ ကျန်အချိန်: {eta_str}"
                                )
                            except:
                                pass
                        update_count += 1
                        await asyncio.sleep(0.5)

                # Start progress update task
                progress_task = asyncio.create_task(update_progress())

                # Download in separate thread to allow progress updates
                def do_download():
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        return ydl.extract_info(url, download=True)

                info = await asyncio.to_thread(do_download)

                # Cancel progress task
                progress_task.cancel()
                try:
                    await progress_task
                except asyncio.CancelledError:
                    pass

                if not info:
                    await status_message.edit_text(
                        "❌ YouTube ဗီဒီယို အချက်အလက် ရယူ၍ မရပါ။"
                    )
                    return

                title = info.get('title', 'video')[:50]

                # Find downloaded file
                downloaded_file = None
                for file in os.listdir(temp_dir):
                    if is_audio and file.endswith(('.m4a', '.mp3', '.webm', '.opus')):
                        downloaded_file = os.path.join(temp_dir, file)
                        break
                    elif not is_audio and file.endswith(('.mp4', '.webm', '.mkv')):
                        downloaded_file = os.path.join(temp_dir, file)
                        break

                # Fallback: find any media file
                if not downloaded_file:
                    for file in os.listdir(temp_dir):
                        if file.endswith(('.mp3', '.m4a', '.mp4', '.webm', '.mkv', '.opus')):
                            downloaded_file = os.path.join(temp_dir, file)
                            break

                if not downloaded_file or not os.path.exists(downloaded_file):
                    await status_message.edit_text(
                        "❌ ဒေါင်းလုဒ် ဖိုင် မတွေ့ပါ။\n"
                        "ထပ်ကြိုးစားပါ။"
                    )
                    return

                # Check file size
                file_size = get_file_size(downloaded_file)
                file_size_mb = file_size / (1024 * 1024)

                # Sanitize title for logging to avoid encoding issues
                safe_title = title.encode('ascii', 'ignore').decode('ascii') if title else 'video'
                logger.info(f"Downloaded YouTube {quality}: {safe_title}, size: {file_size_mb:.2f}MB")

                if file_size > config.max_file_size:
                    await status_message.edit_text(
                        f"❌ ဖိုင်အရွယ်အစား ({file_size_mb:.1f}MB) သည် ကန့်သတ်ချက် (200MB) ထက် ကြီးပါသည်။"
                    )
                    return

                if file_size > config.telegram_file_limit:
                    await status_message.edit_text(
                        f"❌ ဖိုင်အရွယ်အစား ({file_size_mb:.1f}MB) သည် Telegram ကန့်သတ်ချက် (50MB) ထက် ကြီးပါသည်။\n\n"
                        "🎵 အသံသီးသန့် (MP3) ကို စမ်းကြည့်ပါ။"
                    )
                    return

                # Update status before sending
                await status_message.edit_text(
                    f"📤 YouTube {quality_labels.get(quality, quality)} ပို့နေပါသည်...\n"
                    f"📁 အရွယ်အစား: {file_size_mb:.1f}MB\n"
                    f"⏳ ခေတ္တစောင့်ပါ..."
                )

                # Send file to user
                with open(downloaded_file, 'rb') as media_file:
                    if is_audio:
                        # Get file extension for caption
                        file_ext = os.path.splitext(downloaded_file)[1].upper().replace('.', '')
                        await query.message.chat.send_audio(
                            audio=media_file,
                            caption=f"🎵 {title}\n\n📺 YouTube Audio ({file_ext})\n📁 {file_size_mb:.1f}MB",
                            title=title
                        )
                    else:
                        # Determine quality label for caption
                        quality_label = quality.upper() if quality != 'audio' else 'Audio'
                        quality_emoji = "📺" if quality == "480p" else "📱"
                        
                        await query.message.chat.send_video(
                            video=media_file,
                            caption=f"🎬 {title}\n\n{quality_emoji} YouTube ({quality_label})\n📁 {file_size_mb:.1f}MB",
                            supports_streaming=True
                        )

                # Delete status message
                await status_message.delete()

                logger.info(f"Successfully sent YouTube {quality} to user {user_id} ({username})")

        except yt_dlp.utils.DownloadError as e:
            error_msg = str(e)
            logger.error(f"YouTube download error: {error_msg}")
            
            # Check if it's an ffmpeg-related error
            if 'ffmpeg' in error_msg.lower() or 'ffprobe' in error_msg.lower():
                logger.warning(f"FFmpeg error detected but should have been avoided: {error_msg}")
                await status_message.edit_text(
                    f"⚠️ Audio format issue\n\n"
                    f"There was an issue with audio processing.\n"
                    f"Error: {error_msg[:150]}\n\n"
                    f"Please try again or contact support."
                )
            
            await status_message.edit_text(
                f"❌ YouTube ဒေါင်းလုဒ် မအောင်မြင်ပါ။\n\n"
                f"အမှား: {error_msg[:100]}\n\n"
                "ထပ်ကြိုးစားပါ။"
            )

        except Exception as e:
            logger.error(f"Error downloading YouTube: {e}")
            await status_message.edit_text(
                f"❌ YouTube ဒေါင်းလုဒ် ပြုလုပ်ရာတွင် အမှားရှိပါသည်။\n\n"
                "ထပ်ကြိုးစားပါ။"
            )

    except Exception as e:
        logger.error(f"Error in handle_quality_callback: {e}")
        try:
            await query.message.reply_text("❌ ပြုစုရာတွင် ပြဿနာရှိပါသည်။ /start နှိပ်၍ ပြန်စတင်ပါ။")
        except:
            pass

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle errors in telegram bot polling."""
    error = context.error

    # Handle Conflict error (multiple instances)
    if isinstance(error, Conflict):
        logger.critical(
            f"❌ BOT CONFLICT ERROR: {error}\n"
            f"Another bot instance is already running with this token.\n"
            f"Please ensure only ONE bot instance is running at a time."
        )
        print("\n" + "="*70)
        print("❌ CRITICAL ERROR: Conflict Error")
        print("="*70)
        print(f"Message: {error}")
        print("\nCause: Another instance of this bot is already running!")
        print("Solution: Kill all other bot instances and restart.")
        print("="*70 + "\n")
        # Exit gracefully
        sys.exit(1)

    # Handle network errors gracefully
    elif isinstance(error, (NetworkError, TimedOut)):
        logger.warning(f"⚠️ Network error: {error}. Will retry automatically.")
        return

    # Log all other errors
    logger.error(f"Update {update} caused error: {error}")

def main():
    """Main function to run the bot with enhanced error handling."""
    try:
        # Check if bot token is set
        if not config.token or config.token == "YOUR_BOT_TOKEN_HERE":
            logger.error("❌ Bot token not configured properly!")
            print("❌ Error: BOT_TOKEN not set or using placeholder value")
            print("Please set your bot token in environment variables or update bot.py")
            return
        
        logger.info("🎬 Starting Video Downloader Bot...")
        print("🎬 Starting Enhanced Video Downloader Bot...")
        print(f"📊 Configuration: Max file size: {config.max_file_size//1024//1024}MB, Timeout: {config.download_timeout}s")
        print("✅ All platforms enabled: YouTube, TikTok, Instagram")
        
        # Create application with increased timeouts for large file uploads
        application = (
            Application.builder()
            .token(config.token)
            .read_timeout(90)  # 90 seconds for reading (increased for slow connections)
            .write_timeout(180)  # 180 seconds for writing large files (3 minutes for TikTok videos)
            .connect_timeout(60)  # 60 seconds for connection (increased)
            .pool_timeout(15)  # 15 seconds for pool (increased)
            .build()
        )
        
        # Add command handlers
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("help", help_command))
        
        # Add callback and message handlers
        application.add_handler(CallbackQueryHandler(handle_quality_callback, pattern="^(quality|info|refresh):"))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        # Add error handler
        application.add_error_handler(error_handler)

        logger.info("✅ Bot handlers configured successfully")
        print("✅ Bot is running! Press Ctrl+C to stop.")
        print("🔍 Bot logs are being saved to 'bot.log'")
        
        # Run the bot
        application.run_polling(drop_pending_updates=True)
        
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        print(f"❌ Failed to start bot: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()