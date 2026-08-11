from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests

from ..config import Config
from ..nba_filter import is_nba_relevant

SEARCH_URL = 'https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts'

MEME_HINTS = (
    'meme', 'funny', 'lol', 'lmao', 'cooked', 'washed', 'brick', 'flop', 'merchant',
    'whistle', 'free throw', 'reaction', 'edit', 'bro ', '😭', '😂', '💀', '🤣',
)


def _since() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=Config.BLUESKY_LOOKBACK_HOURS)).isoformat().replace('+00:00', 'Z')


def _post_url(post: dict) -> str:
    uri = post.get('uri') or ''
    handle = ((post.get('author') or {}).get('handle') or '').strip()
    if not handle or '/app.bsky.feed.post/' not in uri:
        return 'https://bsky.app/'
    rkey = uri.rsplit('/', 1)[-1]
    return f'https://bsky.app/profile/{quote(handle, safe=".@:-")}/post/{quote(rkey, safe="")}'


def _thumb(post: dict):
    embed = post.get('embed') or {}
    # app.bsky.embed.images#view
    images = embed.get('images') or []
    if images:
        return images[0].get('thumb') or images[0].get('fullsize')
    # recordWithMedia view -> media.images
    media = embed.get('media') or {}
    images = media.get('images') or []
    if images:
        return images[0].get('thumb') or images[0].get('fullsize')
    # external card thumb
    external = embed.get('external') or {}
    return external.get('thumb')


def _text(post: dict) -> str:
    record = post.get('record') or {}
    return str(record.get('text') or '').strip()


def _published(post: dict) -> str:
    record = post.get('record') or {}
    return record.get('createdAt') or post.get('indexedAt') or datetime.now(timezone.utc).isoformat()


def _engagement(post: dict) -> int:
    return int(post.get('likeCount') or 0) + int(post.get('repostCount') or 0) * 2 + int(post.get('replyCount') or 0) * 2 + int(post.get('quoteCount') or 0) * 2


def _looks_memeish(text: str) -> bool:
    low = text.lower()
    return any(x in low for x in MEME_HINTS)


def fetch_bluesky_memes_report():
    attempted_at = datetime.now(timezone.utc).isoformat()
    status_key = 'bluesky:meme'
    if not Config.BLUESKY_ENABLED:
        return [], {
            'source_key': status_key,
            'display_name': 'Bluesky 梗 / 球迷反应',
            'group_name': 'meme',
            'status': 'disabled',
            'attempted_at': attempted_at,
            'fetched_count': 0,
            'raw_count': 0,
            'message': 'BLUESKY_ENABLED=0',
        }

    seen = {}
    raw_total = 0
    errors = []
    query_count = 0

    for query in Config.BLUESKY_MEME_QUERIES:
        try:
            resp = requests.get(SEARCH_URL, params={
                'q': query,
                'sort': 'top',
                'since': _since(),
                'limit': Config.BLUESKY_MAX_PER_QUERY,
            }, timeout=20)
            resp.raise_for_status()
            posts = resp.json().get('posts') or []
            query_count += 1
            raw_total += len(posts)
        except Exception as exc:
            response = getattr(exc, 'response', None)
            code = getattr(response, 'status_code', None) if response is not None else None
            errors.append(f'{query}: HTTP {code}' if code else f'{query}: {str(exc)[:100]}')
            continue

        specific_query = any(h in query.lower() for h in ('meme', 'funny', 'reaction', 'edit'))
        for post in posts:
            text = _text(post)
            if not text:
                continue
            if not is_nba_relevant(text, '', trusted_nba_feed=True):
                continue
            engagement = _engagement(post)
            if not specific_query and not _looks_memeish(text) and engagement < Config.BLUESKY_MIN_ENGAGEMENT:
                continue
            uri = post.get('uri') or _post_url(post)
            author = post.get('author') or {}
            display = author.get('displayName') or author.get('handle') or 'Bluesky'
            seen[uri] = {
                'external_id': uri,
                'source': 'bluesky',
                'source_name': 'Bluesky',
                'category': 'meme',
                'title': text[:350],
                'summary': f'Bluesky 球迷帖子 · @{author.get("handle", "")}',
                'url': _post_url(post),
                'author': display,
                'published_at': _published(post),
                'thumbnail_url': _thumb(post),
                'metrics': {
                    'like_count': int(post.get('likeCount') or 0),
                    'comment_count': int(post.get('replyCount') or 0),
                    'repost_count': int(post.get('repostCount') or 0),
                    'quote_count': int(post.get('quoteCount') or 0),
                },
                '_status_key': status_key,
            }

    items = list(seen.values())
    items.sort(key=_engagement_for_item, reverse=True)
    message = f'{query_count}/{len(Config.BLUESKY_MEME_QUERIES)} 个查询成功'
    if errors:
        message += '；失败：' + ' | '.join(errors[:3])
    status = 'success' if query_count == len(Config.BLUESKY_MEME_QUERIES) else ('partial' if query_count else 'error')
    return items, {
        'source_key': status_key,
        'display_name': 'Bluesky 梗 / 球迷反应',
        'group_name': 'meme',
        'status': status,
        'attempted_at': attempted_at,
        'fetched_count': len(items),
        'raw_count': raw_total,
        'message': message,
    }


def _engagement_for_item(item: dict) -> int:
    m = item.get('metrics') or {}
    return int(m.get('like_count') or 0) + int(m.get('comment_count') or 0) * 2 + int(m.get('repost_count') or 0) * 2 + int(m.get('quote_count') or 0) * 2
