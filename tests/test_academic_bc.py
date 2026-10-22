import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import academic_formats
import academic_views
import content_guard
import convert
import glossary
import glossary_v3
import format_review
import latex_ingest
import merge_meta_v3
import meta
import pdf_ingest
import quality
import review_tasks
import run_state
import table_tasks
import task_context
from manifest import file_hash


class AcademicBCTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.parent = Path(self.tmp.name)
        self.source = self.parent / 'source.md'
        self.source.write_bytes((ROOT / 'tests/fixtures/academic/context-book.md').read_bytes()
                                + b'\n\n## Closing\n\nThe policy remains a history-to-action mapping.\n')
        with mock.patch.object(sys, 'argv', ['convert.py', str(self.source), '--profile', 'academic',
                                             '--temp-root', str(self.parent), '--chunk-size', '130',
                                             '--independent-review', '--review-mode', 'full']), contextlib.redirect_stdout(io.StringIO()):
            convert.main()
        self.root = self.parent / 'source_temp'
        self.mapping = json.loads((self.root / 'document_map.json').read_text(encoding='utf-8'))
        self.chunks = [c['id'] for c in self.mapping['chunks']]
        self.text = (self.root / 'input.md').read_text(encoding='utf-8')
        findings = self.root / 'inspection.json'
        ingestion = json.loads((self.root / 'ingestion.json').read_text(encoding='utf-8'))
        findings.write_text(json.dumps({'checks': ingestion['manual_checks_required'], 'findings': []}), encoding='utf-8')
        quality.record_input_review(self.root, findings)
        for cid in self.chunks:
            content_guard.prepare_chunk(self.root, cid)
            (self.root / f'output_{cid}.md').write_bytes((self.root / f'{cid}.md').read_bytes())
        self.refresh()

    def refresh(self):
        for cid in self.chunks:
            task_context.write_context(self.root, cid)
        run_state.record_chunks(self.root, self.chunks, 'translator-test', 'translation-context-test')

    def v3(self, locked=False):
        term = {'id': 'policy-decision', 'source': 'policy', 'target': '策略', 'category': 'technical',
                'aliases': [], 'frequency': 1, 'notes': 'decision mapping', 'gender': 'unknown',
                'confidence': 'high', 'evidence_refs': []}
        result = glossary_v3.migrate({'version': 2, 'terms': [term], 'applied_meta_hashes': {}})
        result['terms'][0]['locked'] = locked
        glossary.save_glossary(self.root / 'glossary.json', result)
        return result

    def test_explicit_migration_keeps_legacy_meaning_and_manual_locks(self):
        data = self.v3(True)
        self.assertEqual(data['terms'][0]['scope'], {'kind': 'document'})
        self.assertEqual(data['terms'][0]['sense'], '')
        self.assertEqual(data['terms'][0]['semantic_notes'], 'decision mapping')
        self.assertEqual(glossary.load_glossary(self.root / 'glossary.json')['version'], 3)

    def test_polysemy_stays_ambiguous_until_exact_occurrence_binding(self):
        data = self.v3()
        second = copy.deepcopy(data['terms'][0])
        second.update(id='policy-access', target='访问规则', sense='access control')
        data['terms'].append(second)
        glossary.save_glossary(self.root / 'glossary.json', data)
        cid = next(b['chunk_id'] for b in self.mapping['blocks'] if 'We define a policy' in self.text[b['source_start']:b['source_end']])
        resolved = glossary_v3.resolve_chunk(self.root, cid, data)
        self.assertTrue(any(o['status'] == 'ambiguous' for o in resolved['occurrences']))
        occurrence = next(o for o in resolved['occurrences'] if o['status'] == 'ambiguous')
        binding = {'schema_version': 1, 'source_sha256': file_hash(self.root / 'input.md'), 'bindings': [
            {'block_id': occurrence['block_id'], 'source_span': occurrence['source_span'], 'term_id': 'policy-decision'}]}
        (self.root / 'term_bindings.json').write_text(json.dumps(binding), encoding='utf-8')
        selected = glossary_v3.resolve_chunk(self.root, cid, data)
        self.assertEqual(selected['occurrences'][0]['term_id'], 'policy-decision')
        binding['bindings'][0]['source_span'][0] += 1
        (self.root / 'term_bindings.json').write_text(json.dumps(binding), encoding='utf-8')
        with self.assertRaises(ValueError):
            glossary_v3.resolve_chunk(self.root, cid, data)

    def test_v3_scopes_do_not_inject_sibling_senses_or_code_hits(self):
        data = self.v3()
        section = next(b['block_id'] for b in self.mapping['blocks'] if self.text[b['source_start']:b['source_end']].startswith('## Probability'))
        data['terms'][0]['scope'] = {'kind': 'section', 'section_id': section}
        glossary.save_glossary(self.root / 'glossary.json', data)
        self.assertFalse(any(glossary_v3.resolve_chunk(self.root, c, data)['terms'] for c in self.chunks))

    def test_v3_hash_ignores_audit_frequency_but_tracks_prompt_fields(self):
        term = self.v3()['terms'][0]
        original = glossary.term_hash(term)
        term.update(frequency=999, notes='audit note', provenance={'kind': 'human'})
        self.assertEqual(original, glossary.term_hash(term))
        term['sense'] = 'changed sense'
        self.assertNotEqual(original, glossary.term_hash(term))

    def test_locked_merge_rolls_back_and_unlocked_replay_is_idempotent(self):
        self.v3(True)
        cid = self.chunks[-1]
        evidence = (self.root / f'{cid}.md').read_text(encoding='utf-8').strip()[:100]
        observation = {'schema_version': 2, 'used_term_ids': [], 'term_proposals': [
            {'term_id': 'policy-decision', 'target': '决策规则', 'evidence': evidence}]}
        meta.save_meta(self.root / f'output_{cid}.meta.json', observation)
        proposal = merge_meta_v3.prepare(self.root)
        proposal['decisions'] = [{'proposal_id': proposal['proposals'][0]['proposal_id'],
                                  'choice': 'update', 'term_id': 'policy-decision', 'patch': {'target': '决策规则'}}]
        before = (self.root / 'glossary.json').read_bytes()
        with self.assertRaises(ValueError): merge_meta_v3.apply(self.root, proposal)
        self.assertEqual(before, (self.root / 'glossary.json').read_bytes())
        data = glossary.load_glossary(self.root / 'glossary.json')
        data['terms'][0]['locked'] = False
        glossary.save_glossary(self.root / 'glossary.json', data)
        proposal['base_glossary_hash'] = glossary.glossary_hash(data)
        merge_meta_v3.apply(self.root, proposal)
        self.assertEqual(merge_meta_v3.apply(self.root, proposal)['status'], 'already_applied')

    def test_final_batch_v3_revision_replans_earlier_dependent_chunks_only(self):
        data = self.v3()
        self.refresh()
        cid = self.chunks[-1]
        evidence = (self.root / f'{cid}.md').read_text(encoding='utf-8').strip()[:100]
        meta.save_meta(self.root / f'output_{cid}.meta.json', {
            'schema_version': 2, 'used_term_ids': [],
            'term_proposals': [{'term_id': 'policy-decision', 'target': '决策策略', 'evidence': evidence}]})
        proposal = merge_meta_v3.prepare(self.root)
        proposal['decisions'] = [{'proposal_id': proposal['proposals'][0]['proposal_id'], 'choice': 'update',
                                  'term_id': 'policy-decision', 'patch': {'target': '决策策略'}}]
        merge_meta_v3.apply(self.root, proposal)
        result = review_tasks.reconcile(self.root)
        affected = result['plan']['translation_chunk_ids']
        self.assertTrue(affected)
        self.assertTrue(any(chunk != cid for chunk in affected))
        for chunk in affected:
            self.assertIn('policy', (self.root / f'{chunk}.md').read_text(encoding='utf-8').lower())
        self.assertLess(len(affected), len(self.chunks))

    def test_independent_review_requires_distinct_recorded_actor_and_context(self):
        cid = self.chunks[0]
        payload = {'findings': [], 'execution': {'translator_id': 'translator-test',
            'translation_context_id': 'translation-context-test', 'reviewer_id': 'translator-test', 'review_context_id': 'separate'}}
        path = self.root / 'findings.json'
        path.write_text(json.dumps(payload), encoding='utf-8')
        with self.assertRaises(ValueError): quality.record_review(self.root, cid, 'independent', path)
        payload['execution']['reviewer_id'] = 'independent-test'
        path.write_text(json.dumps(payload), encoding='utf-8')
        quality.record_review(self.root, cid, 'independent', path)
        packets = review_tasks.prepare(self.root)
        self.assertEqual(len(packets), len(self.chunks))

    def test_failed_repair_preserves_output_and_consumes_bounded_attempt(self):
        cid = self.chunks[0]
        output = self.root / f'output_{cid}.md'
        old = output.read_bytes()
        draft = self.root / 'bad.md'
        draft.write_text('bad heading structure', encoding='utf-8')
        with self.assertRaises(ValueError):
            review_tasks.repair(self.root, cid, draft, file_hash(output), 'repairer', 'repair-context')
        self.assertEqual(old, output.read_bytes())
        with self.assertRaisesRegex(ValueError, 'limit'):
            review_tasks.repair(self.root, cid, draft, file_hash(output), 'repairer', 'repair-context')

    def test_exact_alignment_and_annotation_hashes(self):
        alignment = academic_views.align(self.root)
        self.assertTrue(alignment['rows'])
        first = alignment['rows'][0]
        annotation = {'schema_version': 1, 'source_sha256': alignment['source_sha256'],
                      'output_hashes': alignment['output_hashes'], 'translator_notes': [
                          {'block_id': first['block_id'], 'text': 'Explicit note', 'confirmed': True,
                           'source_evidence': self.text[slice(*first['source_span'])]}], 'figure_text': []}
        academic_views.validate_annotations(self.root, alignment, annotation)
        annotation['source_sha256'] = 'stale'
        with self.assertRaises(ValueError): academic_views.validate_annotations(self.root, alignment, annotation)

    def test_bilingual_anchor_namespaces_preserve_both_links(self):
        parser = academic_views._NamespaceIds()
        parser.feed('<div class="source-column"><h2 id="a">A</h2><a href="#a">x</a></div>'
                    '<div class="translation-column"><h2 id="a">甲</h2><a href="#a">x</a></div>')
        output = ''.join(parser.parts)
        self.assertIn('id="src-a"', output)
        self.assertIn('href="#dst-a"', output)

    def test_table_row_groups_restore_all_rows_and_fail_before_overwriting(self):
        root = self.parent / 'table'
        root.mkdir()
        source = '| Name | Value |\n|---|---:|\n' + ''.join(f'| Case {n} | {n}.0 |\n' for n in range(7))
        (root / 'chunk0001.md').write_bytes(source.encode())
        groups = table_tasks.prepare(root, 'chunk0001', 3)
        folder = root / 'tasks/chunk0001.table'
        (folder / 'output_header.md').write_bytes((folder / 'header.md').read_bytes())
        for group in groups['groups']:
            (folder / ('output_' + group['id'] + '.md')).write_bytes((folder / (group['id'] + '.md')).read_bytes())
        table_tasks.commit(root, 'chunk0001')
        output = root / 'output_chunk0001.md'
        self.assertEqual(output.read_text(encoding='utf-8'), source)
        old = output.read_bytes()
        group = groups['groups'][0]
        (folder / ('output_' + group['id'] + '.md')).write_text('', encoding='utf-8')
        with self.assertRaises(ValueError): table_tasks.commit(root, 'chunk0001')
        self.assertEqual(old, output.read_bytes())


class AdapterTests(unittest.TestCase):
    def test_marker_and_mineru_probe_contracts_use_explicit_page_scope(self):
        for parser, kit in [('marker', False), ('mineru', False), ('mineru', True)]:
            with self.subTest(parser=parser, kit=kit), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / 'paper.pdf'
                source.write_bytes(b'%PDF-mock')
                calls = []
                def which(name):
                    return None if name == 'mineru-kit' and not kit else '/local/' + name
                def run(command, **kwargs):
                    calls.append(command)
                    self.assertEqual(kwargs['env']['HF_HUB_OFFLINE'], '1')
                    if command[0].endswith('pdfinfo'): output = 'Pages: 4\n'
                    elif '--help' in command: output = '--output_dir --output_format --page_range -p -o -s -e --pages'
                    elif '--version' in command: output = 'local parser test version'
                    else:
                        output = ''
                        (root / 'out/paper.md').write_text('Selected page content', encoding='utf-8')
                    return mock.Mock(returncode=0, stdout=output, stderr='')
                with mock.patch.object(pdf_ingest.shutil, 'which', side_effect=which), \
                     mock.patch.object(pdf_ingest.subprocess, 'run', side_effect=run):
                    _, report = pdf_ingest.parse_pdf(source, root / 'out', parser, (2, 3))
                command = calls[-1]
                self.assertNotIn('--use_llm', command)
                if parser == 'marker': self.assertEqual(command[command.index('--page_range') + 1], '1,2')
                elif kit: self.assertEqual(command[command.index('--pages') + 1], '2-3')
                else:
                    self.assertEqual(command[command.index('-s') + 1], '1')
                    self.assertEqual(command[command.index('-e') + 1], '2')
                self.assertEqual(report['scope'], 'page_subset')

    def test_failed_native_rebuild_preserves_previous_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'output.md').write_text('$x$\n', encoding='utf-8')
            old = root / 'book.docx'
            old.write_bytes(b'previous human-inspected artifact')
            def fail(command, **kwargs):
                Path(command[command.index('-o') + 1]).write_bytes(b'incomplete')
                return mock.Mock(returncode=1, stderr='conversion failed')
            with mock.patch.object(academic_formats.shutil, 'which', return_value='pandoc'), \
                 mock.patch.object(academic_formats.subprocess, 'run', side_effect=fail):
                result = academic_formats.build(root, 'docx', 'zh-CN', force=True)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(old.read_bytes(), b'previous human-inspected artifact')
            self.assertFalse(list(root.glob('.book-pending-*')))

    def test_format_inspection_requires_complete_checks_and_expires_on_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / 'book.pdf'
            artifact.write_bytes(b'%PDF-test')
            self.assertEqual(format_review.status(root, 'pdf'), 'not_recorded')
            evidence = {'artifact_sha256': file_hash(artifact), 'reviewer': 'actual-reviewer',
                        'engine': 'actual-reader', 'inspection_scope': 'all pages',
                        'checks': sorted(format_review.CHECKS['pdf']), 'findings': []}
            path = root / 'evidence.json'
            path.write_text(json.dumps(evidence), encoding='utf-8')
            format_review.record(root, 'pdf', path)
            self.assertEqual(format_review.status(root, 'pdf'), 'passed')
            artifact.write_bytes(b'%PDF-changed')
            self.assertEqual(format_review.status(root, 'pdf'), 'stale')
            with self.assertRaises(ValueError): format_review.record(root, 'pdf', path)

    def test_latex_expands_recursive_includes_but_rejects_cycles_and_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'main.tex').write_text('\\documentclass{article}\n\\input{part}\n', encoding='utf-8')
            (root / 'part.tex').write_text('Theorem source $x=1$.', encoding='utf-8')
            _, expanded, manifest = latex_ingest.expand_project(root)
            self.assertIn('$x=1$', expanded)
            self.assertEqual(len(manifest['files']), 2)
            (root / 'part.tex').write_text('\\input{main}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'cycle'): latex_ingest.expand_project(root)
            (root / 'part.tex').write_text('\\input{../outside}', encoding='utf-8')
            with self.assertRaises(ValueError): latex_ingest.expand_project(root)

    def test_latex_does_not_execute_or_expand_includes_from_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file = root / 'main.tex'
            file.write_text('\\documentclass{article}\n\\begin{verbatim}\n\\input{missing}\n\\end{verbatim}', encoding='utf-8')
            _, source, _ = latex_ingest.expand_project(file)
            self.assertIn('\\input{missing}', source)
            file.write_text('\\documentclass{article}\n\\write18{echo unsafe}', encoding='utf-8')
            with self.assertRaises(ValueError): latex_ingest.expand_project(file)

    def test_poppler_requires_simple_pdf_and_records_full_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / 'file with spaces.pdf'
            pdf.write_bytes(b'%PDF-mock')
            with self.assertRaises(ValueError): pdf_ingest.parse_pdf(pdf, root / 'no', 'poppler')
            def run(command, **kwargs):
                self.assertNotIn('shell', kwargs)
                if command[0].endswith('pdfinfo'):
                    text = 'Pages: 3\n'
                elif '-layout' in command:
                    Path(command[-1]).write_text('Page content', encoding='utf-8')
                    text = ''
                else: text = 'pdftotext help/version'
                return mock.Mock(returncode=0, stdout=text, stderr='')
            with mock.patch.object(pdf_ingest.shutil, 'which', side_effect=lambda n: '/local/' + n), \
                 mock.patch.object(pdf_ingest.subprocess, 'run', side_effect=run):
                _, report = pdf_ingest.parse_pdf(pdf, root / 'out', 'poppler', simple_pdf=True)
            self.assertEqual(report['scope'], 'full_document')
            self.assertEqual(report['page_end'], 3)

    def test_docx_native_math_validation_detects_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'math.docx'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('word/document.xml', '<document xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><m:oMath/></document>')
            self.assertEqual(academic_formats.validate_package(path, 'docx', 1)['native_math_count'], 1)
            with self.assertRaises(ValueError): academic_formats.validate_package(path, 'docx', 2)


if __name__ == '__main__':
    unittest.main()
