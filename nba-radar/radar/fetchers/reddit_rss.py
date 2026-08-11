import hashlib
import html
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin

import feedparser
import requests

from ..config import Config
from ..db import get_reddit_cooldown_until
from ..nba_filter import is_nba_relevant

TAG_RE = re.compile(r'<[^>]+>')
IMG_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)


def _clean(value: str) -> str:
    text = html.unescape(TAG_RE.sub(' ', value or ''))
    return re.sub(r'\s+', ' ', text).strip()


def _content_html(entry) -> str:
    content = entry.get('content') or []
    if content and isinstance(content, list):
        return content[0].get('value') or ''
    return entry.get('summary') or ''


def _thumbnail(raw_html: str) -> str | None:
    for match in IMG_RE.finditer(raw_html or ''):
        url = html.unescape(match.group(1))
        if any(host in url for host in ('i.redd.it', 'preview.redd.it', 'external-preview.redd.it', 'redditmedia.com')):
            return url
    return None


def _published(entry) -> str:
    value = entry.get('published') or entry.get('updated')
    if value:
        try:
            dt = parsedate_to_datetime(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        except Exception:
            pass
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc).isoformat()
        except Exception:
            pass
    return datetime.now(timezone.utc).isoformat()


def _feed_url(subreddit: str) -> str:
    # Fetch only NEW. The database retains older posts, so
    # repeatedly polling TOP is not worth doubling unauthenticated RSS traffic.
    return f'https://www.reddit.com/r/{subreddit}/new/.rss?limit=50'


def _retry_after_seconds(response) -> int:
    raw = (response.headers.get('Retry-After') or '').strip()
    if raw.isdigit():
        return max(0, int(raw))
    if raw:
        try:
            dt = parsedate_to_datetime(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return max(0, int((dt - datetime.now(timezone.utc)).total_seconds()))
        except Exception:
            pass
    return 0


def _cooldown_time(response=None) -> datetime:
    minimum = max(1, Config.REDDIT_COOLDOWN_MINUTES) * 60
    server_wait = _retry_after_seconds(response) if response is not None else 0
    return datetime.now(timezone.utc) + timedelta(seconds=max(minimum, server_wait))


def _error_message(exc: Exception) -> str:
    response = getattr(exc, 'response', None)
    if response is not None:
        code = getattr(response, 'status_code', None)
        if code == 429:
            return 'HTTP 429：Reddit 暂时限流'
        if code == 403:
            return 'HTTP 403：Reddit 拒绝了本次 feed 请求'
        if code:
            return f'HTTP {code}: {exc}'[:500]
    return str(exc)[:500]


def _cooldown_reports(until: datetime):
    attempted_at = datetime.now(timezone.utc).isoformat()
    until_iso = until.astimezone(timezone.utc).isoformat()
    local_hint = until.astimezone().strftime('%H:%M')
    return [{
        'source_key': f'reddit:{subreddit.lower()}',
        'display_name': f'Reddit r/{subreddit}',
        'group_name': 'meme',
        'status': 'cooldown',
        'attempted_at': attempted_at,
        'fetched_count': 0,
        'raw_count': 0,
        'message': f'此前收到 HTTP 429；冷却至约 {local_hint}，本次未向 Reddit 发请求',
        'cooldown_until': until_iso,
    } for subreddit in Config.REDDIT_MEME_SUBREDDITS]


def fetch_reddit_memes_report():
    """Low-frequency read-only meme discovery via public subreddit RSS.

    This reader is intentionally conservative:
    - one NEW feed request per subreddit (no TOP request on every refresh)
    - no background polling in v0.6; manual requests are guarded upstream
    - HTTP 429 creates a persisted cooldown; manual refreshes during cooldown
      do not send additional requests to Reddit
    """
    saved_cooldown = get_reddit_cooldown_until()
    now = datetime.now(timezone.utc)
    if saved_cooldown and saved_cooldown > now:
        return [], _cooldown_reports(saved_cooldown)

    all_items = []
    reports = []
    headers = {
        'User-Agent': Config.REDDIT_USER_AGENT,
        'Accept': 'application/atom+xml,application/rss+xml,application/xml,text/xml;q=0.9,*/*;q=0.1',
    }

    hit_429_until = None
    processed = set()

    for subreddit in Config.REDDIT_MEME_SUBREDDITS:
        if hit_429_until:
            break

        attempted_at = datetime.now(timezone.utc).isoformat()
        status_key = f'reddit:{subreddit.lower()}'
        unique_items = {}
        raw_count = 0
        feed_url = _feed_url(subreddit)

        try:
            resp = requests.get(feed_url, headers=headers, timeout=20)
            if resp.status_code == 429:
                hit_429_until = _cooldown_time(resp)
                msg = 'HTTP 429：Reddit 暂时限流'
                print(f'[reddit rss] r/{subreddit}: {msg}; cooldown until {hit_429_until.isoformat()}')
                reports.append({
                    'source_key': status_key,
                    'display_name': f'Reddit r/{subreddit}',
                    'group_name': 'meme',
                    'status': 'error',
                    'attempted_at': attempted_at,
                    'fetched_count': 0,
                    'raw_count': 0,
                    'message': msg,
                    'cooldown_until': hit_429_until.isoformat(),
                })
                processed.add(subreddit.lower())
                break

            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
            raw_count = len(feed.entries)
        except Exception as exc:
            msg = _error_message(exc)
            print(f'[reddit rss] r/{subreddit}: {msg}')
            reports.append({
                'source_key': status_key,
                'display_name': f'Reddit r/{subreddit}',
                'group_name': 'meme',
                'status': 'error',
                'attempted_at': attempted_at,
                'fetched_count': 0,
                'raw_count': 0,
                'message': msg,
                'cooldown_until': None,
            })
            processed.add(subreddit.lower())
            continue

        for entry in feed.entries[:50]:
            title = html.unescape((entry.get('title') or '').strip())
            url = (entry.get('link') or '').strip()
            raw = _content_html(entry)
            summary = _clean(raw)
            if not title or not url:
                continue
            if not is_nba_relevant(title, summary, trusted_nba_feed=True):
                continue

            ext = entry.get('id') or hashlib.sha256(url.encode()).hexdigest()
            author = (entry.get('author') or '').replace('/u/', '').strip()
            item = {
                'external_id': str(ext),
                'source': 'reddit',
                'source_name': f'Reddit r/{subreddit}',
                'category': 'meme',
                'title': title,
                'summary': summary[:600],
                'url': urljoin('https://www.reddit.com', url),
                'author': author or f'r/{subreddit}',
                'published_at': _published(entry),
                'thumbnail_url': _thumbnail(raw),
                'metrics': {},
                '_status_key': status_key,
            }
            unique_items[str(ext)] = item

        all_items.extend(unique_items.values())
        reports.append({
            'source_key': status_key,
            'display_name': f'Reddit r/{subreddit}',
            'group_name': 'meme',
            'status': 'success',
            'attempted_at': attempted_at,
            'fetched_count': len(unique_items),
            'raw_count': raw_count,
            'message': '',
            'cooldown_until': None,
        })
        processed.add(subreddit.lower())

    # One 429 means we stop sending Reddit requests for the rest of this run.
    # Mark untouched communities as cooldown instead of probing them too.
    if hit_429_until:
        until_iso = hit_429_until.isoformat()
        local_hint = hit_429_until.astimezone().strftime('%H:%M')
        attempted_at = datetime.now(timezone.utc).isoformat()
        for subreddit in Config.REDDIT_MEME_SUBREDDITS:
            if subreddit.lower() in processed:
                continue
            reports.append({
                'source_key': f'reddit:{subreddit.lower()}',
                'display_name': f'Reddit r/{subreddit}',
                'group_name': 'meme',
                'status': 'cooldown',
                'attempted_at': attempted_at,
                'fetched_count': 0,
                'raw_count': 0,
                'message': f'其他 Reddit feed 已收到 429；冷却至约 {local_hint}，本次未请求',
                'cooldown_until': until_iso,
            })

    return all_items, reports


def fetch_reddit_memes():
    return fetch_reddit_memes_report()[0]
