"""Authoritative *sent*, not generated, Financial Brief publication history.

PUBLISHED_URLS.json is created only after a report was actually distributed.
QA-passed HTML or report manifests are never publication evidence. Reading is
side-effect free. An invalid explicit publication record fails closed.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import json
import re
from urllib.parse import urlsplit, urlunsplit


def canonical(url: str) -> str:
    p = urlsplit(str(url or '').strip())
    host = p.netloc.casefold().removeprefix('www.')
    return urlunsplit(('https', host, p.path.rstrip('/') or '/', '', '')) if host else ''


def earliest_eligible_day(day: date, *, days: int = 1) -> date:
    """Daily: prior day; Monday: Fri-Sun weekend catch-up."""
    if days != 1:
        return day - timedelta(days=days - 1)
    return day - timedelta(days=3 if day.weekday() == 0 else 1)


def published_editions(report_root: Path, before: date, *, max_days: int = 400) -> list[dict]:
    """Return immutable sent-manifest events across prior publication dates.

    Manifest `events` come from the actual HTML emailed, even if a later
    generated audit/HTML in the project folder differs.
    """
    root = Path(report_root)
    output = []
    if not root.is_dir():
        return output
    for folder in sorted(root.iterdir(), reverse=True):
        if not folder.is_dir():
            continue
        try:
            day = date.fromisoformat(folder.name)
        except ValueError:
            continue
        if day >= before or (before - day).days > max_days:
            continue
        for window in sorted(folder.glob('window_*d')):
            manifest = window / 'PUBLISHED_URLS.json'
            legacy = window / 'PUBLISHED.ok'
            sent=next(iter(sorted(window.rglob('SENT_EDITION.json'))),None)
            if sent:
                payload=json.loads(sent.read_text(encoding='utf-8-sig'))
                if payload.get('state')!='DELIVERED_CONFIRMED_BY_OPERATOR' or payload.get('date')!=day.isoformat() or not payload.get('events'):
                    raise RuntimeError(f'Invalid confirmed sent edition: {sent}')
                cards=[{**e,'source_urls':[s['source_url'] for s in e['sources']], 'report_date':day.isoformat()} for e in payload['events']]
                output.append({'date':day,'record':sent,'cards':cards})
            elif manifest.is_file():
                try:
                    payload = json.loads(manifest.read_text(encoding='utf-8-sig'))
                except (OSError, ValueError) as exc:
                    raise RuntimeError(f'Unreadable confirmed publication record: {manifest}') from exc
                if not isinstance(payload, dict) or payload.get('report_date') != day.isoformat():
                    raise RuntimeError(f'Publication date mismatch: {manifest}')
                urls = payload.get('source_urls')
                events = payload.get('events')
                if not isinstance(urls, list) or not urls or not isinstance(events, list) or not events:
                    raise RuntimeError(f'Incomplete publication record: {manifest}')
                allowed = {canonical(u) for u in urls}
                if not allowed or '' in allowed:
                    raise RuntimeError(f'Invalid URLs in publication record: {manifest}')
                cards = []
                for event in events:
                    if not isinstance(event, dict):
                        raise RuntimeError(f'Invalid published event: {manifest}')
                    event_urls = [u for u in event.get('source_urls', []) if canonical(u) in allowed]
                    if not event.get('headline') or not event_urls:
                        raise RuntimeError(f'Published event has missing title/URL: {manifest}')
                    cards.append({'headline': str(event['headline']),
                                  'source_urls': event_urls,
                                  'publication_date': str(event.get('publication_date') or day.isoformat()),
                                  'report_date': day.isoformat()})
                if {canonical(u) for e in cards for u in e['source_urls']} != allowed:
                    raise RuntimeError(f'Published sources do not match published events: {manifest}')
                output.append({'date': day, 'record': manifest, 'cards': cards})
            elif legacy.is_file():
                # Only load a legacy marker when the original audit exists.
                audit = window / 'stakeholder_brief_v2_audit.json'
                if not audit.is_file():
                    raise RuntimeError(f'PUBLISHED.ok without supporting audit: {legacy}')
                payload = json.loads(audit.read_text(encoding='utf-8-sig'))
                cards = [{'headline': e.get('headline',''),
                          'source_urls': [s['source_url'] for s in e.get('sources', [])
                                          if s.get('source_url')],
                          'publication_date': e.get('publication_date', day.isoformat()),
                          'report_date': day.isoformat()}
                         for e in payload.get('events', [])]
                if not cards:
                    raise RuntimeError(f'No events in published legacy audit: {audit}')
                output.append({'date':day,'record':legacy,'cards':cards})
    return output


def published_cards(report_root: Path, before: date, *, max_days: int = 400) -> list[dict]:
    return [c for entry in published_editions(report_root, before, max_days=max_days)
            for c in entry['cards']]


def published_urls(report_root: Path, before: date, *, max_days: int = 400) -> set[str]:
    return {canonical(u) for card in published_cards(report_root, before, max_days=max_days)
            for u in card.get('source_urls', []) if canonical(u)}


# NEWS_SENT_EDITION_IMMUTABILITY_V1
def assert_edition_not_sent(report_root: Path, day: date) -> None:
    """Refuse a run that would replace a confirmed distributed edition.

    The publication manifest is an immutable proof of the emailed report,
    not an approval to overwrite it. Both the master runner and direct
    report builder must call this BEFORE creating any output.
    """
    date_root = Path(report_root) / day.isoformat()
    if not date_root.is_dir():
        return
    for window in sorted(date_root.glob('window_*d')):
        for marker in ('PUBLISHED_URLS.json', 'PUBLISHED.ok', 'SENT_EDITION.json','integrated_edition/SENT_EDITION.json'):
            published = window / marker
            if published.exists():
                raise RuntimeError(
                    'ALREADY_SENT_EDITION_LOCK: ' + str(published) +
                    '. This report date is already registered as distributed. '
                    'Do not regenerate or overwrite its HTML. New articles '
                    'belong in the next scheduled brief as late discoveries.'
                )


def sent_html_integrity(report_root: Path, day: date) -> list[dict]:
    """Read-only SHA256 comparison for a confirmed, recorded edition.

    The sent manifest remains authoritative even when somebody has already
    accidentally overwritten the original HTML. Never rewrite the manifest.
    """
    import hashlib
    date_root = Path(report_root) / day.isoformat()
    result=[]
    for manifest in sorted(date_root.glob('window_*d/PUBLISHED_URLS.json')):
        try:
            payload=json.loads(manifest.read_text(encoding='utf-8-sig'))
        except (OSError,ValueError) as exc:
            raise RuntimeError(f'Invalid sent manifest: {manifest}') from exc
        digest=str(payload.get('sent_html_sha256') or '').lower()
        if not re.fullmatch(r'[a-f0-9]{64}',digest):
            raise RuntimeError(f'Missing SHA256 in immutable sent record: {manifest}')
        recorded_path=Path(str(payload.get('sent_html') or ''))
        if not recorded_path.is_absolute():
            raise RuntimeError(f'Sent HTML path must be absolute: {manifest}')
        current=(hashlib.sha256(recorded_path.read_bytes()).hexdigest()
                 if recorded_path.is_file() else None)
        result.append({'manifest':str(manifest),
                       'sent_html':str(recorded_path),
                       'expected_sha256':digest,
                       'current_sha256':current,
                       'matches':current==digest})
    return result
