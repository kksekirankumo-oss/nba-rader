from datetime import datetime, timezone

from flask import Flask, jsonify, render_template, request

from radar.config import Config
from radar.db import init_db, list_items, list_source_statuses, stats, update_review
from radar.service import refresh_all, refresh_check, translator

app = Flask(__name__)
init_db()


@app.get('/')
def index():
    return render_template('index.html', translation_enabled=translator.enabled)


@app.get('/api/items')
def api_items():
    hours = request.args.get('hours', Config.DEFAULT_HOURS, type=int)
    category = request.args.get('category', 'news')
    source = request.args.get('source', 'all')
    sort = request.args.get('sort', 'latest')
    items = list_items(
        hours=max(1, min(hours, 168)),
        category=category,
        source=source,
        sort=sort,
    )
    return jsonify({
        'items': items,
        'stats': stats(),
        'source_statuses': list_source_statuses(),
        'translation_enabled': translator.enabled,
        'youtube_enabled': bool(Config.YOUTUBE_API_KEY),
        'manual_only': True,
    })


@app.patch('/api/items/<int:item_id>/review')
def api_review(item_id):
    payload = request.get_json(silent=True) or {}
    try:
        item = update_review(
            item_id,
            favorite=payload.get('favorite') if 'favorite' in payload else None,
            risk_level=payload.get('risk_level') if 'risk_level' in payload else None,
            risk_tags=payload.get('risk_tags') if 'risk_tags' in payload else None,
            risk_notes=payload.get('risk_notes') if 'risk_notes' in payload else None,
        )
    except ValueError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400
    if item is None:
        return jsonify({'ok': False, 'error': 'item not found'}), 404
    return jsonify({'ok': True, 'item': item})


@app.get('/api/refresh-check')
def api_refresh_check():
    return jsonify({'ok': True, **refresh_check()})


@app.post('/api/refresh')
def api_refresh():
    payload = request.get_json(silent=True) or {}
    result = refresh_all(
        force_reddit=bool(payload.get('force_reddit', False)),
        skip_reddit=bool(payload.get('skip_reddit', False)),
        skip_youtube=bool(payload.get('skip_youtube', False)),
    )
    return jsonify({
        'ok': True,
        'result': result,
        'source_statuses': list_source_statuses(),
        'at': datetime.now(timezone.utc).isoformat(),
    })


if __name__ == '__main__':
    print('NBA Radar v0.7 starting...')
    print('Refresh mode: one startup refresh, then manual only (no background scheduler)')
    print(f'Translation: {"DeepL enabled" if translator.enabled else "disabled (set DEEPL_API_KEY to enable)"}')
    print(f'Reddit memes: {"OAuth enabled" if (Config.REDDIT_CLIENT_ID and Config.REDDIT_CLIENT_SECRET) else "OAuth not configured"}, local safety warning < {Config.REDDIT_SAFE_INTERVAL_MINUTES} min, 429 cooldown {Config.REDDIT_COOLDOWN_MINUTES} min')
    print(f'YouTube: {"enabled" if Config.YOUTUBE_API_KEY else "disabled"}')
    try:
        print('Initial refresh:', refresh_all())
    except Exception as exc:
        print('Initial refresh failed:', exc)
    app.run(host='127.0.0.1', port=Config.PORT, debug=False, use_reloader=False)
