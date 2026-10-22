"""Single-writer v3 glossary transactions; legacy ambiguous surfaces stay pending."""
import copy
import json
from pathlib import Path

import glossary
import glossary_v3
import meta
from manifest import file_hash, load_manifest


def prepare(temp_dir):
    root = Path(temp_dir)
    data = glossary.load_glossary(root / 'glossary.json')
    manifest = load_manifest(root)
    tracked = {c['id']: c for c in manifest['chunks']} if manifest else None
    proposals, consumed, malformed = [], {}, []
    for path in sorted(root.glob('output_chunk*.meta.json')):
        cid = meta.chunk_id_from_meta_path(path)
        if tracked is not None and (cid not in tracked or file_hash(root / tracked[cid]['source_file']) != tracked[cid]['source_hash']):
            raise ValueError('Meta source chunk is untracked or differs from its manifest')
        try:
            observation = meta.load_meta(path)
        except (ValueError, OSError) as exc:
            malformed.append({'chunk_id': cid, 'error': str(exc)})
            continue
        digest = meta.meta_content_hash(observation)
        if data.get('applied_meta_hashes', {}).get(cid) == digest:
            continue
        consumed[cid] = digest
        if observation['schema_version'] == 2:
            entries = observation['term_proposals']
        else:
            entries = []
            for entity in observation.get('new_entities', []):
                entries.append({'source': entity['source'], 'target': entity['target_proposal'],
                                'evidence': entity['evidence'], 'action': 'propose'})
            for conflict in observation.get('conflicts', []):
                entries.append({'source': conflict['entity_source'], 'target': conflict['observed_better'],
                                'field': conflict['field'], 'evidence': conflict['evidence'], 'action': 'propose'})
            for alias in observation.get('alias_hypotheses', []):
                entries.append({'source': alias['may_be_alias_of_source'], 'alias': alias['variant'],
                                'evidence': alias['evidence'], 'action': 'alias'})
            for attribute in observation.get('attribute_hypotheses', []):
                entries.append({'source': attribute['entity_source'], 'attribute': attribute['attribute'],
                                'value': attribute['value'], 'evidence': attribute['evidence'], 'action': 'attribute'})
        for entry in entries:
            source = (root / f'{cid}.md').read_text(encoding='utf-8')
            if not entry.get('evidence') or entry['evidence'] not in source:
                raise ValueError(f'{cid} proposal evidence is not present in source')
            candidates = [t['id'] for t in data['terms'] if entry.get('term_id') == t['id'] or
                          (entry.get('source') and entry['source'].casefold() in
                           {s.casefold() for s in [t['source'], *t['aliases']]})]
            proposals.append({'proposal_id': f'{cid}:{len(proposals)}', 'chunk_id': cid,
                              'candidate_term_ids': candidates, 'status': 'ambiguous' if len(candidates) > 1 else 'pending',
                              'proposal': entry})
    return {'schema_version': 3, 'base_glossary_hash': glossary.glossary_hash(data),
            'consumed_meta_hashes': consumed, 'proposals': proposals, 'malformed': malformed}


def apply(temp_dir, decisions):
    root = Path(temp_dir)
    existing = glossary.load_glossary(root / 'glossary.json')
    expected = decisions.get('consumed_meta_hashes', {})
    if expected and all(existing.get('applied_meta_hashes', {}).get(cid) == digest
                        and meta.meta_content_hash(meta.load_meta(root / f'output_{cid}.meta.json')) == digest
                        for cid, digest in expected.items()):
        return {'status': 'already_applied', 'consumed_chunk_ids': sorted(expected)}
    current = prepare(root)
    if (decisions.get('base_glossary_hash') != current['base_glossary_hash']
            or decisions.get('consumed_meta_hashes') != current['consumed_meta_hashes']):
        raise ValueError('Stale v3 merge transaction; prepare again')
    edits = decisions.get('decisions', [])
    if not isinstance(edits, list):
        raise ValueError('v3 decisions must be a list')
    by_id = {p['proposal_id']: p for p in current['proposals']}
    if len(edits) != len(by_id) or {e.get('proposal_id') for e in edits} != set(by_id):
        raise ValueError('Every pending v3 proposal requires exactly one explicit decision')
    data = copy.deepcopy(glossary.load_glossary(root / 'glossary.json'))
    for edit in edits:
        proposal = by_id[edit['proposal_id']]
        choice = edit.get('choice')
        if choice == 'skip':
            continue
        if choice not in ('update', 'new_term'):
            raise ValueError('Unknown v3 merge choice')
        if choice == 'new_term':
            term = copy.deepcopy(edit.get('term'))
            if not isinstance(term, dict) or term.get('locked') is not False:
                raise ValueError('New observed term requires an explicit unlocked v3 term')
            if not isinstance(term.get('source'), str) or not any(glossary_v3._positions(proposal['proposal']['evidence'], term['source'])):
                raise ValueError('A new source term must occur verbatim in the proposal evidence; do not invent qualified surfaces')
            data['terms'].append(term)
        else:
            tid = edit.get('term_id')
            if proposal['proposal'].get('term_id') and proposal['proposal']['term_id'] != tid:
                raise ValueError('Decision must preserve the explicit proposed term ID')
            term = next((t for t in data['terms'] if t['id'] == tid), None)
            if term is None or term['locked']:
                raise ValueError('Unknown or manually locked v3 term; no automatic edit is permitted')
            if not any(any(glossary_v3._positions(proposal['proposal']['evidence'], surface))
                       for surface in [term['source'], *term['aliases']]):
                raise ValueError('Term proposal evidence does not contain the resolved term surface')
            if proposal['candidate_term_ids'] and tid not in proposal['candidate_term_ids']:
                raise ValueError('Decision does not resolve the proposed source/term identity')
            patch = edit.get('patch')
            if not isinstance(patch, dict) or not set(patch) <= {'target', 'aliases', 'sense', 'semantic_notes', 'translation_policy'}:
                raise ValueError('v3 merge patch has unsupported fields')
            term.update(patch)
        term['provenance'] = {'kind': 'reviewed_proposal', 'chunk_id': proposal['chunk_id'],
                              'evidence': proposal['proposal']['evidence']}
    for cid in current['consumed_meta_hashes']:
        observation = meta.load_meta(root / f'output_{cid}.meta.json')
        for surface in observation.get('used_term_sources', observation.get('used_term_ids', [])):
            matches = [t for t in data['terms'] if (t['id'] == surface if observation['schema_version'] == 2 else
                       surface in [t['source'], *t['aliases']])]
            if len(matches) == 1:
                refs = matches[0].setdefault('evidence_refs', [])
                if cid not in refs:
                    refs.append(cid)
    data.setdefault('applied_meta_hashes', {}).update(current['consumed_meta_hashes'])
    glossary_v3.validate(data)
    glossary.save_glossary(root / 'glossary.json', data)
    return {'status': 'applied', 'consumed_chunk_ids': sorted(current['consumed_meta_hashes'])}
