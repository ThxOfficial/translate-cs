"""Human/model-run evaluation driver. Never fabricates independent findings.

Prepare writes a real Chinese translation authored for the synthetic source,
then deliberately injects one labeled semantic fault. The self-check records
that fault; fresh independent reviewers do not receive that record or the
reference. Repair/finalize require actual external review findings.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import academic_views
import content_guard
import convert
import document_context
import glossary
import glossary_v3
import quality
import review_tasks
import run_state
import task_context
from manifest import file_hash

PARENT = ROOT / 'tests/.artifacts/semantic-bc'
TEMP = PARENT / 'semantic-evaluation_temp'


def masked(translation, guard):
    result, cursor = '', 0
    for span in guard['protected_spans']:
        start = translation.find(span['source'], cursor)
        if start < 0:
            raise ValueError('Authored sample does not retain an exact protected source slice')
        result += translation[cursor:start] + span['token']
        cursor = start + len(span['source'])
    return result + translation[cursor:]


def prepare():
    PARENT.mkdir(parents=True, exist_ok=True)
    if (TEMP / 'evaluation.json').exists():
        raise ValueError('Evaluation already exists; preserve prior evidence')
    subprocess.run([sys.executable, str(ROOT / 'scripts/convert.py'),
                    str(ROOT / 'tests/fixtures/academic/semantic-evaluation.md'), '--profile', 'academic',
                    '--independent-review', '--review-mode', 'full', '--format', 'md', '--format', 'html',
                    '--temp-root', str(PARENT)], check=True)
    source = (TEMP / 'input.md').read_text(encoding='utf-8')
    reference = (ROOT / 'tests/fixtures/academic/semantic-reference.zh.md').read_text(encoding='utf-8')
    left = convert._structural_blocks_with_offsets(source)
    right = convert._structural_blocks_with_offsets(reference)
    assert len(left) == len(right) and all(a['kind'] == b['kind'] for a, b in zip(left, right))
    translation_by_block = {a['block_id']: b['source_text'] for a, b in zip(left, right)}
    mapping = json.loads((TEMP / 'document_map.json').read_text(encoding='utf-8'))
    context = document_context.prepare_context(TEMP)
    glossary_data = {'version': 3, 'terms': [], 'applied_meta_hashes': {}}
    for tid, surface, target, sense in (
        ('bfs', 'breadth-first search', '广度优先搜索', 'unweighted shortest-hop traversal'),
        ('token-auth', 'token', '令牌', 'authentication credential'),
        ('token-nlp', 'token', '词元', 'NLP discrete text unit')):
        entry = {'id': tid, 'source': surface, 'target': target, 'category': 'technical', 'aliases': [],
                 'sense': sense, 'scope': {'kind': 'document'}, 'translation_policy': 'translate',
                 'locked': False, 'semantic_notes': '', 'provenance': {'kind': 'source_definition'}}
        glossary_data['terms'].append(entry)
    glossary.save_glossary(TEMP / 'glossary.json', glossary_data)
    bindings = []
    for block in mapping['blocks']:
        text = source[block['source_start']:block['source_end']]
        if 'An authentication token' in text:
            hits = list(glossary_v3._positions(text, 'token'))
            assert len(hits) == 2
            for hit, tid in zip(hits, ('token-auth', 'token-nlp')):
                bindings.append({'block_id': block['block_id'], 'term_id': tid,
                                 'source_span': [block['source_start'] + hit.start(), block['source_start'] + hit.end()]})
    (TEMP / 'term_bindings.json').write_text(json.dumps({'schema_version': 1,
        'source_sha256': file_hash(TEMP / 'input.md'), 'bindings': bindings}), encoding='utf-8')
    inspection = TEMP / 'input-inspection.json'
    ingestion = json.loads((TEMP / 'ingestion.json').read_text(encoding='utf-8'))
    inspection.write_text(json.dumps({'checks': ingestion['manual_checks_required'], 'findings': []}), encoding='utf-8')
    quality.record_input_review(TEMP, inspection)
    faulty = None
    for chunk in mapping['chunks']:
        cid = chunk['id']
        content_guard.prepare_chunk(TEMP, cid)
        translated = '\n'.join(translation_by_block[bid] for bid in chunk['block_ids'])
        correct = translated
        if '它不能证明普遍性的改进或因果效应' in translated:
            faulty = cid
            translated = translated.replace('它不能证明普遍性的改进或因果效应', '它足以证明普遍性的改进或因果效应')
        guard = json.loads((TEMP / 'protected' / f'{cid}.json').read_text(encoding='utf-8'))
        draft = TEMP / 'drafts' / f'output_{cid}.masked.md'
        content_guard._atomic_write(draft, masked(translated, guard))
        content_guard._atomic_write(TEMP / 'drafts' / f'{cid}.correct.masked.md', masked(correct, guard))
        content_guard.restore_chunk(TEMP, cid)
        task_context.write_context(TEMP, cid)
    run_state.record_chunks(TEMP, [c['id'] for c in mapping['chunks']], '/root', '/root')
    assert faulty
    for chunk in mapping['chunks']:
        findings = []
        if chunk['id'] == faulty:
            findings = [{'rule_id': 'SEMANTIC-NEGATION', 'severity': 'error', 'status': 'open',
                         'evidence': 'it does not establish a general improvement or a causal effect',
                         'message': 'Labeled fault injection reverses the source negation; candidate requires repair.'}]
        path = TEMP / 'drafts' / f"{chunk['id']}.self.json"
        content_guard._atomic_write(path, json.dumps({'findings': findings}))
        quality.record_review(TEMP, chunk['id'], 'self_check', path)
    evaluation = {'schema_version': 1, 'evaluation_kind': 'authored_model_translation_with_labeled_fault_injection',
                  'translator_id': '/root', 'translation_context_id': '/root', 'seeded_fault_chunk': faulty,
                  'domains': ['cs', 'llm', 'agent', 'math'], 'accuracy_benchmark': False}
    content_guard._atomic_write(TEMP / 'evaluation.json', json.dumps(evaluation, indent=2))
    print(json.dumps({'packets': review_tasks.prepare(TEMP)}, indent=2))


def repair():
    info = json.loads((TEMP / 'evaluation.json').read_text(encoding='utf-8'))
    cid = info['seeded_fault_chunk']
    independent = TEMP / 'drafts' / f'{cid}.independent.json'
    if not independent.is_file():
        raise ValueError('Actual independent findings must exist before repair')
    quality.record_review(TEMP, cid, 'independent', independent)
    review_tasks.repair(TEMP, cid, TEMP / 'drafts' / f'{cid}.correct.masked.md',
                        file_hash(TEMP / f'output_{cid}.md'), '/root', '/root')
    path = TEMP / 'drafts' / f'{cid}.self.json'
    content_guard._atomic_write(path, json.dumps({'findings': []}))
    quality.record_review(TEMP, cid, 'self_check', path)
    print(json.dumps({'repair': cid, 'packets': review_tasks.prepare(TEMP)}, indent=2))


def finalize():
    for chunk in json.loads((TEMP / 'manifest.json').read_text(encoding='utf-8'))['chunks']:
        cid = chunk['id']
        quality.record_review(TEMP, cid, 'independent', TEMP / 'drafts' / f'{cid}.independent.json')
    reconciliation = review_tasks.reconcile(TEMP)
    assert reconciliation['status'] == 'stable'
    report = quality.build_quality_report(TEMP)
    quality.write_quality_report(TEMP, report)
    assert report['quality_status'] == 'validated', report['issues']
    academic_views.build_view(TEMP)
    print(json.dumps({'quality_status': report['quality_status'], 'independent_checks': report['completed_review_count'],
                      'bilingual_rows': len(academic_views.align(TEMP)['rows']), 'accuracy_benchmark': False}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'repair', 'finalize'])
    action = parser.parse_args().action
    {'prepare': prepare, 'repair': repair, 'finalize': finalize}[action]()
