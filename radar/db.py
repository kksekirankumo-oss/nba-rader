import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from .config import Config


@contextmanager
def conn():
    c = sqlite3.connect(Config.DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def _columns(c) -> set[str]:
    return {row['name'] for row in c.execute('PRAGMA table_info(items)').fetchall()}


def init_db():
    with conn() as c:
        c.execute('''
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                external_id TEXT NOT NULL,
                source TEXT NOT NULL,
                source_name TEXT,
                category TEXT NOT NULL DEFAULT 'news',
                title TEXT NOT NULL,
                title_zh TEXT,
                summary TEXT,
                summary_zh TEXT,
                url TEXT NOT NULL,
                author TEXT,
                published_at TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                metrics_json TEXT DEFAULT '{}',
                thumbnail_url TEXT,
                favorite INTEGER NOT NULL DEFAULT 0,
                favorite_at TEXT,
                risk_level TEXT NOT NULL DEFAULT 'unreviewed',
                risk_tags_json TEXT NOT NULL DEFAULT '[]',
                risk_notes TEXT,
                UNIQUE(source, external_id)
            )
        ''')

        cols = _columns(c)
        if 'category' not in cols:
            c.execute("ALTER TABLE items ADD COLUMN category TEXT NOT NULL DEFAULT 'news'")
            c.execute("UPDATE items SET category='news' WHERE category IS NULL OR category='' ")
            cols.add('category')

        migrations = [
            ('favorite', "ALTER TABLE items ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0"),
            ('favorite_at', "ALTER TABLE items ADD COLUMN favorite_at TEXT"),
            ('risk_level', "ALTER TABLE items ADD COLUMN risk_level TEXT NOT NULL DEFAULT 'unreviewed'"),
            ('risk_tags_json', "ALTER TABLE items ADD COLUMN risk_tags_json TEXT NOT NULL DEFAULT '[]'"),
            ('risk_notes', "ALTER TABLE items ADD COLUMN risk_notes TEXT"),
        ]
        for column, sql in migrations:
            if column not in cols:
                c.execute(sql)
                cols.add(column)

        # v0.4: persist the last result of every external source fetch.
        c.execute('''
            CREATE TABLE IF NOT EXISTS source_status (
                source_key TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                group_name TEXT NOT NULL,
                status TEXT NOT NULL,
                attempted_at TEXT NOT NULL,
                fetched_count INTEGER NOT NULL DEFAULT 0,
                raw_count INTEGER NOT NULL DEFAULT 0,
                inserted_count INTEGER NOT NULL DEFAULT 0,
                message TEXT NOT NULL DEFAULT '',
                cooldown_until TEXT
            )
        ''')

        status_cols = {row['name'] for row in c.execute('PRAGMA table_info(source_status)').fetchall()}
        if 'cooldown_until' not in status_cols:
            c.execute("ALTER TABLE source_status ADD COLUMN cooldown_until TEXT")

        # v0.6: lightweight history used only for local frequency/quota warnings.
        c.execute('''
            CREATE TABLE IF NOT EXISTS fetch_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_key TEXT NOT NULL,
                status TEXT NOT NULL,
                attempted_at TEXT NOT NULL,
                fetched_count INTEGER NOT NULL DEFAULT 0,
                inserted_count INTEGER NOT NULL DEFAULT 0,
                message TEXT NOT NULL DEFAULT ''
            )
        ''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_fetch_history_source_time ON fetch_history(source_key, attempted_at DESC)')

        c.execute('CREATE INDEX IF NOT EXISTS idx_items_published ON items(published_at DESC)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_items_source ON items(source)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_items_category ON items(category)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_items_favorite ON items(favorite, favorite_at DESC)')


def item_exists(source: str, external_id: str) -> bool:
    with conn() as c:
        row = c.execute(
            'SELECT 1 FROM items WHERE source=? AND external_id=? LIMIT 1',
            (source, external_id)
        ).fetchone()
    return row is not None


def upsert_item(item: dict) -> bool:
    with conn() as c:
        cur = c.execute('''
            INSERT INTO items (
                external_id, source, source_name, category, title, title_zh,
                summary, summary_zh, url, author, published_at,
                fetched_at, metrics_json, thumbnail_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, external_id) DO UPDATE SET
                source_name=excluded.source_name,
                category=excluded.category,
                title=excluded.title,
                summary=excluded.summary,
                url=excluded.url,
                author=excluded.author,
                published_at=excluded.published_at,
                fetched_at=excluded.fetched_at,
                metrics_json=excluded.metrics_json,
                thumbnail_url=COALESCE(excluded.thumbnail_url, items.thumbnail_url),
                title_zh=COALESCE(items.title_zh, excluded.title_zh),
                summary_zh=COALESCE(items.summary_zh, excluded.summary_zh)
        ''', (
            item['external_id'], item['source'], item.get('source_name'),
            item.get('category', 'news'), item['title'], item.get('title_zh'),
            item.get('summary'), item.get('summary_zh'), item['url'], item.get('author'),
            item['published_at'], item.get('fetched_at') or datetime.now(timezone.utc).isoformat(),
            json.dumps(item.get('metrics', {}), ensure_ascii=False),
            item.get('thumbnail_url'),
        ))
        return cur.rowcount > 0


def record_source_status(report: dict):
    with conn() as c:
        c.execute('''
            INSERT INTO source_status (
                source_key, display_name, group_name, status, attempted_at,
                fetched_count, raw_count, inserted_count, message, cooldown_until
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_key) DO UPDATE SET
                display_name=excluded.display_name,
                group_name=excluded.group_name,
                status=excluded.status,
                attempted_at=excluded.attempted_at,
                fetched_count=excluded.fetched_count,
                raw_count=excluded.raw_count,
                inserted_count=excluded.inserted_count,
                message=excluded.message,
                cooldown_until=excluded.cooldown_until
        ''', (
            report['source_key'], report['display_name'], report['group_name'],
            report['status'], report['attempted_at'], int(report.get('fetched_count', 0) or 0),
            int(report.get('raw_count', 0) or 0), int(report.get('inserted_count', 0) or 0),
            str(report.get('message', '') or '')[:500], report.get('cooldown_until'),
        ))

        # Only actual external attempts enter history. Local skips/cooldowns/disabled sources do not.
        if report.get('status') not in {'cooldown', 'guarded', 'disabled'}:
            c.execute('''
                INSERT INTO fetch_history (
                    source_key, status, attempted_at, fetched_count, inserted_count, message
                ) VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                report['source_key'], report['status'], report['attempted_at'],
                int(report.get('fetched_count', 0) or 0),
                int(report.get('inserted_count', 0) or 0),
                str(report.get('message', '') or '')[:500],
            ))
            c.execute("DELETE FROM fetch_history WHERE datetime(attempted_at) < datetime('now', '-30 day')")


def list_source_statuses():
    with conn() as c:
        rows = c.execute('''
            SELECT source_key, display_name, group_name, status, attempted_at,
                   fetched_count, raw_count, inserted_count, message, cooldown_until
            FROM source_status
            ORDER BY CASE group_name WHEN 'news' THEN 0 WHEN 'meme' THEN 1 ELSE 2 END,
                     display_name COLLATE NOCASE
        ''').fetchall()
    return [dict(row) for row in rows]


def get_last_actual_attempt(source_prefix: str):
    # Latest actual external attempt for a source prefix, as a UTC datetime.
    with conn() as c:
        row = c.execute(
            "SELECT MAX(attempted_at) AS attempted_at FROM fetch_history WHERE source_key LIKE ?",
            (f'{source_prefix}%',)
        ).fetchone()
        value = row['attempted_at'] if row else None
        # v0.5 databases have no history yet; fall back to current status when it represents a real attempt.
        if not value:
            row = c.execute('''
                SELECT MAX(attempted_at) AS attempted_at FROM source_status
                WHERE source_key LIKE ? AND status NOT IN ('cooldown','guarded','disabled')
            ''', (f'{source_prefix}%',)).fetchone()
            value = row['attempted_at'] if row else None
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def count_actual_attempts(source_prefix: str, hours: int = 24) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with conn() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM fetch_history WHERE source_key LIKE ? AND attempted_at >= ?",
            (f'{source_prefix}%', cutoff)
        ).fetchone()
    return int(row['n'] if row else 0)


def get_reddit_cooldown_until():
    """Return the furthest persisted Reddit cooldown, if it is still active."""
    with conn() as c:
        row = c.execute(
            "SELECT MAX(cooldown_until) AS until_at FROM source_status "
            "WHERE source_key LIKE 'reddit:%' AND cooldown_until IS NOT NULL"
        ).fetchone()
    value = row['until_at'] if row else None
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
        return dt if dt > datetime.now(timezone.utc) else None
    except Exception:
        return None


def _row_to_dict(row):
    d = dict(row)
    try:
        d['metrics'] = json.loads(d.pop('metrics_json') or '{}')
    except json.JSONDecodeError:
        d['metrics'] = {}
    try:
        d['risk_tags'] = json.loads(d.pop('risk_tags_json') or '[]')
    except json.JSONDecodeError:
        d['risk_tags'] = []
    d['favorite'] = bool(d.get('favorite'))
    return d


def _heat_score(item: dict) -> int:
    m = item.get('metrics', {})
    return (
        int(m.get('view_count', 0) or 0)
        + int(m.get('like_count', 0) or 0) * 20
        + int(m.get('comment_count', 0) or 0) * 50
        + int(m.get('score', 0) or 0) * 100
    )


def list_items(
    hours: int = 24,
    category: str = 'news',
    source: str = 'all',
    sort: str = 'latest',
    limit: int = 250,
):
    where = []
    args = []

    if category == 'favorite':
        where.append('favorite = 1')
    else:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        where.append('published_at >= ?')
        args.append(cutoff)
        if category in {'news', 'meme'}:
            where.append('category = ?')
            args.append(category)

    if source != 'all':
        where.append('source = ?')
        args.append(source)

    if not where:
        where.append('1=1')

    order_col = 'COALESCE(favorite_at, published_at)' if category == 'favorite' else 'published_at'
    with conn() as c:
        rows = c.execute(
            f'SELECT * FROM items WHERE {" AND ".join(where)} ORDER BY {order_col} DESC LIMIT ?',
            (*args, limit)
        ).fetchall()

    out = [_row_to_dict(r) for r in rows]
    if sort in {'views', 'hot'}:
        out.sort(key=_heat_score, reverse=True)
    return out


def update_review(item_id: int, *, favorite=None, risk_level=None, risk_tags=None, risk_notes=None):
    allowed_levels = {'unreviewed', 'green', 'yellow', 'red', 'group_check'}
    updates = []
    args = []

    if favorite is not None:
        value = 1 if bool(favorite) else 0
        updates.append('favorite = ?')
        args.append(value)
        updates.append('favorite_at = ?')
        args.append(datetime.now(timezone.utc).isoformat() if value else None)

    if risk_level is not None:
        if risk_level not in allowed_levels:
            raise ValueError('invalid risk level')
        updates.append('risk_level = ?')
        args.append(risk_level)

    if risk_tags is not None:
        if not isinstance(risk_tags, list):
            raise ValueError('risk_tags must be a list')
        clean_tags = [str(x).strip()[:30] for x in risk_tags if str(x).strip()][:12]
        updates.append('risk_tags_json = ?')
        args.append(json.dumps(clean_tags, ensure_ascii=False))

    if risk_notes is not None:
        updates.append('risk_notes = ?')
        args.append(str(risk_notes)[:2000])

    if not updates:
        raise ValueError('nothing to update')

    args.append(item_id)
    with conn() as c:
        cur = c.execute(f'UPDATE items SET {", ".join(updates)} WHERE id = ?', args)
        if cur.rowcount == 0:
            return None
        row = c.execute('SELECT * FROM items WHERE id = ?', (item_id,)).fetchone()
    return _row_to_dict(row)


def stats():
    with conn() as c:
        row = c.execute('''
            SELECT
                COUNT(*) AS n,
                SUM(CASE WHEN category='news' THEN 1 ELSE 0 END) AS news_n,
                SUM(CASE WHEN category='meme' THEN 1 ELSE 0 END) AS meme_n,
                SUM(CASE WHEN favorite=1 THEN 1 ELSE 0 END) AS favorite_n,
                MAX(fetched_at) AS last_fetch
            FROM items
        ''').fetchone()
        attempt = c.execute('SELECT MAX(attempted_at) AS last_attempt FROM source_status').fetchone()
    data = dict(row)
    data['last_attempt'] = attempt['last_attempt'] if attempt else None
    return data
