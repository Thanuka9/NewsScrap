"""Separate publication, first discovery, collection and prior-send evidence."""
from datetime import datetime
import json
from zoneinfo import ZoneInfo
from .identity import canonical, publisher_article_id


def load_collection_metadata(root, date_strings=()):
    result = {}
    for path in sorted((root / 'data' / 'state').glob('*.json')):
        obj = json.loads(path.read_text(encoding='utf-8-sig'))
        for url, entry in (obj.get('articles') or {}).items():
            if not isinstance(entry, dict):continue
            result[canonical(url)] = {
                'published_at': entry.get('published_at', ''),
                'first_seen_at': entry.get('first_seen_at', ''),
                'collected_at': entry.get('last_fetched_at', ''),
                'collection_evidence': str(path),
            }
    # Generic collectors persist immutable versions rather than state entries.
    # v001 establishes first discovery; later versions must not reset that clock.
    for source in (root/'data/staged').glob('*'):
        if not source.is_dir():continue
        for day in set(date_strings):
            for path in (source/day).glob('*/v001.json'):
                entry=json.loads(path.read_text(encoding='utf-8-sig'))
                key=canonical(entry.get('source_url',''))
                if not key or key in result:continue
                result[key]={'published_at':entry.get('published_at',''),
                    'first_seen_at':entry.get('first_seen_at') or entry.get('collected_at',''),
                    'collected_at':entry.get('collected_at',''),'collection_evidence':str(path)}
    # Publisher URL aliases (Daily Mirror /business/ vs /business-news/) are one
    # physical article: discovering any alias discovers the article, so a later
    # alias fetch must not make an already-seen story look like late news.
    earliest = {}
    for key, entry in result.items():
        pid = publisher_article_id(key);first = aware(entry['first_seen_at'])
        if pid and first and (pid not in earliest or first < earliest[pid][0]):
            earliest[pid] = (first, entry['first_seen_at'], entry['collection_evidence'])
    for key, entry in result.items():
        pid = publisher_article_id(key)
        if pid in earliest and earliest[pid][1] != entry['first_seen_at']:
            entry['first_seen_at'] = earliest[pid][1]
            entry['first_seen_evidence'] = earliest[pid][2]
    return result


def aware(value):
    try:
        d = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return d if d.tzinfo else None
    except ValueError:return None


def late_status(article, target, prior):
    if article['publication_date'][:10] == target.isoformat():return 'TODAY'
    first = aware(article.get('first_seen_at'))
    published = aware(article.get('published_at'))
    if not first:return 'MISSING_FIRST_DISCOVERY_EVIDENCE'
    # A late discovery is valid even when a publisher exposes only a date.
    if first.astimezone(ZoneInfo('Asia/Colombo')).date() == target:return 'LATE_DISCOVERY'
    # The cutoff is the most recent sent edition's own send time; an older
    # edition's time would admit stories the latest edition may already carry.
    dated = [p for p in prior if p.get('edition_date') and p['edition_date'] < target.isoformat()]
    if dated:
        latest = max(p['edition_date'] for p in dated)
        times = [aware(p.get('sent_at')) for p in dated if p['edition_date'] == latest]
        times = [d for d in times if d]
        if not times:
            # Name the missing evidence; never assume a send time.
            return 'PREVIOUS_SEND_TIME_MISSING:' + latest
        cutoffs = times
    else:
        cutoffs = [aware(p.get('sent_at')) for p in prior]
        cutoffs = [d for d in cutoffs if d and d.astimezone(ZoneInfo('Asia/Colombo')).date() < target]
    if cutoffs and published and published > max(cutoffs) and first >= published:
        return 'PUBLISHED_AFTER_PREVIOUS_SEND'
    # Collected and published before the previous edition went out: that
    # edition already had it and left it out. Not late news.
    if cutoffs and published and published <= max(cutoffs) and first <= max(cutoffs):
        return 'AVAILABLE_BEFORE_PREVIOUS_SEND'
    return 'LATE_NEWS_NOT_ESTABLISHED'
