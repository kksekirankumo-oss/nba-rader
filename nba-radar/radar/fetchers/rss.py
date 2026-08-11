import hashlib
import html
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser
import requests

from ..nba_filter import is_nba_relevant

TAG_RE = re.compile(r'<[^>]+>')


def _clean(value: str) -> str:
    return html.unescape(TAG_RE.sub(' ', value or '')).replace('  ', ' ').strip()


RSS_SOURCES = [
    {
        'key': 'rss:espn_nba',
        'name': 'ESPN NBA',
        'url': 'https://www.espn.com/espn/rss/nba/news',
    },
    {
        'key': 'rss:yahoo_nba',
        'name': 'Yahoo Sports NBA',
        'url': 'https://sports.yahoo.com/nba/rss/',
    },
]


def _published(entry):
    value = entry.get('published') or entry.get('updated')
    if value:
        try:
            dt = parsedate_to_datetime(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        except Exception:
            pass
    return datetime.now(timezone.utc).isoformat()


def _error_message(exc: Exception) -> str:
    response = getattr(exc, 'response', None)
    if response is not None:
        code = getattr(response, 'status_code', None)
        if code == 429:
            return 'HTTP 429：来源暂时限流'
        if code:
            return f'HTTP {code}: {exc}'[:500]
    return str(exc)[:500]


def fetch_rss_report():
    items = []
    reports = []
    headers = {'User-Agent': 'NBA-Radar/0.6 personal-news-aggregator'}

    for source in RSS_SOURCES:
        attempted_at = datetime.now(timezone.utc).isoformat()
        raw_count = 0
        accepted = 0
        try:
            resp = requests.get(source['url'], headers=headers, timeout=15)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
            raw_count = len(feed.entries)
        except Exception as exc:
            msg = _error_message(exc)
            print(f'[rss] {source["name"]}: {msg}')
            reports.append({
                'source_key': source['key'],
                'display_name': source['name'],
                'group_name': 'news',
                'status': 'error',
                'attempted_at': attempted_at,
                'fetched_count': 0,
                'raw_count': 0,
                'message': msg,
            })
            continue

        for entry in feed.entries[:50]:
            title = (entry.get('title') or '').strip()
            summary = _clean(entry.get('summary') or entry.get('description') or '')
            url = (entry.get('link') or '').strip()
            if not title or not url:
                continue
            if not is_nba_relevant(title, summary, trusted_nba_feed=True):
                continue
            ext = entry.get('id') or hashlib.sha256(url.encode()).hexdigest()
            items.append({
                'external_id': str(ext),
                'source': 'rss',
                'source_name': source['name'],
                'category': 'news',
                'title': title,
                'summary': summary,
                'url': url,
                'author': entry.get('author') or source['name'],
                'published_at': _published(entry),
                'metrics': {},
                '_status_key': source['key'],
            })
            accepted += 1

        reports.append({
            'source_key': source['key'],
            'display_name': source['name'],
            'group_name': 'news',
            'status': 'success',
            'attempted_at': attempted_at,
            'fetched_count': accepted,
            'raw_count': raw_count,
            'message': '',
        })

    return items, reports


def fetch_rss():
    return fetch_rss_report()[0]
