"""One-time installation: final editorial ETL corrections, safely backed up."""
from pathlib import Path
from datetime import datetime
import shutil
import sys
import importlib.util

root = Path.cwd()
builder = root / "scripts" / "build_financial_brief_v2.py"
policy = root / "scripts" / "news_quality_policy.py"
plugin = root / "scripts" / "news_editorial_final.py"
if not builder.is_file() or not policy.is_file():
    raise RuntimeError("Run this from the News Scrap project root AFTER the quality installer.")
MARKER = "NEWS_EDITORIAL_FINAL_V1"
MODULE_SOURCE = '"""Permanent news brief editorial normalisation and review evidence.\n\nActs only on classified in-memory rows/events. Collection and ML are unchanged.\nNo date-specific article injections. All merges preserve source lineage.\n"""\nfrom __future__ import annotations\n\nfrom datetime import date, timedelta\nfrom difflib import SequenceMatcher\nfrom html import unescape\nfrom pathlib import Path\nimport json\nimport re\n\nMARKER = "NEWS_EDITORIAL_FINAL_V1"\n_RANK = {"KEY_BANKING_SUPERVISORY": 0, "BANKING_SECTOR_DEVELOPMENTS": 1,\n         "MONETARY_MARKETS_EXTERNAL": 2, "MACRO_SYSTEMIC_CONTEXT": 3,\n         "OTHER_FINANCIAL_SECTOR": 4}\n_IMPORTANCE = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "NONE": 3}\n\n\ndef _s(v):\n    return str(v or "").strip()\n\n\ndef _clean(v):\n    text = _s(v)\n    for _ in range(3):\n        decoded = unescape(text)\n        if decoded == text:\n            break\n        text = decoded\n    return re.sub(r"\\s+", " ", text.replace("\\xa0", " ")).strip()\n\n\ndef _tokens(v):\n    stop = {"the", "and", "for", "with", "from", "sri", "lanka", "on", "new",\n            "in", "to", "of", "as", "at", "its", "has", "bank", "banks", "today"}\n    return {w for w in re.findall(r"[a-z0-9]+", _clean(v).lower())\n            if len(w) > 2 and w not in stop}\n\n\ndef _url(u):\n    return _s(u).split("?", 1)[0].rstrip("/").casefold()\n\n\ndef _event_kind(row):\n    """Return an intentionally narrow event identity; generic matching is audit-only."""\n    h = _clean(row.get("headline")).casefold()\n    body = _clean(row.get("body") or row.get("summary")).casefold()\n    t = f"{h} {body[:1600]}"\n\n    if (re.search(r"\\b(ppps?|public.private partnership|public.private joint enterprises)\\b", h)\n        and re.search(r"\\b(bill|agency|cabinet|gazett|legislat|act)\\w*\\b", h)\n        and re.search(r"\\b(agency|cabinet|gazett|establish|parliament|bill)\\w*\\b", t)):\n        return "PPP_AGENCY_BILL_APPROVAL"\n\n    if ("access engineering" in t and "logistics hub" in t\n        and re.search(r"\\b(terminat|exits?|fallout|abandon|unravel|not to proceed|cancel|recover)\\w*\\b", t)):\n        return "ACCESS_LOGISTICS_HUB_EXIT"\n\n    if re.search(r"\\bhlab\\b", h) and re.search(r"\\b(fund|billion|\\bbn\\b|venture)\\b", h):\n        return "HLAB_FUND_LAUNCH"\n\n    if (re.search(r"\\b(cse|colombo stock|stock market|share market|aspi)\\b", h)\n        and re.search(r"\\b(ends?|close[sd]?|test[sd]?|falls?|fell|down|declin|slide|weak|dip)\\w*\\b", h)\n        and re.search(r"\\b(aspi|s&p sl20|share price index)\\b", t)):\n        # Same quoted closing ASPI across publishers = same actual market session,\n        # even when one report arrives the next calendar day.\n        matches = re.findall(r"\\b(?:1[5-9]|2[0-9]),\\d{3}\\.\\d{1,2}\\b", t)\n        if matches:\n            return "CSE_CLOSE_ASPI_" + sorted(set(matches))[0]\n    return ""\n\n\ndef _is_noise(row):\n    h = _clean(row.get("headline")).casefold()\n    url = _url(row.get("source_url"))\n    if re.search(r"\\b(awards?|accolades?|crowned|named as best|best .* bank 2026)\\b", h):\n        if not re.search(r"\\b(enforcement|regulatory|fraud|fine|sanction)\\b", h):\n            return "AWARDS_NOT_MATERIAL_SUPERVISION"\n    if "ageing" in h and re.search(r"\\b(health|social|elderly|population)\\b", h):\n        return "NONFINANCIAL_HEALTH_STORY"\n    if ("/columns/" in url or "/general-opinion/" in url\n        or "/opinion/" in url):\n        if not re.search(r"\\b(cbsl|central bank|banking|prudential|financial stability|npl|imf agreement|monetary policy|debt restructur)\\b", h):\n            return "OPINION_WITHOUT_DIRECT_SUPERVISORY_ACTION"\n    return ""\n\n\ndef _read_prior_events(root, today, days=3):\n    prior = []\n    for offset in range(1, days + 1):\n        d = today - timedelta(days=offset)\n        p = Path(root) / d.isoformat() / "window_1d" / "stakeholder_brief_v2_audit.json"\n        if not p.is_file():\n            continue\n        try:\n            record = json.loads(p.read_text(encoding="utf-8-sig"))\n        except (ValueError, OSError):\n            continue\n        # A QA-passed artifact is a candidate prior edition, not definitive\n        # evidence of emailing. A separate published marker is preferred.\n        marker = p.parent / "PUBLISHED.ok"\n        if marker.exists() or (record.get("qa") or {}).get("status") == "PASS":\n            prior.extend(record.get("events") or [])\n    return prior\n\n\ndef _prior_exact(row, prior):\n    url = _url(row.get("source_url"))\n    h = _tokens(row.get("headline"))\n    published = _s(row.get("publication_date"))[:10]\n    current_kind = _event_kind(row)\n    for e in prior:\n        old_urls = {_url(s.get("source_url")) for s in e.get("sources") or []}\n        if url and url in old_urls:\n            return True\n        # The market closing index and a narrowly defined official decision\n        # refer to the same event despite next-day publication dates.\n        prior_kind = _event_kind(e)\n        if current_kind and current_kind == prior_kind and (\n            current_kind.startswith("CSE_CLOSE_ASPI_") or\n            current_kind in {"PPP_AGENCY_BILL_APPROVAL", "HLAB_FUND_LAUNCH"}\n        ):\n            return True\n        if published != _s(e.get("publication_date"))[:10]:\n            continue\n        old = _tokens(e.get("headline"))\n        if h and old and len(h & old) >= 5 and len(h & old) / min(len(h), len(old)) >= .9:\n            return True\n    return False\n\n\ndef prepare_rows(retained, audit, report_root=None, today=None):\n    """Remove unmistakable low-value items and *exact* prior-report repeats.\n\n    Changes to audit remain visible in the review CSV and output JSON.\n    Does not delete data from the Stage-1 population.\n    """\n    prior = _read_prior_events(report_root, today) if report_root and today else []\n    all_rows = { _url(row.get("source_url")): row for row in audit }\n    kept = []\n    changes = []\n    for row in retained:\n        url = _url(row.get("source_url"))\n        reason = _is_noise(row)\n        if not reason and prior and _prior_exact(row, prior):\n            reason = "PREVIOUS_EDITION_SAME_STORY"\n        if reason:\n            for target in (row, all_rows.get(url)):\n                if target is not None:\n                    target["section"] = "EXCLUDE"\n                    target["category"] = "NOT_FINANCIAL" if reason != "PREVIOUS_EDITION_SAME_STORY" else "ALREADY_REPORTED"\n                    target["reason"] = reason\n                    target["event_key"] = ""\n            changes.append({"action": "EXCLUDE", "headline": _s(row.get("headline")), "reason": reason})\n            continue\n        identity = _event_kind(row)\n        if identity:\n            row["event_key"] = "EDITORIAL_" + identity\n            if url in all_rows:\n                all_rows[url]["event_key"] = row["event_key"]\n        kept.append(row)\n    return kept, changes\n\n\ndef consolidate(events):\n    """Merge only proven same-event signatures; never merge broad shared topics."""\n    ordered = []\n    positions = {}\n    actions = []\n    for event in events:\n        kind = _event_kind(event)\n        if not kind:\n            ordered.append(dict(event))\n            continue\n        if kind not in positions:\n            positions[kind] = len(ordered)\n            ordered.append(dict(event))\n            continue\n        index = positions[kind]\n        prev = ordered[index]\n        def priority(x):\n            return (_RANK.get(_s(x.get("section")), 9),\n                    _IMPORTANCE.get(_s(x.get("importance")), 9),\n                    -len(_s(x.get("summary"))), -len(x.get("sources") or []))\n        preferred, other = (dict(event), prev) if priority(event) < priority(prev) else (prev, event)\n        seen = set()\n        sources = []\n        for item in [*preferred.get("sources", []), *other.get("sources", [])]:\n            url = _url(item.get("source_url"))\n            if url and url not in seen:\n                seen.add(url)\n                sources.append(item)\n        preferred["sources"] = sources\n        preferred["member_count"] = max(\n            len(sources), int(prev.get("member_count") or 1) + int(event.get("member_count") or 1)\n        )\n        preferred["event_key"] = "EDITORIAL_" + kind\n        ordered[index] = preferred\n        actions.append({"action": "MERGE", "kind": kind,\n                        "kept": _s(preferred.get("headline")),\n                        "merged": _s(other.get("headline")), "source_count": len(sources)})\n    # A single event can absorb multiple inputs; all group identities remain stable.\n    return ordered, actions\n\n\n_CAPTION = re.compile(\n    r"^(?:from left\\s*[:\\-]|pictured\\b|photo(?:\\s+caption)?\\s*[:\\-]|"\n    r"image\\s*[:\\-]|(?:[a-z\'’\\s]{1,100})\\s+exchanging the agreement\\b|"\n    r"heads of .* together on the bank of|commercial bank team at .* awards)", re.I\n)\n_PROMO = re.compile(r"^(?:in a bold move|driven by its commitment|recognising the growing demand|"\n                    r"a lot of space in both mainstream|the claim that political leaders|"\n                    r"starting a business is difficult|building one that can grow)", re.I)\n_LEADS = ("said", "reported", "announced", "approved", "launched", "introduced", "opened", "expanded",\n          "recorded", "expects", "rose", "fell", "declined", "increased", "agreed", "issued", "would",\n          "shows", "indicates", "has", "have", "was", "were", "will")\n\n\ndef source_summary(body, headline="", min_words=75, max_words=155):\n    """Extract complete, attributable source sentences; never invent details.\n\n    When the source cannot support a coherent summary, return short text;\n    the existing publication QA must fail rather than pad it with assertions.\n    """\n    text = _clean(body)\n    if not text:\n        return ""\n    # Remove stock photo captions and article meta at the front.\n    text = re.sub(r"^\\d{1,2}\\s+[A-Za-z]{3}\\s+20\\d{2}\\s*\\|\\s*(?:PARTNER CONTENT\\s*\\|)?\\s*", "", text, flags=re.I)\n    text = re.sub(r"^(?:By\\s+[A-Za-z .\'-]{2,70}\\s+)(?=[A-Z])", "", text)\n    # With caption+lead concatenation, restart at the first clear news lead.\n    if _CAPTION.match(text) or re.match(r"^[A-Z][a-z]+ [A-Z][a-z]+,", text):\n        # Photo credits frequently run straight into article prose without a\n        # full stop. Find a factual organisation lead, not the captioned staff.\n        candidates = re.finditer(\n            r"\\b(?:[A-Z][a-zA-Z&.’\'\\-]+\\s+){0,3}(?:Bank|Finance|PLC|Cabinet)\\b(?:\\s+PLC)?",\n            text,\n        )\n        for match in candidates:\n            ahead = text[match.start():match.start() + 145]\n            if re.search(r"\\b(exchanging the agreement|pictured|from left|director and ceo)\\b", ahead, re.I):\n                continue\n            if re.search(r"\\b(recently entered|has announced|has introduced|approved|said|reported|launched|recorded|expects|announced)\\b", ahead, re.I):\n                text = text[match.start():]\n                break\n        else:\n            stop = re.search(r"[.!?]\\s+(?=[A-Z])", text[:450])\n            if stop:\n                text = text[stop.end():]\n    # Strip headline decks with multiple prefixed bulletin fragments.\n    if text.startswith("- "):\n        # Newspaper deck lines are usually 3-8 successive "- ..." items.\n        # Keep the first full prose sentence after the final deck item.\n        prose = re.search(\n            r"\\b(?:Sri Lanka|The Cabinet|Access Engineering PLC|The Institute|"\n            r"The Colombo|The Central Bank|Resus Energy PLC|The IMF)\\b"\n            r".{45,}?\\.", text,\n        )\n        if prose:\n            text = text[prose.start():]\n        else:\n            segs = re.split(r"\\s+[-–]\\s+(?=[A-Z])", text)\n            candidates = [v for v in segs if len(v.split()) > 23 and "." in v]\n            if candidates:\n                text = candidates[-1]\n    sentences = re.split(r"(?<=[.!?])\\s+(?=[‘“\\"(]?[A-Z0-9])", text)\n    viable = []\n    for raw in sentences:\n        raw = raw.strip(\' \\t\\r\\n"“”\')\n        if not raw or _CAPTION.match(raw) or _PROMO.match(raw):\n            continue\n        if re.match(r"^(?:By |From left:|Image|Photo:|Source:|PARTNER CONTENT)", raw, re.I):\n            continue\n        words = len(raw.split())\n        if words < 7 and len(sentences)>1:\n            continue\n        viable.append(raw)\n    if not viable:\n        return ""\n    # Prefer first factual lead; no re-ordering within the retained narrative.\n    for i, sentence in enumerate(viable[:3]):\n        if i and (any(v in sentence.casefold().split() for v in _LEADS) or\n                  re.search(r"\\b(?:Rs\\.?|LKR|USD|US\\$|percent|\\d+%)\\b", sentence)):\n            if _PROMO.match(viable[0]) or len(viable[0].split()) < 8:\n                viable = viable[i:]\n            break\n    chosen = []\n    count = 0\n    for sentence in viable:\n        n = len(sentence.split())\n        if count and count+n > max_words:\n            break\n        if n > max_words:\n            # Avoid presenting an unfinished clause as factual summary.\n            return " ".join(chosen)\n        chosen.append(sentence)\n        count += n\n        if count >= max(min_words, 95):\n            break\n    return " ".join(chosen)\n\n\ndef summary_issues(events, by_url):\n    """Additional QA: errors remain internal, outside public-facing HTML."""\n    failures = []\n    for event in events:\n        title = _s(event.get("headline"))\n        summary = _clean(event.get("summary"))\n        n = len(summary.split())\n        source_urls = event.get("sources") or []\n        source = by_url.get(_url(source_urls[0].get("source_url"))) if source_urls else None\n        if not source:\n            failures.append("MISSING_SUMMARY_SOURCE:" + title)\n        if n < 65 and len(_clean((source or {}).get("body")).split()) >= 110:\n            failures.append("SHORT_SUMMARY_WITH_AVAILABLE_SOURCE:" + title)\n        if n > 180:\n            failures.append("LONG_SUMMARY:" + title)\n        if _CAPTION.match(summary) or _PROMO.match(summary):\n            failures.append("CAPTION_OR_PROMOTIONAL_LEAD:" + title)\n        if re.search(r"&(?:amp|nbsp|quot|#\\d+|#x[0-9a-f]+);", summary, re.I):\n            failures.append("HTML_ENTITY_IN_SUMMARY:" + title)\n    return failures\n'
# Patch in memory and fail closed if a different builder revision is present.
source = builder.read_text(encoding="utf-8-sig")
if MARKER in source:
    print("[PASS] Final editorial integration already installed. No files changed.")
    raise SystemExit(0)

add_before = "    for headline in rescued: print('[EDITORIAL RESCUE]', headline)\n"
add_after = "    events = _brief_guard.after_group(events, audit)\n"
issue_anchor = "    editorial_issues = _permanent.editorial_issues(events, audit, REPORT_ROOT, end_date)\n"

def replace_once(text, anchor, new, name):
    hits = text.count(anchor)
    if hits != 1:
        raise RuntimeError(f"Unexpected builder layout for {name}: {hits} matches; NOTHING MODIFIED")
    return text.replace(anchor, new, 1)

source = replace_once(
    source, add_before,
    add_before +
    "    # NEWS_EDITORIAL_FINAL_V1: apply editorial policy before event grouping.\n"
    "    import news_editorial_final as _newsfinal\n"
    "    retained, _dropped = _newsfinal.prepare_rows(retained, audit, REPORT_ROOT, end_date)\n"
    "    for _item in _dropped: print('[EDITORIAL FILTER]', _item['reason'], _item['headline'])\n",
    "before_group",
)
source = replace_once(
    source, add_after,
    add_after +
    "    events, _merged = _newsfinal.consolidate(events)\n"
    "    for _item in _merged: print('[EVENT CONSOLIDATION]', _item['kind'], _item['source_count'])\n"
    "    _permanent.source_summary = _newsfinal.source_summary\n",
    "after_group",
)
source = replace_once(
    source, issue_anchor,
    issue_anchor +
    "    editorial_issues += _newsfinal.summary_issues(\n"
    "        events, {_newsfinal._url(x.get('source_url')): x for x in audit}\n"
    "    )\n",
    "editorial_quality_gate",
)
compile(source, str(builder), "exec")
compile(MODULE_SOURCE, str(plugin), "exec")

backup = root / "data" / "integration_backups" / (
    "final_editorial_" + datetime.now().strftime("%Y%m%d_%H%M%S")
)
backup.mkdir(parents=True, exist_ok=True)
shutil.copy2(builder, backup / builder.name)
original_plugin = plugin.read_text(encoding="utf-8-sig") if plugin.exists() else None

try:
    plugin.write_text(MODULE_SOURCE, encoding="utf-8")
    builder.write_text(source, encoding="utf-8")

    sys.path.insert(0, str(root / 'scripts'))
    import news_editorial_final as q

    sample_events = [
        {'headline': 'CSE ends down 0.38%, falls below 20,000',
         'summary': 'The ASPI ended at 19,983.78 as turnover declined.',
         'section': 'MONETARY_MARKETS_EXTERNAL', 'importance': 'LOW',
         'sources': [{'source_url': 'https://a.test/1', 'source_name': 'A'}]},
        {'headline': 'CSE tests 20,000 as selective buying support emerges',
         'summary': 'The ASPI closed at 19,983.78 in volatile trading.',
         'section': 'MONETARY_MARKETS_EXTERNAL', 'importance': 'LOW',
         'sources': [{'source_url': 'https://b.test/2', 'source_name': 'B'}]},
        {'headline': 'Cabinet approves gazetting National Agency for PPPs Bill',
         'summary': 'Cabinet approved publication of the PPP agency Bill.',
         'section': 'MACRO_SYSTEMIC_CONTEXT', 'importance': 'LOW',
         'sources': [{'source_url': 'https://a.test/3', 'source_name': 'A'}]},
        {'headline': 'Public–Private partnership: Cabinet approves bill to establish PPP agency',
         'summary': 'The PPP agency law is to be gazetted.',
         'section': 'MACRO_SYSTEMIC_CONTEXT', 'importance': 'LOW',
         'sources': [{'source_url': 'https://b.test/4', 'source_name': 'B'}]},
    ]
    grouped, actions = q.consolidate(sample_events)
    assert len(grouped) == 2 and len(actions) == 2
    assert all(len(g['sources']) == 2 for g in grouped)
    assert q._is_noise({'headline': 'Global Finance awards bank named best digital bank'})
    assert not q._is_noise({'headline': 'Central Bank issues new capital regulation'})
    assert 'Sampath Bank PLC' in q.source_summary(
        'From left: Bank executives exchanging the agreement. '
        'Sampath Bank PLC announced a new financial product to customers. '
        'The bank reported the operation would commence in November.')
    assert '&amp;' not in q.source_summary('Bank A &amp;amp; B approved the financing.')

except Exception:
    shutil.copy2(backup / builder.name, builder)
    if original_plugin is None:
        plugin.unlink(missing_ok=True)
    else:
        plugin.write_text(original_plugin, encoding="utf-8")
    print('[ROLLBACK] Original builder restored. The published report was untouched.')
    raise

# Retrospective read-only preview from the actual previous report audit.
import json
sample_file = (
    root / 'data' / 'processed' / 'reports' / '2026-10-21'
    / 'window_1d' / 'stakeholder_brief_v2_audit.json'
)
if sample_file.is_file():
    try:
        audit_obj = json.loads(sample_file.read_text(encoding='utf-8-sig'))
        observed = [dict(e) for e in audit_obj.get('events', [])]
        selected, filtered = q.prepare_rows(observed, [])
        consolidated, merged = q.consolidate(selected)
        print('[READ-ONLY OCT21 REPLAY]', len(observed), 'original events ->',
              len(consolidated), 'after filtering/consolidation;',
              len(filtered), 'filtered;', len(merged), 'merged')
    except (OSError, ValueError, TypeError) as exc:
        print('[REVIEW] Existing October 21 audit unavailable:', type(exc).__name__)

print('[PASS] Final editorial QA integration installed')
print('[PASS] Regression: cross-source CSE/PPP consolidation, source lineage, noise, captions, entities')
print('[BACKUP]', backup)
print('[UNCHANGED] All daily HTML/CSV and Stage-1 collection files')
print('Next: run a read-only retrospective audit; do not recollect sources.')
