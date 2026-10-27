"""Permanent news brief editorial normalisation and review evidence.

Acts only on classified in-memory rows/events. Collection and ML are unchanged.
No date-specific article injections. All merges preserve source lineage.
"""
from __future__ import annotations

from datetime import date, timedelta
from difflib import SequenceMatcher
from html import unescape
from pathlib import Path
import json
import re

MARKER = "NEWS_EDITORIAL_FINAL_V1"
_RANK = {"KEY_BANKING_SUPERVISORY": 0, "BANKING_SECTOR_DEVELOPMENTS": 1,
         "MONETARY_MARKETS_EXTERNAL": 2, "MACRO_SYSTEMIC_CONTEXT": 3,
         "OTHER_FINANCIAL_SECTOR": 4}
_IMPORTANCE = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "NONE": 3}


def _s(v):
    return str(v or "").strip()


def _clean(v):
    text = _s(v)
    for _ in range(3):
        decoded = unescape(text)
        if decoded == text:
            break
        text = decoded
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def _tokens(v):
    stop = {"the", "and", "for", "with", "from", "sri", "lanka", "on", "new",
            "in", "to", "of", "as", "at", "its", "has", "bank", "banks", "today"}
    return {w for w in re.findall(r"[a-z0-9]+", _clean(v).lower())
            if len(w) > 2 and w not in stop}


def _url(u):
    return _s(u).split("?", 1)[0].rstrip("/").casefold()


def _event_kind(row):
    """Return an intentionally narrow event identity; generic matching is audit-only."""
    h = _clean(row.get("headline")).casefold()
    body = _clean(row.get("body") or row.get("summary")).casefold()
    t = f"{h} {body[:1600]}"

    if (re.search(r"\b(ppps?|public.private partnership|public.private joint enterprises)\b", h)
        and re.search(r"\b(bill|agency|cabinet|gazett|legislat|act)\w*\b", h)
        and re.search(r"\b(agency|cabinet|gazett|establish|parliament|bill)\w*\b", t)):
        return "PPP_AGENCY_BILL_APPROVAL"

    if ("access engineering" in t and "logistics hub" in t
        and re.search(r"\b(terminat|exits?|fallout|abandon|unravel|not to proceed|cancel|recover)\w*\b", t)):
        return "ACCESS_LOGISTICS_HUB_EXIT"

    # Signed IRD / Excise coordination agreement has multiple publisher
    # headlines. Treat the signing itself as one event, not later actions.
    if (('excise' in h) and ('inland revenue' in h or 'ird' in h or 'tax evaders' in h)
        and re.search(r'\b(mou|sign|join forces|tackle|curb)\b', h)
        and re.search(r'\b(tax evasion|tax evaders|money laundering|tax)\b', h)):
        return 'IRD_EXCISE_MOU_SIGNING_2026_10'

    if re.search(r"\bhlab\b", h) and re.search(r"\b(fund|billion|\bbn\b|venture)\b", h):
        return "HLAB_FUND_LAUNCH"

    if (re.search(r"\b(cse|colombo stock|stock market|share market|aspi)\b", h)
        and re.search(r"\b(ends?|close[sd]?|test[sd]?|falls?|fell|down|declin|slide|weak|dip)\w*\b", h)
        and re.search(r"\b(aspi|s&p sl20|share price index)\b", t)):
        # Same quoted closing ASPI across publishers = same actual market session,
        # even when one report arrives the next calendar day.
        matches = re.findall(r"\b(?:1[5-9]|2[0-9]),\d{3}\.\d{1,2}\b", t)
        if matches:
            return "CSE_CLOSE_ASPI_" + sorted(set(matches))[0]
    # Consolidate the same NCPI monthly release reported under different titles.
    # Do not conflate national NCPI with Colombo CCPI or generic inflation news.
    ncpi = "ncpi" in t or "national consumer price index" in t
    if ncpi and ("inflation" in h or "ncpi" in h):
        month_match = re.search(
            r"\b(january|february|march|april|may|june|july|august|"
            r"september|october|november|december|sept)\b", t)
        rate_match = re.search(r"\b(\d+(?:\.\d+)?)\s*%", h)
        if month_match and rate_match:
            month = "september" if month_match.group(1) == "sept" else month_match.group(1)
            return "SL_NCPI_" + month.upper() + "_" + rate_match.group(1).replace(".", "_")
    return ""


def _is_noise(row):
    h = _clean(row.get("headline")).casefold()
    url = _url(row.get("source_url"))
    if re.search(r"\b(awards?|accolades?|crowned|named as best|best .* bank 2026)\b", h):
        if not re.search(r"\b(enforcement|regulatory|fraud|fine|sanction)\b", h):
            return "AWARDS_NOT_MATERIAL_SUPERVISION"
    if "ageing" in h and re.search(r"\b(health|social|elderly|population)\b", h):
        return "NONFINANCIAL_HEALTH_STORY"
    if ("/columns/" in url or "/general-opinion/" in url
        or "/opinion/" in url):
        if not re.search(r"\b(cbsl|central bank|banking|prudential|financial stability|npl|imf agreement|monetary policy|debt restructur)\b", h):
            return "OPINION_WITHOUT_DIRECT_SUPERVISORY_ACTION"
    return ""


def _read_prior_events(root, today, days=400):
    """Use only published events from immutable sent-edition manifests."""
    from news_publication_history import published_cards
    return [{'headline':c['headline'],
             'publication_date':c.get('publication_date',''),
             'sources':[{'source_url':u} for u in c.get('source_urls',[])]}
            for c in published_cards(root,today,max_days=days)]


def _prior_exact(row, prior):
    url = _url(row.get("source_url"))
    h = _tokens(row.get("headline"))
    published = _s(row.get("publication_date"))[:10]
    current_kind = _event_kind(row)
    for e in prior:
        old_urls = {_url(s.get("source_url")) for s in e.get("sources") or []}
        if url and url in old_urls:
            return True
        # The market closing index and a narrowly defined official decision
        # refer to the same event despite next-day publication dates.
        prior_kind = _event_kind(e)
        if current_kind and current_kind == prior_kind and (
            current_kind.startswith("CSE_CLOSE_ASPI_") or
            current_kind in {"PPP_AGENCY_BILL_APPROVAL", "HLAB_FUND_LAUNCH",
                             "IRD_EXCISE_MOU_SIGNING_2026_10"}
        ):
            return True
        old = _tokens(e.get("headline"))
        if h and old and len(h & old) >= 5 and len(h & old) / min(len(h), len(old)) >= .92:
            return True
    return False


def prepare_rows(retained, audit, report_root=None, today=None):
    """Remove unmistakable low-value items and *exact* prior-report repeats.

    Changes to audit remain visible in the review CSV and output JSON.
    Does not delete data from the Stage-1 population.
    """
    prior = _read_prior_events(report_root, today) if report_root and today else []
    all_rows = { _url(row.get("source_url")): row for row in audit }
    kept = []
    changes = []
    for row in retained:
        url = _url(row.get("source_url"))
        reason = _is_noise(row)
        if not reason and prior and _prior_exact(row, prior):
            reason = "PREVIOUS_EDITION_SAME_STORY"
        if reason:
            for target in (row, all_rows.get(url)):
                if target is not None:
                    target["section"] = "EXCLUDE"
                    target["category"] = "NOT_FINANCIAL" if reason != "PREVIOUS_EDITION_SAME_STORY" else "ALREADY_REPORTED"
                    target["reason"] = reason
                    target["event_key"] = ""
            changes.append({"action": "EXCLUDE", "headline": _s(row.get("headline")), "reason": reason})
            continue
        identity = _event_kind(row)
        if identity:
            row["event_key"] = "EDITORIAL_" + identity
            if url in all_rows:
                all_rows[url]["event_key"] = row["event_key"]
        kept.append(row)
    return kept, changes


def consolidate(events):
    """Merge only proven same-event signatures; never merge broad shared topics."""
    ordered = []
    positions = {}
    actions = []
    for event in events:
        kind = _event_kind(event)
        if not kind:
            ordered.append(dict(event))
            continue
        if kind not in positions:
            positions[kind] = len(ordered)
            ordered.append(dict(event))
            continue
        index = positions[kind]
        prev = ordered[index]
        def priority(x):
            return (_RANK.get(_s(x.get("section")), 9),
                    _IMPORTANCE.get(_s(x.get("importance")), 9),
                    -len(_s(x.get("summary"))), -len(x.get("sources") or []))
        preferred, other = (dict(event), prev) if priority(event) < priority(prev) else (prev, event)
        seen = set()
        sources = []
        for item in [*preferred.get("sources", []), *other.get("sources", [])]:
            url = _url(item.get("source_url"))
            if url and url not in seen:
                seen.add(url)
                sources.append(item)
        preferred["sources"] = sources
        preferred["member_count"] = max(
            len(sources), int(prev.get("member_count") or 1) + int(event.get("member_count") or 1)
        )
        preferred["event_key"] = "EDITORIAL_" + kind
        ordered[index] = preferred
        actions.append({"action": "MERGE", "kind": kind,
                        "kept": _s(preferred.get("headline")),
                        "merged": _s(other.get("headline")), "source_count": len(sources)})
    # A single event can absorb multiple inputs; all group identities remain stable.
    return ordered, actions


_CAPTION = re.compile(
    r"^(?:from left\s*[:\-]|pictured\b|photo(?:\s+caption)?\s*[:\-]|"
    r"image\s*[:\-]|(?:[a-z'’\s]{1,100})\s+exchanging the agreement\b|"
    r"heads of .* together on the bank of|commercial bank team at .* awards)", re.I
)
_PROMO = re.compile(r"^(?:in a bold move|driven by its commitment|recognising the growing demand|"
                    r"a lot of space in both mainstream|the claim that political leaders|"
                    r"starting a business is difficult|building one that can grow)", re.I)
_LEADS = ("said", "reported", "announced", "approved", "launched", "introduced", "opened", "expanded",
          "recorded", "expects", "rose", "fell", "declined", "increased", "agreed", "issued", "would",
          "shows", "indicates", "has", "have", "was", "were", "will")


def source_summary(body, headline="", min_words=75, max_words=155):
    """Summarize *only* source sentences; do not fabricate details to hit a quota.

    Keep Rs./Dr./financial decimals intact, retain short factual sentences, and
    avoid dropping an otherwise useful paragraph because a single sentence is long.
    Publication QA remains responsible for rejecting insufficient evidence.
    """
    # NEWS_SOURCE_SUMMARY_V2_20261022
    text = _clean(body)
    if not text:
        return ""
    text = re.sub(r"^\d{1,2}\s+[A-Za-z]{3,9}\s+20\d{2}\s*\|\s*", "", text, flags=re.I)
    text = re.sub(r"^(?:PARTNER CONTENT|ADVERTISEMENT)\s*\|?\s*", "", text, flags=re.I)
    text = re.sub(r"^By\s+[A-Za-z .’'\-]{2,70}(?=\s+(?:The |Sri |[A-Z]{2,}))", "", text)

    # Strip a recognised caption only when a verifiable prose lead follows it.
    if _CAPTION.match(text):
        lead = re.search(
            r"\b(?:[A-Z][A-Za-z&.’'\-]*\s+){0,4}"
            r"(?:Bank|PLC|Finance|Cabinet|Government|CSE|NDB)\b.{0,75}?"
            r"\b(?:said|reported|announced|approved|issued|recorded|launched|entered|expects|fell|rose)\b",
            text[30:], re.I,
        )
        if lead:
            text = text[30 + lead.start():]

    # Preserve the periods inside abbreviations/amounts rather than treating
    # Rs. 2.9 billion or Dr. Silva as three separate sentences.
    stop = '\uE000'
    protected = re.sub(r"(?<=\d)\.(?=\d)", stop, text)
    protected = re.sub(
        r"\b(?:Rs|Dr|Prof|Mr|Mrs|Ms|No|St|Ltd|Inc|Co|vs|e\.g|i\.e)\.",
        lambda m: m.group(0).replace('.', stop), protected, flags=re.I,
    )
    protected = re.sub(r"\bU\.S\.", 'U' + stop + 'S' + stop, protected, flags=re.I)
    parts = re.split(r"(?<=[.!?])\s+(?=[‘“(]?[A-Z0-9])", protected)

    sentences = []
    for item in parts:
        item = item.replace(stop, '.').strip().strip(chr(34) + '“”')
        if not item:
            continue
        if re.match(r"^(?:By\s|From left[:\s]|Image[:\s]|Photo[:\s]|Source[:\s])", item, re.I):
            continue
        if _CAPTION.match(item) or _PROMO.match(item):
            continue
        if len(item.split()) < 4 and len(parts) > 1:
            continue
        sentences.append(item)

    if not sentences:
        return ''

    chosen = []
    count = 0
    for sentence in sentences:
        n = len(sentence.split())
        if n > 180:
            # A very long run-on segment cannot safely be clipped mid-claim.
            # Prefer genuine punctuation boundaries, never add invented prose.
            pieces = re.split(r"(?<=[;:])\s+(?=[A-Z0-9])", sentence)
            if len(pieces) == 1:
                if chosen:
                    break
                return ''
            for piece in pieces:
                pn = len(piece.split())
                if count + pn > 180:
                    break
                chosen.append(piece)
                count += pn
            break
        if chosen and count + n > max_words:
            # A final complete sentence may exceed the *target* length without
            # exceeding the existing 180-word QA limit.
            if count < min_words and count + n <= 180:
                chosen.append(sentence)
                count += n
            break
        if not chosen and n > max_words and n <= 180:
            chosen.append(sentence)
            count += n
            break
        chosen.append(sentence)
        count += n
        if count >= max(min_words, 95):
            break

    result = ' '.join(chosen).strip()
    # Only a genuine short source may yield a short summary; an adequately long
    # source with an unusable extract is deliberately left for the existing QA.
    return result


def summary_issues(events, by_url):
    """Additional QA: errors remain internal, outside public-facing HTML."""
    failures = []
    for event in events:
        title = _s(event.get("headline"))
        summary = _clean(event.get("summary"))
        n = len(summary.split())
        source_urls = event.get("sources") or []
        source = by_url.get(_url(source_urls[0].get("source_url"))) if source_urls else None
        if not source:
            failures.append("MISSING_SUMMARY_SOURCE:" + title)
        if n < 65 and len(_clean((source or {}).get("body")).split()) >= 110:
            failures.append("SHORT_SUMMARY_WITH_AVAILABLE_SOURCE:" + title)
        if n > 180:
            failures.append("LONG_SUMMARY:" + title)
        if _CAPTION.match(summary) or _PROMO.match(summary):
            failures.append("CAPTION_OR_PROMOTIONAL_LEAD:" + title)
        if re.search(r"&(?:amp|nbsp|quot|#\d+|#x[0-9a-f]+);", summary, re.I):
            failures.append("HTML_ENTITY_IN_SUMMARY:" + title)
    return failures
