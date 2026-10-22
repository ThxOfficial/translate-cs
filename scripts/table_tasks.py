"""Row-group tasks for one atomic pipe-table chunk; canonical source stays intact."""
import argparse
import json
import re
from pathlib import Path

import content_guard
from manifest import file_hash


def prepare(temp_dir, cid, rows_per_group=20):
    if not content_guard.CHUNK_ID_RE.fullmatch(cid) or type(rows_per_group) is not int or rows_per_group < 1:
        raise ValueError('Invalid table task identity or row budget')
    root = Path(temp_dir)
    source = root / f'{cid}.md'
    text = source.read_text(encoding='utf-8')
    lines = text.strip().splitlines()
    if (len(lines) < 3 or any(not re.match(r'^\s*\|', line) for line in lines)
            or not re.match(r'^\s*\|\s*:?-+', lines[1])):
        raise ValueError('Row grouping requires one standalone pipe-table chunk')
    if len(set(len(content_guard._table_cells(line)) for line in lines)) != 1:
        raise ValueError('Table columns are inconsistent')
    folder = root / 'tasks' / f'{cid}.table'
    manifest_path = folder / 'groups.json'
    if manifest_path.is_file():
        cached = json.loads(manifest_path.read_text(encoding='utf-8'))
        if cached['source_sha256'] != file_hash(source) or cached['rows_per_group'] != rows_per_group:
            raise ValueError('Table source or row budget changed; preserve drafts and use a new task directory')
        return cached
    content_guard.prepare_chunk(root, cid)
    masked = (root / 'tasks' / f'{cid}.md').read_text(encoding='utf-8').strip().splitlines()
    groups = []
    content_guard._atomic_write(folder / 'header.md', masked[0] + '\n')
    for i in range(2, len(masked), rows_per_group):
        name = f'rows{i - 1:05d}'
        group = {'id': name, 'row_start': i, 'row_end': min(len(masked), i + rows_per_group),
                 'row_count': len(masked[i:i + rows_per_group])}
        groups.append(group)
        content_guard._atomic_write(folder / (name + '.md'), '\n'.join(masked[i:i + rows_per_group]) + '\n')
    manifest = {'schema_version': 1, 'chunk_id': cid, 'source_sha256': file_hash(source),
                'masked_sha256': file_hash(root / 'tasks' / f'{cid}.md'), 'rows_per_group': rows_per_group,
                'separator': masked[1], 'groups': groups}
    content_guard._atomic_write(manifest_path, json.dumps(manifest, indent=2))
    return manifest


def commit(temp_dir, cid):
    if not content_guard.CHUNK_ID_RE.fullmatch(cid):
        raise ValueError('Invalid chunk identity')
    root = Path(temp_dir)
    folder = root / 'tasks' / f'{cid}.table'
    manifest = json.loads((folder / 'groups.json').read_text(encoding='utf-8'))
    if (file_hash(root / f'{cid}.md') != manifest['source_sha256']
            or file_hash(root / 'tasks' / f'{cid}.md') != manifest['masked_sha256']):
        raise ValueError('Table task source changed')
    header = (folder / 'output_header.md').read_text(encoding='utf-8').splitlines()
    if len(header) != 1:
        raise ValueError('Translated table header must be exactly one row')
    combined = header + [manifest['separator']]
    for group in manifest['groups']:
        rows = (folder / ('output_' + group['id'] + '.md')).read_text(encoding='utf-8').splitlines()
        if len(rows) != group['row_count']:
            raise ValueError('A row group lost or added data rows')
        combined.extend(rows)
    draft = root / 'drafts' / f'output_{cid}.masked.md'
    content_guard._atomic_write(draft, '\n'.join(combined) + '\n')
    return content_guard.restore_chunk(root, cid, draft)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['prepare', 'commit'])
    p.add_argument('temp_dir')
    p.add_argument('chunk_id')
    p.add_argument('--rows-per-group', type=int, default=20)
    a = p.parse_args()
    result = prepare(a.temp_dir, a.chunk_id, a.rows_per_group) if a.action == 'prepare' else str(commit(a.temp_dir, a.chunk_id))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
