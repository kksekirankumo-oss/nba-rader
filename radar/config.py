import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _csv(name: str, default: str = '') -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(',') if x.strip()]


class Config:
    PORT = _int('PORT', 5050)
    DB_PATH = os.getenv('DB_PATH', str(BASE_DIR / 'nba_radar.db'))
    DEFAULT_HOURS = _int('DEFAULT_HOURS', 24)

    # v0.7: still manual-only after one startup refresh.
    # With OAuth we can read Reddit rate-limit headers, but this local warning still helps prevent accidental spam-clicking.
    REDDIT_SAFE_INTERVAL_MINUTES = max(1, _int('REDDIT_SAFE_INTERVAL_MINUTES', 10))
    REDDIT_COOLDOWN_MINUTES = max(5, _int('REDDIT_COOLDOWN_MINUTES', 30))
    REDDIT_WARN_REMAINING = max(1, _int('REDDIT_WARN_REMAINING', 15))
    YOUTUBE_DAILY_WARN_CALLS = max(10, _int('YOUTUBE_DAILY_WARN_CALLS', 80))

    DEEPL_API_KEY = os.getenv('DEEPL_API_KEY', '').strip()

    # Reddit OAuth (application-only / client credentials)
    REDDIT_USER_AGENT = os.getenv(
        'REDDIT_USER_AGENT',
        'windows:nba-radar:v0.7 (by /u/your_reddit_username for personal monitoring)'
    ).strip()
    REDDIT_CLIENT_ID = os.getenv('REDDIT_CLIENT_ID', '').strip()
    REDDIT_CLIENT_SECRET = os.getenv('REDDIT_CLIENT_SECRET', '').strip()
    REDDIT_MEME_SUBREDDITS = _csv(
        'REDDIT_MEME_SUBREDDITS',
        'nbacirclejerk,Nbamemes'
    )

    YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY', '').strip()
    YOUTUBE_REGION = os.getenv('YOUTUBE_REGION', 'US').strip()
    YOUTUBE_LANGUAGE = os.getenv('YOUTUBE_LANGUAGE', 'en').strip()

    YOUTUBE_NEWS_QUERY = os.getenv('YOUTUBE_NEWS_QUERY', 'NBA').strip()
    YOUTUBE_NEWS_LOOKBACK_HOURS = _int('YOUTUBE_NEWS_LOOKBACK_HOURS', 24)
    YOUTUBE_NEWS_CHANNEL_WHITELIST = [
        s.lower() for s in _csv(
            'YOUTUBE_NEWS_CHANNEL_WHITELIST',
            'NBA,ESPN,Bleacher Report,House of Highlights'
        )
    ]

    YOUTUBE_MEME_QUERY = os.getenv(
        'YOUTUBE_MEME_QUERY',
        'NBA meme|NBA funny|NBA reaction|NBA edit|NBA shorts'
    ).strip()
    YOUTUBE_MEME_LOOKBACK_HOURS = _int('YOUTUBE_MEME_LOOKBACK_HOURS', 72)
