"""Release checks consume governed decisions, never reclassify articles."""
import csv
import hashlib
import json
from pathlib import Path
from .identity import sources_for_date, canonical
from .ledger import validate_outputs
from .coverage import missing_surfaces, open_issues


def audit_release(*, report, manifest_path, inventory, coverage, out, report_date):
    issues = []
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    ledger_path = manifest_path.parent / manifest['decision_ledger']
    ledger = json.loads(ledger_path.read_text(encoding='utf-8'))
    if manifest['sha256'] != hashlib.sha256(report.read_bytes()).hexdigest():
        issues.append({'kind': 'REPORT_HASH_MISMATCH'})
    if manifest['decision_ledger_sha256'] != hashlib.sha256(ledger_path.read_bytes()).hexdigest():
        issues.append({'kind': 'DECISION_LEDGER_HASH_MISMATCH'})
    if ledger['report_date'] != report_date or manifest['date'] != report_date:
        issues.append({'kind': 'REPORT_DATE_MISMATCH'})
    issues.extend(manifest['issues'])
    issues.extend(validate_outputs(ledger, manifest, report.read_text(encoding='utf-8')))
    with inventory.open(encoding='utf-8-sig', newline='') as stream:
        inputs = list(csv.DictReader(stream))
    if {canonical(r['source_url']) for r in inputs} != {r['source_url'] for r in ledger['articles']}:
        issues.append({'kind': 'INVENTORY_LEDGER_MEMBERSHIP_MISMATCH'})
    try:
        verified = json.loads(coverage.read_text(encoding='utf-8-sig'))
        if verified.get('report_date') != report_date:raise ValueError('Coverage date mismatch')
        for source in sources_for_date(report_date):
            evidence = verified.get('publishers', {}).get(source, {})
            if (evidence.get('status') != 'VERIFIED' or not evidence.get('reviewer')
                    or not evidence.get('reviewed_at') or not evidence.get('evidence')
                    or missing_surfaces(source,evidence.get('surfaces', [])) or open_issues(evidence)):
                issues.append({'kind': 'PUBLISHER_COVERAGE_NOT_VERIFIED', 'publisher': source,
                               'evidence': evidence.get('evidence'),
                               'open_defects': open_issues(evidence)})
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        issues.append({'kind': 'COVERAGE_MANIFEST_INVALID', 'details': str(exc)})
    result = {'release_status': 'HOLD_FOR_REVIEW' if issues else 'PASS',
              'report_date': report_date, 'report':str(report.resolve()), 'report_sha256': manifest['sha256'],
              'decision_ledger_sha256': manifest['decision_ledger_sha256'],
              'inventory_candidates': len(ledger['articles']), 'story_count': len(manifest['events']),
              'counts': ledger['counts'], 'issues': issues,
              'count_reconciliation': {
                  'input_articles': len(inputs), 'final_decisions': len(ledger['articles']),
                  'included_articles': ledger['counts'].get('INCLUDE', 0),
                  'displayed_events': len(manifest['events']),
                  'merged_supporting_articles': ledger['counts'].get('INCLUDE', 0)-len(manifest['events'])}}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    # QA carries the exact decision IDs and actions; check failures live in the
    # release issues, never as an independent ELIGIBLE/EXCLUDE interpretation.
    rows = ledger['articles']
    with out.with_suffix('.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader();writer.writerows(rows)
    return 0 if not issues else 2
