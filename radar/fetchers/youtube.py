from datetime import datetime, timedelta, timezone

import requests

from ..config import Config
from ..nba_filter import is_nba_relevant

SEARCH_URL = 'https://www.googleapis.com/youtube/v3/search'
VIDEOS_URL = 'https://www.googleapis.com/youtube/v3/videos'


def _published_after(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat().replace('+00:00', 'Z')


def _error_message(exc: Exception) -> str:
    response = getattr(exc, 'response', None)
    if response is not None:
        code = getattr(response, 'status_code', None)
        detail = ''
        try:
            payload = response.json()
            errors = payload.get('error', {}).get('errors', [])
            if errors:
                reason = errors[0].get('reason', '')
                detail = errors[0].get('message', '')
                if reason in {'quotaExceeded', 'dailyLimitExceeded'}:
                    return f'YouTube API 配额已用尽（{reason}）'
            if not detail:
                detail = payload.get('error', {}).get('message', '')
        except Exception:
            pass
        if code == 429:
            return 'HTTP 429：YouTube 暂时限流'
        if code:
            return f'HTTP {code}: {detail or exc}'[:500]
    return str(exc)[:500]


def _search(query: str, lookback_hours: int, order: str, short_only: bool = False):
    params = {
        'part': 'snippet',
        'q': query,
        'type': 'video',
        'order': order,
        'maxResults': 25,
        'publishedAfter': _published_after(lookback_hours),
        'regionCode': Config.YOUTUBE_REGION,
        'relevanceLanguage': Config.YOUTUBE_LANGUAGE,
        'safeSearch': 'moderate',
        'key': Config.YOUTUBE_API_KEY,
    }
    if short_only:
        params['videoDuration'] = 'short'

    resp = requests.get(SEARCH_URL, params=params, timeout=20)
    resp.raise_for_status()
    return resp.json().get('items', [])


def _stats(video_ids: list[str]):
    if not video_ids:
        return {}, ''
    try:
        resp = requests.get(VIDEOS_URL, params={
            'part': 'statistics',
            'id': ','.join(video_ids),
            'key': Config.YOUTUBE_API_KEY,
        }, timeout=20)
        resp.raise_for_status()
        return {x['id']: x.get('statistics', {}) for x in resp.json().get('items', [])}, ''
    except Exception as exc:
        msg = _error_message(exc)
        print(f'[youtube stats] {msg}')
        return {}, msg


def _build(results, category: str, status_key: str, channel_whitelist: list[str] | None = None):
    candidates = []
    for result in results:
        sn = result.get('snippet', {})
        channel = (sn.get('channelTitle') or '').strip()
        title = (sn.get('title') or '').strip()
        description = (sn.get('description') or '').strip()

        if channel_whitelist and channel.lower() not in channel_whitelist:
            continue
        if not is_nba_relevant(title, description):
            continue

        video_id = result.get('id', {}).get('videoId')
        if video_id:
            candidates.append((video_id, sn))

    stats_map, stats_error = _stats([video_id for video_id, _ in candidates])
    out = []
    for video_id, sn in candidates:
        st = stats_map.get(video_id, {})
        thumbs = sn.get('thumbnails') or {}
        thumb = (thumbs.get('high') or thumbs.get('medium') or thumbs.get('default') or {}).get('url')
        out.append({
            'external_id': f'meme:{video_id}' if category == 'meme' else video_id,
            'source': 'youtube',
            'source_name': 'YouTube',
            'category': category,
            'title': sn.get('title', ''),
            'summary': sn.get('description', ''),
            'url': f'https://www.youtube.com/watch?v={video_id}',
            'author': sn.get('channelTitle', 'YouTube'),
            'published_at': sn.get('publishedAt') or datetime.now(timezone.utc).isoformat(),
            'thumbnail_url': thumb,
            'metrics': {
                'view_count': int(st.get('viewCount', 0) or 0),
                'like_count': int(st.get('likeCount', 0) or 0),
                'comment_count': int(st.get('commentCount', 0) or 0),
            },
            '_status_key': status_key,
        })
    return out, stats_error


def _fetch_report(*, category: str, display_name: str, query: str, lookback_hours: int,
                  order: str, short_only: bool, channel_whitelist=None):
    status_key = f'youtube:{category}'
    attempted_at = datetime.now(timezone.utc).isoformat()

    if not Config.YOUTUBE_API_KEY:
        return [], {
            'source_key': status_key,
            'display_name': display_name,
            'group_name': category,
            'status': 'disabled',
            'attempted_at': attempted_at,
            'fetched_count': 0,
            'raw_count': 0,
            'message': '未配置 YOUTUBE_API_KEY',
        }

    try:
        results = _search(query, lookback_hours, order=order, short_only=short_only)
    except Exception as exc:
        msg = _error_message(exc)
        print(f'[youtube {category} search] {msg}')
        return [], {
            'source_key': status_key,
            'display_name': display_name,
            'group_name': category,
            'status': 'error',
            'attempted_at': attempted_at,
            'fetched_count': 0,
            'raw_count': 0,
            'message': msg,
        }

    items, stats_error = _build(results, category, status_key, channel_whitelist)
    return items, {
        'source_key': status_key,
        'display_name': display_name,
        'group_name': category,
        'status': 'partial' if stats_error else 'success',
        'attempted_at': attempted_at,
        'fetched_count': len(items),
        'raw_count': len(results),
        'message': f'搜索成功，但互动统计读取失败：{stats_error}' if stats_error else '',
    }


def fetch_youtube_news_report():
    return _fetch_report(
        category='news',
        display_name='YouTube 新闻',
        query=Config.YOUTUBE_NEWS_QUERY,
        lookback_hours=Config.YOUTUBE_NEWS_LOOKBACK_HOURS,
        order='date',
        short_only=False,
        channel_whitelist=Config.YOUTUBE_NEWS_CHANNEL_WHITELIST,
    )


def fetch_youtube_memes_report():
    return _fetch_report(
        category='meme',
        display_name='YouTube 梗 / 二创',
        query=Config.YOUTUBE_MEME_QUERY,
        lookback_hours=Config.YOUTUBE_MEME_LOOKBACK_HOURS,
        order='viewCount',
        short_only=True,
        channel_whitelist=None,
    )


def fetch_youtube_news():
    return fetch_youtube_news_report()[0]


def fetch_youtube_memes():
    return fetch_youtube_memes_report()[0]


def fetch_youtube():
    return fetch_youtube_news() + fetch_youtube_memes()

COMMENT_THREADS_URL = 'https://www.googleapis.com/youtube/v3/commentThreads'


def fetch_youtube_meme_comments_report(meme_items: list[dict]):
    """Fetch a small number of high-signal top comments from already discovered meme videos.

    commentThreads.list costs far less quota than search.list, so this adds fan-language/meme
    discovery without performing another expensive YouTube search.
    """
    status_key = 'youtube:comments'
    attempted_at = datetime.now(timezone.utc).isoformat()
    if not Config.YOUTUBE_API_KEY:
        return [], {
            'source_key': status_key,
            'display_name': 'YouTube 高赞评论梗',
            'group_name': 'meme',
            'status': 'disabled',
            'attempted_at': attempted_at,
            'fetched_count': 0,
            'raw_count': 0,
            'message': '未配置 YOUTUBE_API_KEY',
        }

    ranked = sorted(
        meme_items,
        key=lambda x: int((x.get('metrics') or {}).get('view_count', 0) or 0),
        reverse=True,
    )[:Config.YOUTUBE_COMMENT_VIDEOS]

    out = []
    raw_count = 0
    ok_videos = 0
    failures = []
    for video in ranked:
        ext = str(video.get('external_id') or '')
        video_id = ext.split(':', 1)[-1]
        if not video_id:
            continue
        try:
            resp = requests.get(COMMENT_THREADS_URL, params={
                'part': 'snippet',
                'videoId': video_id,
                'maxResults': Config.YOUTUBE_COMMENTS_PER_VIDEO,
                'order': 'relevance',
                'textFormat': 'plainText',
                'key': Config.YOUTUBE_API_KEY,
            }, timeout=20)
            resp.raise_for_status()
            rows = resp.json().get('items', [])
            raw_count += len(rows)
            ok_videos += 1
        except Exception as exc:
            response = getattr(exc, 'response', None)
            code = getattr(response, 'status_code', None) if response is not None else None
            failures.append(f'{video_id}: HTTP {code}' if code else f'{video_id}: {str(exc)[:80]}')
            continue

        for row in rows:
            top = (((row.get('snippet') or {}).get('topLevelComment') or {}).get('snippet') or {})
            comment_id = ((row.get('snippet') or {}).get('topLevelComment') or {}).get('id') or row.get('id')
            text = str(top.get('textDisplay') or top.get('textOriginal') or '').strip()
            if not text or not comment_id:
                continue
            # Comments from NBA meme/reaction videos are trusted context; no need to require NBA words in every joke.
            out.append({
                'external_id': f'comment:{comment_id}',
                'source': 'youtube_comment',
                'source_name': 'YouTube 评论',
                'category': 'meme',
                'title': text[:350],
                'summary': f'来自视频：{video.get("title", "")[:260]}',
                'url': f'https://www.youtube.com/watch?v={video_id}&lc={comment_id}',
                'author': top.get('authorDisplayName') or 'YouTube user',
                'published_at': top.get('publishedAt') or video.get('published_at') or datetime.now(timezone.utc).isoformat(),
                'thumbnail_url': video.get('thumbnail_url'),
                'metrics': {
                    'like_count': int(top.get('likeCount', 0) or 0),
                },
                '_status_key': status_key,
            })

    out.sort(key=lambda x: int((x.get('metrics') or {}).get('like_count', 0) or 0), reverse=True)
    # Keep the feed useful instead of flooding it with low-signal comments.
    out = out[: max(10, Config.YOUTUBE_COMMENT_VIDEOS * Config.YOUTUBE_COMMENTS_PER_VIDEO)]
    if not ranked:
        status = 'success'
        message = '本次没有可用于读取评论的 YouTube 梗视频'
    elif ok_videos == len(ranked):
        status = 'success'
        message = f'读取 {ok_videos} 个视频的高赞评论'
    elif ok_videos:
        status = 'partial'
        message = f'成功读取 {ok_videos}/{len(ranked)} 个视频评论；部分视频关闭评论或请求失败'
    else:
        status = 'error'
        message = '未能读取评论' + (f'：{failures[0]}' if failures else '')

    return out, {
        'source_key': status_key,
        'display_name': 'YouTube 高赞评论梗',
        'group_name': 'meme',
        'status': status,
        'attempted_at': attempted_at,
        'fetched_count': len(out),
        'raw_count': raw_count,
        'message': message,
    }
