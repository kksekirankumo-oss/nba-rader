import html
from datetime import datetime, timedelta, timezone

import requests

from ..config import Config
from ..db import get_reddit_cooldown_until
from ..nba_filter import is_nba_relevant

TOKEN_URL = 'https://www.reddit.com/api/v1/access_token'
API_BASE = 'https://oauth.reddit.com'

_token_cache = {'access_token': None, 'expires_at': None}


def reddit_enabled() -> bool:
    return bool(Config.REDDIT_CLIENT_ID and Config.REDDIT_CLIENT_SECRET and Config.REDDIT_USER_AGENT)


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_epoch(value) -> str:
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


def _retry_after_seconds(response) -> int:
    raw = (response.headers.get('Retry-After') or '').strip()
    if raw.isdigit():
        return max(0, int(raw))
    return 0


def _cooldown_time(response=None) -> datetime:
    minimum = max(1, Config.REDDIT_COOLDOWN_MINUTES) * 60
    server_wait = _retry_after_seconds(response) if response is not None else 0
    reset_hint = 0
    if response is not None:
        try:
            remaining = float(response.headers.get('x-ratelimit-remaining', '0') or 0)
            reset_hint = int(float(response.headers.get('x-ratelimit-reset', '0') or 0)) if remaining <= 0 else 0
        except Exception:
            reset_hint = 0
    return datetime.now(timezone.utc) + timedelta(seconds=max(minimum, server_wait, reset_hint))


def _cooldown_reports(until: datetime, message: str | None = None):
    attempted_at = datetime.now(timezone.utc).isoformat()
    until_iso = until.astimezone(timezone.utc).isoformat()
    local_hint = until.astimezone().strftime('%H:%M')
    msg = message or f'此前收到 Reddit 限流响应；冷却至约 {local_hint}，本次未向 Reddit 发请求'
    return [{
        'source_key': f'reddit:{subreddit.lower()}',
        'display_name': f'Reddit r/{subreddit}',
        'group_name': 'meme',
        'status': 'cooldown',
        'attempted_at': attempted_at,
        'fetched_count': 0,
        'raw_count': 0,
        'message': msg,
        'cooldown_until': until_iso,
    } for subreddit in Config.REDDIT_MEME_SUBREDDITS]


def _disabled_reports(reason: str):
    attempted_at = datetime.now(timezone.utc).isoformat()
    return [{
        'source_key': f'reddit:{subreddit.lower()}',
        'display_name': f'Reddit r/{subreddit}',
        'group_name': 'meme',
        'status': 'disabled',
        'attempted_at': attempted_at,
        'fetched_count': 0,
        'raw_count': 0,
        'message': reason,
        'cooldown_until': None,
    } for subreddit in Config.REDDIT_MEME_SUBREDDITS]


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({'User-Agent': Config.REDDIT_USER_AGENT, 'Accept': 'application/json'})
    return s


def _auth_headers() -> tuple[str | None, dict | None, str | None]:
    if not reddit_enabled():
        return None, None, '未配置 Reddit OAuth（需要 REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET / REDDIT_USER_AGENT）'

    now = datetime.now(timezone.utc)
    expires_at = _token_cache.get('expires_at')
    access_token = _token_cache.get('access_token')
    if access_token and expires_at and expires_at > now + timedelta(seconds=30):
        return access_token, {'Authorization': f'Bearer {access_token}'}, None

    sess = _session()
    try:
        resp = sess.post(
            TOKEN_URL,
            auth=(Config.REDDIT_CLIENT_ID, Config.REDDIT_CLIENT_SECRET),
            data={'grant_type': 'client_credentials'},
            timeout=20,
        )
        if resp.status_code == 429:
            until = _cooldown_time(resp)
            return None, {'cooldown_until': until.isoformat()}, 'HTTP 429：Reddit OAuth 取 token 被限流'
        resp.raise_for_status()
        data = resp.json()
        token = data.get('access_token')
        expires_in = int(data.get('expires_in', 3600) or 3600)
        if not token:
            return None, None, 'Reddit OAuth 未返回 access_token'
        _token_cache['access_token'] = token
        _token_cache['expires_at'] = now + timedelta(seconds=max(60, expires_in - 60))
        return token, {'Authorization': f'Bearer {token}'}, None
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else None
        if code == 401:
            return None, None, 'HTTP 401：Reddit OAuth 认证失败，请检查 client id / secret'
        if code == 403:
            return None, None, 'HTTP 403：Reddit 拒绝了 OAuth 请求，请检查 app 类型与权限'
        return None, None, f'HTTP {code}: Reddit OAuth 取 token 失败' if code else str(exc)
    except Exception as exc:
        return None, None, str(exc)[:500]


def _thumbnail(post: dict) -> str | None:
    preview = post.get('preview') or {}
    images = preview.get('images') or []
    if images:
        src = images[0].get('source') or {}
        url = src.get('url')
        if url:
            return html.unescape(url)
    thumb = (post.get('thumbnail') or '').strip()
    if thumb.startswith('http'):
        return thumb
    media = post.get('media_metadata') or {}
    for meta in media.values():
        s = meta.get('s') or {}
        u = s.get('u')
        if u:
            return html.unescape(u)
    return None


def _summary(post: dict) -> str:
    text = (post.get('selftext') or '').strip()
    if text:
        return text[:600]
    flair = (post.get('link_flair_text') or '').strip()
    domain = (post.get('domain') or '').strip()
    bits = [x for x in [flair, domain] if x]
    return ' · '.join(bits)[:600]


def _ratelimit_message(headers) -> str:
    remain = headers.get('x-ratelimit-remaining')
    used = headers.get('x-ratelimit-used')
    reset = headers.get('x-ratelimit-reset')
    pieces = []
    if remain is not None:
        pieces.append(f'剩余 {remain}')
    if used is not None:
        pieces.append(f'已用 {used}')
    if reset is not None:
        try:
            pieces.append(f'约 {int(float(reset))} 秒后重置')
        except Exception:
            pieces.append(f'重置 {reset}')
    return ' · '.join(pieces)


def fetch_reddit_memes_report():
    saved_cooldown = get_reddit_cooldown_until()
    now = datetime.now(timezone.utc)
    if saved_cooldown and saved_cooldown > now:
        return [], _cooldown_reports(saved_cooldown)

    token, auth_meta, auth_error = _auth_headers()
    if auth_error:
        if isinstance(auth_meta, dict) and auth_meta.get('cooldown_until'):
            return [], _cooldown_reports(datetime.fromisoformat(auth_meta['cooldown_until']), auth_error)
        return [], _disabled_reports(auth_error)

    session = _session()
    session.headers.update(auth_meta)

    all_items = []
    reports = []
    hit_429_until = None
    processed = set()

    for subreddit in Config.REDDIT_MEME_SUBREDDITS:
        if hit_429_until:
            break

        attempted_at = datetime.now(timezone.utc).isoformat()
        status_key = f'reddit:{subreddit.lower()}'
        raw_count = 0
        url = f'{API_BASE}/r/{subreddit}/new'
        params = {'limit': 50, 'raw_json': 1}

        try:
            resp = session.get(url, params=params, timeout=20)
            if resp.status_code == 429:
                hit_429_until = _cooldown_time(resp)
                msg = 'HTTP 429：Reddit 暂时限流'
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
            if resp.status_code == 401:
                reports.append({
                    'source_key': status_key,
                    'display_name': f'Reddit r/{subreddit}',
                    'group_name': 'meme',
                    'status': 'error',
                    'attempted_at': attempted_at,
                    'fetched_count': 0,
                    'raw_count': 0,
                    'message': 'HTTP 401：Reddit token 失效或认证失败',
                    'cooldown_until': None,
                })
                processed.add(subreddit.lower())
                continue
            resp.raise_for_status()
            data = resp.json().get('data') or {}
            children = data.get('children') or []
            raw_count = len(children)
        except Exception as exc:
            msg = str(exc)[:500]
            if isinstance(exc, requests.HTTPError) and exc.response is not None:
                code = exc.response.status_code
                msg = f'HTTP {code}: Reddit API 请求失败'
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

        unique_items = {}
        for child in children[:50]:
            post = child.get('data') or {}
            title = html.unescape((post.get('title') or '').strip())
            url_out = (post.get('url_overridden_by_dest') or post.get('url') or '').strip()
            permalink = post.get('permalink') or ''
            reddit_link = f'https://www.reddit.com{permalink}' if permalink else ''
            if not title or not reddit_link:
                continue
            summary = _summary(post)
            if not is_nba_relevant(title, summary, trusted_nba_feed=True):
                continue
            ext = str(post.get('id') or permalink or reddit_link)
            item = {
                'external_id': ext,
                'source': 'reddit',
                'source_name': f'Reddit r/{subreddit}',
                'category': 'meme',
                'title': title,
                'summary': summary,
                'url': reddit_link,
                'author': post.get('author') or f'r/{subreddit}',
                'published_at': _parse_epoch(post.get('created_utc')),
                'thumbnail_url': _thumbnail(post),
                'metrics': {
                    'score': int(post.get('score') or 0),
                    'comment_count': int(post.get('num_comments') or 0),
                },
                '_status_key': status_key,
            }
            # keep reddit permalink as primary, but mention outbound target in summary when helpful
            if url_out and url_out != reddit_link and not item['summary']:
                item['summary'] = url_out[:600]
            unique_items[ext] = item

        msg = _ratelimit_message(resp.headers)
        try:
            remaining = float(resp.headers.get('x-ratelimit-remaining', '999') or 999)
            reset = int(float(resp.headers.get('x-ratelimit-reset', '0') or 0))
            if remaining <= Config.REDDIT_WARN_REMAINING:
                msg = (msg + '；接近 Reddit 速率上限，请稍后再抓').strip('；')
                pass
        except Exception:
            pass

        all_items.extend(unique_items.values())
        reports.append({
            'source_key': status_key,
            'display_name': f'Reddit r/{subreddit}',
            'group_name': 'meme',
            'status': 'success',
            'attempted_at': attempted_at,
            'fetched_count': len(unique_items),
            'raw_count': raw_count,
            'message': msg,
            'cooldown_until': None,
        })
        processed.add(subreddit.lower())

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
                'message': f'其他 Reddit OAuth 请求已收到 429；冷却至约 {local_hint}，本次未请求',
                'cooldown_until': until_iso,
            })

    return all_items, reports


def fetch_reddit_memes():
    return fetch_reddit_memes_report()[0]
