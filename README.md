# 🎬 Video Downloader Bot

A powerful Telegram bot for downloading videos from YouTube, TikTok, and Instagram with advanced features like quality selection, progress tracking, and rate limiting.

![Python](https://img.shields.io/badge/python-3.11+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Platform](https://img.shields.io/badge/platform-Telegram-blue.svg)

## ✨ Features

### 🎥 Multi-Platform Support
- **YouTube**: Download videos in 360p, 480p, or extract audio (M4A)
- **TikTok**: Download videos in original quality
- **Instagram**: Download posts and reels

### 🛡️ Security & Reliability
- ✅ No hardcoded secrets
- ✅ Input validation & sanitization
- ✅ Rate limiting (per-user & global)
- ✅ Resource management
- ✅ Comprehensive error handling

### 📊 User Experience
- Quality selection for YouTube
- File size estimation
- Real-time download progress tracking
- Video thumbnail previews
- Smart content recommendations

## 🚀 Quick Start

### Prerequisites
- Python 3.11 or higher
- FFmpeg installed
- Telegram Bot Token from [@BotFather](https://t.me/BotFather)

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/kuramaOn/tg_bot.git
   cd tg_bot
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment variables**
   
   Create a `.env` file:
   ```env
   BOT_TOKEN=your_telegram_bot_token_here
   MAX_FILE_SIZE=209715200
   DOWNLOAD_TIMEOUT=300
   ```

4. **Run the bot**
   ```bash
   python bot.py
   ```

## 📦 Deployment

This bot can be deployed on various platforms:

### Railway (Recommended - Easy)
```bash
# Install Railway CLI
npm install -g @railway/cli

# Login and deploy
railway login
railway init
railway up
```

### Render.com
1. Connect your GitHub repository
2. Set environment variable: `BOT_TOKEN`
3. Deploy automatically

### VPS (DigitalOcean, Linode, etc.)
See [DEPLOYMENT_OPTIONS.md](DEPLOYMENT_OPTIONS.md) for detailed instructions.

## 🎯 Usage

1. Start a chat with your bot on Telegram
2. Send a video URL (YouTube, TikTok, or Instagram)
3. For YouTube: Select quality (360p, 480p, or audio only)
4. Wait for the download and receive your video!

### Supported URL Formats

**YouTube:**
- `https://youtube.com/watch?v=VIDEO_ID`
- `https://youtu.be/VIDEO_ID`
- `https://youtube.com/shorts/VIDEO_ID`

**TikTok:**
- `https://tiktok.com/@username/video/ID`
- `https://vm.tiktok.com/SHORT_ID`

**Instagram:**
- `https://instagram.com/p/POST_ID`
- `https://instagram.com/reel/REEL_ID`

## ⚙️ Configuration

Environment variables you can customize:

| Variable | Default | Description |
|----------|---------|-------------|
| `BOT_TOKEN` | Required | Telegram bot token |
| `MAX_FILE_SIZE` | 209715200 | Max download size (200MB) |
| `DOWNLOAD_TIMEOUT` | 300 | Download timeout in seconds |
| `TELEGRAM_FILE_LIMIT` | 52428800 | Telegram's 50MB file limit |
| `RATE_LIMIT_REQUESTS` | 5 | Requests per period |
| `RATE_LIMIT_PERIOD` | 60 | Rate limit period (seconds) |
| `MAX_CONCURRENT_DOWNLOADS` | 10 | Global concurrent downloads |
| `MAX_DOWNLOADS_PER_USER` | 2 | Per-user concurrent downloads |
| `LOG_LEVEL` | INFO | Logging level |

## 📂 Project Structure

```
tg_bot/
├── bot.py                  # Main bot application
├── config.py               # Configuration management
├── exceptions.py           # Custom exceptions
├── validators.py           # Input validation
├── rate_limiter.py        # Rate limiting logic
├── resource_manager.py    # Resource management
├── utils.py               # Utility functions
├── test_bot.py            # Test suite
├── requirements.txt       # Production dependencies
├── requirements-dev.txt   # Development dependencies
├── .env                   # Environment variables (not in git)
├── .gitignore            # Git ignore rules
└── README.md             # This file
```

## 🧪 Testing

Run the test suite:

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Run tests
python test_bot.py
```

## 🔒 Security

- Bot token is stored in environment variables
- Input validation prevents injection attacks
- Rate limiting prevents abuse
- File size limits prevent resource exhaustion
- No sensitive data in logs

## 📏 Limitations

- **Download limit**: 200MB per file
- **Telegram send limit**: 50MB per file
- **Download timeout**: 5 minutes
- **Rate limit**: 5 requests per minute per user

## 🐛 Troubleshooting

### Bot not responding
1. Check if bot token is correct
2. Verify internet connection
3. Check logs in `bot.log`

### FFmpeg errors
Make sure FFmpeg is installed:
```bash
# Ubuntu/Debian
sudo apt install ffmpeg

# Windows (using Chocolatey)
choco install ffmpeg

# macOS (using Homebrew)
brew install ffmpeg
```

### File too large errors
- For YouTube: Try selecting audio-only format
- Videos over 50MB cannot be sent via Telegram bots
- Consider reducing `MAX_FILE_SIZE` in configuration

## 🤝 Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) - Telegram Bot API wrapper
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) - YouTube & video downloader
- [FFmpeg](https://ffmpeg.org/) - Multimedia processing

## 📞 Support

- **Issues**: [GitHub Issues](https://github.com/kuramaOn/tg_bot/issues)
- **Discussions**: [GitHub Discussions](https://github.com/kuramaOn/tg_bot/discussions)

---

**Made with ❤️ for the Telegram community**

**Version**: 2.0.0  
**Last Updated**: December 2024
