"""Record actual format inspection separately from successful file generation."""
import argparse
import json
from pathlib import Path

import content_guard
from manifest import file_hash

CHECKS = {'docx': {'math', 'cjk', 'tables', 'footnotes', 'pagination'},
          'pdf': {'math', 'cjk', 'tables', 'footnotes', 'pagination'},
          'epub': {'math', 'cjk', 'tables', 'footnotes', 'reader_navigation'}}


def record(temp_dir, fmt, evidence_file):
    root = Path(temp_dir)
    evidence = json.loads(Path(evidence_file).read_text(encoding='utf-8'))
    digest = file_hash(root / ('book.' + fmt))
    if (evidence.get('artifact_sha256') != digest or set(evidence.get('checks', [])) != CHECKS[fmt]
            or not evidence.get('reviewer') or not evidence.get('engine') or evidence.get('findings') != []
            or not evidence.get('inspection_scope')):
        raise ValueError('Format review requires a current artifact hash, actual reviewer/engine/scope, complete checks and no unresolved findings')
    path = root / 'format_reviews.json'
    reports = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {'schema_version': 1, 'formats': {}}
    reports['formats'][fmt] = evidence
    content_guard._atomic_write(path, json.dumps(reports, ensure_ascii=False, indent=2))
    return evidence


def status(temp_dir, fmt):
    root = Path(temp_dir)
    path = root / 'format_reviews.json'
    if not path.is_file():
        return 'not_recorded'
    record = json.loads(path.read_text(encoding='utf-8')).get('formats', {}).get(fmt, {})
    if record.get('artifact_sha256') != file_hash(root / ('book.' + fmt)):
        return 'stale'
    if (set(record.get('checks', [])) == CHECKS[fmt] and record.get('findings') == []
            and record.get('reviewer') and record.get('engine') and record.get('inspection_scope')):
        return 'passed'
    return 'invalid'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('temp_dir')
    p.add_argument('--format', choices=sorted(CHECKS), required=True)
    p.add_argument('--evidence-file', required=True)
    a = p.parse_args()
    print(json.dumps(record(a.temp_dir, a.format, a.evidence_file), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
