"""Exact source-block bilingual alignment and explicit separate annotations."""
import argparse
import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import content_guard
import convert
import document_context
import merge_and_build
import quality
from manifest import file_hash


def align(temp_dir):
    root, source, mapping, _ = document_context._source(temp_dir)
    rows, hashes = [], {}
    for chunk in sorted(mapping['chunks'], key=lambda c: c['order']):
        output = root / f"output_{chunk['id']}.md"
        text = output.read_text(encoding='utf-8')
        hashes[chunk['id']] = file_hash(output)
        source_blocks = [b for b in mapping['blocks'] if b['chunk_id'] == chunk['id']
                         and source[b['source_start']:b['source_end']].strip()]
        target_blocks = [b for b in convert._structural_blocks_with_offsets(text) if b['source_text'].strip()]
        if len(source_blocks) != len(target_blocks) or any(a['kind'] != b['kind'] for a, b in zip(source_blocks, target_blocks)):
            raise ValueError(f"Cannot establish exact block alignment for {chunk['id']}; no fuzzy alignment is applied")
        for a, b in zip(source_blocks, target_blocks):
            rows.append({'block_id': a['block_id'], 'kind': a['kind'], 'chunk_id': chunk['id'],
                         'source_span': [a['source_start'], a['source_end']],
                         'output_span': [b['source_start'], b['source_end']]})
    return {'schema_version': 1, 'source_sha256': file_hash(root / 'input.md'),
            'document_map_sha256': file_hash(root / 'document_map.json'),
            'output_hashes': hashes, 'coordinate_unit': 'python_unicode_codepoint', 'rows': rows}


def validate_annotations(temp_dir, alignment, payload):
    root = Path(temp_dir)
    if not isinstance(payload, dict) or payload.get('schema_version') != 1:
        raise ValueError('Annotations require schema 1')
    for key in ('source_sha256', 'output_hashes'):
        if payload.get(key) != alignment[key]:
            raise ValueError('Annotation evidence is stale')
    by_id = {r['block_id']: r for r in alignment['rows']}
    source = (root / 'input.md').read_text(encoding='utf-8')
    for key in ('translator_notes', 'figure_text'):
        if not isinstance(payload.get(key, []), list):
            raise ValueError('Annotation collections must be lists')
        for item in payload.get(key, []):
            if (not isinstance(item, dict) or item.get('block_id') not in by_id or item.get('confirmed') is not True
                    or not isinstance(item.get('text'), str) or not item['text'].strip()):
                raise ValueError('Annotations require confirmed text and an aligned source block')
            row = by_id[item['block_id']]
            if not item.get('source_evidence') or item['source_evidence'] not in source[slice(*row['source_span'])]:
                raise ValueError('Annotation evidence must occur in its source block')
            if key == 'figure_text':
                asset = (root / item.get('asset', '')).resolve()
                if not asset.is_relative_to((root / 'images').resolve()) or not asset.is_file() or file_hash(asset) != item.get('asset_sha256'):
                    raise ValueError('Figure text must bind to an unchanged local image')
                if not item.get('original_text') or item.get('method') not in ('manual', 'local_ocr'):
                    raise ValueError('Figure text needs an actual transcription and declared method')
    return payload


class _NamespaceIds(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack, self.parts = [], []

    def handle_starttag(self, tag, attrs):
        scope = self.stack[-1][1] if self.stack else ''
        classes = dict(attrs).get('class', '').split()
        if 'source-column' in classes:
            scope = 'src-'
        elif 'translation-column' in classes:
            scope = 'dst-'
        pairs = []
        for key, value in attrs:
            generated_footnote = bool(value and re.fullmatch(r'fn(?:ref)?\d+(?:-\d+)?', value.lstrip('#')))
            if value and scope and key == 'id' and not generated_footnote:
                value = scope + value
            if value and scope and key == 'href' and value.startswith('#') and not generated_footnote:
                value = '#' + scope + value[1:]
            pairs.append(key if value is None else f'{key}="{html.escape(value, quote=True)}"')
        self.parts.append('<' + tag + (' ' + ' '.join(pairs) if pairs else '') + '>')
        if tag not in ('meta', 'link', 'img', 'br', 'hr', 'input', 'source', 'wbr', 'area', 'base', 'col', 'embed', 'param', 'track'):
            self.stack.append((tag, scope))

    def handle_endtag(self, tag):
        self.parts.append('</' + tag + '>')
        if self.stack and self.stack[-1][0] == tag:
            self.stack.pop()

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.stack and self.stack[-1][0] == tag:
            self.stack.pop()

    def handle_data(self, data): self.parts.append(data)
    def handle_entityref(self, name): self.parts.append('&' + name + ';')
    def handle_charref(self, name): self.parts.append('&#' + name + ';')
    def handle_decl(self, decl): self.parts.append('<!' + decl + '>')


def build_view(temp_dir, annotations=None, allow_draft=False):
    root = Path(temp_dir)
    report = quality.build_quality_report(root)
    if report['quality_status'] == 'blocked' or (report['quality_status'] != 'validated' and not allow_draft):
        raise ValueError('Bilingual view obeys the academic quality gate; --allow-draft is only for needs_review')
    alignment = align(root)
    notes = validate_annotations(root, alignment, json.loads(Path(annotations).read_text(encoding='utf-8'))) if annotations else {}
    source = (root / 'input.md').read_text(encoding='utf-8')
    output_texts = {cid: (root / f'output_{cid}.md').read_text(encoding='utf-8') for cid in alignment['output_hashes']}
    title = 'Bilingual reading view' + (' — DRAFT' if report['quality_status'] != 'validated' else '')
    parts = []
    def footnotes(text, prefix):
        # Derived view only; preserve code/prompt payloads and canonical files.
        _, guard = content_guard.mask_text(text, 'chunk0000')
        frozen = [(s['start'], s['end']) for s in guard['protected_spans']
                  if s['kind'] in ('code_block', 'inline_code', 'prompt_transcript')]
        matches = [m for m in re.finditer(r'\[\^([^\]]+)\]', text)
                   if not any(a <= m.start() < b for a, b in frozen)]
        for match in reversed(matches):
            text = text[:match.start()] + '[^' + prefix + match[1] + ']' + text[match.end():]
        return text
    for row in alignment['rows']:
        parts += [f"::: {{.parallel-row #{row['block_id']}}}", '::: {.source-column}',
                  footnotes(source[slice(*row['source_span'])], 'src-'), ':::', '::: {.translation-column}',
                  footnotes(output_texts[row['chunk_id']][slice(*row['output_span'])], 'dst-'), ':::', ':::']
        for kind in ('translator_notes', 'figure_text'):
            for item in notes.get(kind, []):
                if item['block_id'] == row['block_id']:
                    # Annotation text is data, kept outside the canonical prose.
                    parts.append('<aside>' + html.escape(kind + ': ' + item['text']) + '</aside>')
                    if kind == 'figure_text':
                        parts.append('<aside>' + html.escape('Original image text: ' + item['original_text']) + '</aside>')
    content_guard._atomic_write(root / 'alignment.json', json.dumps(alignment, ensure_ascii=False, indent=2))
    content_guard._atomic_write(root / 'bilingual.md', '\n\n'.join(parts))
    if not merge_and_build.convert_with_pandoc(str(root / 'bilingual.md'), str(root / 'bilingual.html'), title, 'zh-CN', academic=True):
        raise ValueError('Bilingual Pandoc rendering failed')
    path = root / 'bilingual.html'
    parser = _NamespaceIds()
    parser.feed(path.read_text(encoding='utf-8'))
    text = ''.join(parser.parts).replace('</head>', '<style>body{max-width:1200px;margin:1em auto;padding:1.5em;box-sizing:border-box}.parallel-row{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:2em;border-bottom:1px solid #ddd;padding:1em 0}.parallel-row>div{min-width:0;overflow-x:auto}aside{font-size:.85em;color:#555}@media(max-width:700px){.parallel-row{grid-template-columns:1fr}}</style></head>')
    content_guard._atomic_write(path, text)
    return alignment


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('temp_dir')
    p.add_argument('--annotations')
    p.add_argument('--allow-draft', action='store_true')
    a = p.parse_args()
    print(json.dumps(build_view(a.temp_dir, a.annotations, a.allow_draft), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
