from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .config import Config
from .db import (
    count_actual_attempts,
    get_last_actual_attempt,
    get_reddit_cooldown_until,
    item_exists,
    list_source_statuses,
    record_source_status,
    upsert_item,
)
from .translator import Translator
from .fetchers.rss import fetch_rss_report
from .fetchers.reddit_oauth import fetch_reddit_memes_report, reddit_enabled
from .fetchers.youtube import fetch_youtube_memes_report, fetch_youtube_news_report

translator = Translator()


def _save(items):
    inserted = 0
    inserted_by_key = defaultdict(int)
    for item in items:
        item['fetched_at'] = datetime.now(timezone.utc).isoformat()
        exists = item_exists(item['source'], item['external_id'])
        if translator.enabled and not exists:
            item['title_zh'] = translator.translate(item.get('title', ''))
            item['summary_zh'] = translator.translate(item.get('summary', ''))
        upsert_item(item)
        if not exists:
            inserted += 1
            inserted_by_key[item.get('_status_key', item.get('source_name', item['source']))] += 1
    return inserted, dict(inserted_by_key)


def _finish(items, reports):
    inserted, inserted_by_key = _save(items)
    for report in reports:
        report['inserted_count'] = inserted_by_key.get(report['source_key'], 0)
        record_source_status(report)
    return inserted, reports


def _skip_reports(kind: str, message: str):
    now = datetime.now(timezone.utc).isoformat()
    if kind == 'reddit':
        return [{
            'source_key': f'reddit:{subreddit.lower()}',
            'display_name': f'Reddit r/{subreddit}',
            'group_name': 'meme',
            'status': 'guarded',
            'attempted_at': now,
            'fetched_count': 0,
            'raw_count': 0,
            'inserted_count': 0,
            'message': message,
            'cooldown_until': None,
        } for subreddit in Config.REDDIT_MEME_SUBREDDITS]
    if kind == 'youtube':
        return [{
            'source_key': 'youtube:news',
            'display_name': 'YouTube 新闻',
            'group_name': 'news',
            'status': 'guarded',
            'attempted_at': now,
            'fetched_count': 0,
            'raw_count': 0,
            'inserted_count': 0,
            'message': message,
            'cooldown_until': None,
        }, {
            'source_key': 'youtube:meme',
            'display_name': 'YouTube 梗 / 二创',
            'group_name': 'meme',
            'status': 'guarded',
            'attempted_at': now,
            'fetched_count': 0,
            'raw_count': 0,
            'inserted_count': 0,
            'message': message,
            'cooldown_until': None,
        }]
    return []


def refresh_rss():
    items, reports = fetch_rss_report()
    inserted, reports = _finish(items, reports)
    return {'news_rss': inserted, 'sources': reports}


def reddit_safety_state():
    now = datetime.now(timezone.utc)
    cooldown = get_reddit_cooldown_until()
    last = get_last_actual_attempt('reddit:')
    wait_seconds = 0
    if last:
        safe_at = last + timedelta(minutes=Config.REDDIT_SAFE_INTERVAL_MINUTES)
        wait_seconds = max(0, int((safe_at - now).total_seconds()))

    low_remaining_message = None
    try:
        rows = list_source_statuses()
        reddit_rows = [r for r in rows if str(r.get('source_key', '')).startswith('reddit:')]
        low_rows = [r for r in reddit_rows if '接近 Reddit 速率上限' in str(r.get('message', ''))]
        if low_rows:
            low_rows.sort(key=lambda x: x.get('attempted_at') or '', reverse=True)
            low_remaining_message = low_rows[0].get('message')
    except Exception:
        pass

    return {
        'enabled': reddit_enabled(),
        'cooldown_until': cooldown.isoformat() if cooldown else None,
        'last_actual_attempt': last.isoformat() if last else None,
        'wait_seconds': wait_seconds,
        'safe_interval_minutes': Config.REDDIT_SAFE_INTERVAL_MINUTES,
        'low_remaining_message': low_remaining_message,
    }


def refresh_reddit(*, force=False, skip=False):
    if skip:
        reports = _skip_reports('reddit', '本次手动抓取选择跳过 Reddit，以减少短时间重复请求')
        for report in reports:
            record_source_status(report)
        return {'reddit_memes': 0, 'sources': reports}

    safety = reddit_safety_state()
    if safety['wait_seconds'] > 0 and not force and not safety['cooldown_until']:
        wait_min = max(1, (safety['wait_seconds'] + 59) // 60)
        reports = _skip_reports(
            'reddit',
            f'本地安全保护：距离上次实际 Reddit 请求过近，建议约 {wait_min} 分钟后再抓；本次未发请求'
        )
        for report in reports:
            record_source_status(report)
        return {'reddit_memes': 0, 'sources': reports}

    items, reports = fetch_reddit_memes_report()
    inserted, reports = _finish(items, reports)
    return {'reddit_memes': inserted, 'sources': reports}


def refresh_youtube(*, skip=False):
    if skip:
        reports = _skip_reports('youtube', '本次手动抓取选择跳过 YouTube，以保留 API 搜索配额')
        for report in reports:
            record_source_status(report)
        return {'youtube_news': 0, 'youtube_memes': 0, 'youtube_total': 0, 'sources': reports}

    news_items, news_report = fetch_youtube_news_report()
    meme_items, meme_report = fetch_youtube_memes_report()
    inserted, reports = _finish(news_items + meme_items, [news_report, meme_report])
    by_key = {r['source_key']: r.get('inserted_count', 0) for r in reports}
    return {
        'youtube_news': by_key.get('youtube:news', 0),
        'youtube_memes': by_key.get('youtube:meme', 0),
        'youtube_total': inserted,
        'sources': reports,
    }


def refresh_check():
    warnings = []
    reddit = reddit_safety_state()
    if not reddit['enabled']:
        warnings.append({
            'kind': 'reddit_not_configured',
            'message': 'Reddit OAuth 尚未配置；当前点抓取时 Reddit 来源会显示未启用。',
            'skip_recommended': False,
        })
    elif reddit['cooldown_until']:
        until = datetime.fromisoformat(reddit['cooldown_until']).astimezone()
        warnings.append({
            'kind': 'reddit_cooldown',
            'message': f'Reddit 正在冷却中（约到 {until.strftime("%H:%M")}）。继续抓取时 Reddit 会自动跳过，不会再次请求。',
            'skip_recommended': True,
        })
    elif reddit['wait_seconds'] > 0:
        wait_min = max(1, (reddit['wait_seconds'] + 59) // 60)
        warnings.append({
            'kind': 'reddit_too_soon',
            'message': f'Reddit 距离上次实际请求还不到本地保守安全间隔，建议再等约 {wait_min} 分钟。',
            'skip_recommended': True,
        })

    if reddit.get('low_remaining_message'):
        warnings.append({
            'kind': 'reddit_near_limit',
            'message': f'Reddit 上次返回的速率信息提示：{reddit["low_remaining_message"]}',
            'skip_recommended': True,
        })

    youtube_calls = count_actual_attempts('youtube:', hours=24) if Config.YOUTUBE_API_KEY else 0
    if Config.YOUTUBE_API_KEY and youtube_calls >= Config.YOUTUBE_DAILY_WARN_CALLS:
        warnings.append({
            'kind': 'youtube_quota',
            'message': f'本机过去 24 小时已记录约 {youtube_calls} 次 YouTube 搜索来源调用，继续频繁抓取会更接近每日 API 配额。',
            'skip_recommended': True,
        })

    return {
        'warnings': warnings,
        'reddit': reddit,
        'youtube_search_calls_24h': youtube_calls,
        'youtube_warn_at': Config.YOUTUBE_DAILY_WARN_CALLS,
    }


def refresh_all(*, force_reddit=False, skip_reddit=False, skip_youtube=False):
    rss = refresh_rss()
    reddit = refresh_reddit(force=force_reddit, skip=skip_reddit)
    youtube = refresh_youtube(skip=skip_youtube)
    sources = rss.pop('sources') + reddit.pop('sources') + youtube.pop('sources')
    inserted = {}
    inserted.update(rss)
    inserted.update(reddit)
    inserted.update(youtube)
    return {'inserted': inserted, 'sources': sources}
