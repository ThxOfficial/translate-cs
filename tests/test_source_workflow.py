"""Source-first orchestration contracts, not a translation-quality evaluation."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import academic_formats
import merge_and_build
import source_workflow as workflow
from manifest import file_hash


class SourceWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.parent = Path(self.tmp.name)
        # Registration only; these bytes are deliberately not a parser fixture.
        self.source = self.parent / 'paper.pdf'
        self.source.write_bytes(b'%PDF-registration-fixture')
        self.assessment = {'mode': 'direct', 'reader': 'test-reader', 'reason': 'Fits test context',
                           'length_assessment': 'Two declared fixture units',
                           'source_units': ['p1', 'p2'], 'inspected_units': ['p1']}
        self.assessment_file = self.parent / 'assessment.json'

    def prepare(self, mode='direct'):
        self.assessment['mode'] = mode
        self.assessment_file.write_text(json.dumps(self.assessment), encoding='utf-8')
        self.run = workflow.prepare(self.source, self.assessment_file, temp_root=self.parent / 'runs')
        return self.run

    def evidence(self):
        data = {'reviewer': 'test-editor', 'reader': 'test-reader',
                'source_sha256': file_hash(self.source), 'output_sha256': file_hash(self.run / 'output.md'),
                'checks': sorted(workflow.CHECKS), 'findings': [],
                'coverage': [{'unit': 'p1', 'location': 'first paragraph'},
                             {'unit': 'p2', 'location': 'second paragraph'}]}
        if self.assessment['mode'] == 'txt':
            data.update(extraction_checked_against_source=True, text_sha256=file_hash(self.run / 'source.txt'))
        return data

    def review(self, data=None):
        (self.parent / 'review.json').write_text(json.dumps(data or self.evidence()), encoding='utf-8')
        return workflow.record_review(self.run, self.parent / 'review.json')

    def translated(self):
        (self.run / 'output.md').write_text('# 测试译文\n\n该结论不一定成立。\n', encoding='utf-8')

    def fake_pdf(self, root, fmt, lang, force=False):
        (Path(root) / 'book.pdf').write_bytes(b'%PDF-test-renderer')
        return {'path': 'book.pdf', 'status': 'needs_visual_review'}

    def test_prepare_does_not_convert_source_and_resumes_exact_identity(self):
        root = self.prepare()
        self.assertFalse((root / 'input.md').exists())
        self.assertFalse((root / 'source.txt').exists())
        self.assertEqual(self.prepare(), root)
        with self.assertRaisesRegex(ValueError, 'changed'):
            workflow.prepare(self.source, self.assessment_file, target_lang='ja', temp_root=self.parent / 'runs')

    def test_assessment_requires_actual_reason_and_consistent_declared_scope(self):
        self.assessment['inspected_units'] = ['p3']
        with self.assertRaisesRegex(ValueError, 'subset'):
            self.prepare()

    def test_source_first_cannot_mix_with_existing_legacy_run(self):
        root = self.parent / 'runs/paper_temp'
        root.mkdir(parents=True)
        (root / 'manifest.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'mix'):
            self.prepare()

    def test_txt_is_selected_by_model_and_preserves_page_breaks(self):
        self.prepare()
        export = self.parent / 'reader.txt'
        export.write_bytes('First page\n\fSecond page\n'.encode('utf-8'))
        with self.assertRaisesRegex(ValueError, 'select txt'):
            workflow.extract_txt(self.run, export)
        # Start a different run with the model-selected long path.
        self.assessment['mode'] = 'txt'
        self.assessment_file.write_text(json.dumps(self.assessment))
        self.run = workflow.prepare(self.source, self.assessment_file, temp_root=self.parent / 'long')
        workflow.extract_txt(self.run, export)
        self.assertIn('\f', (self.run / 'source.txt').read_text())
        self.assertFalse((self.run / 'input.md').exists())
        with self.assertRaisesRegex(ValueError, 'retained'):
            workflow.extract_txt(self.run, export)

    def test_empty_pdf_extraction_is_not_accepted_as_translation_input(self):
        self.prepare('txt')

        def empty(command, **kwargs):
            Path(command[-1]).write_text('\f\n')
            return mock.Mock(returncode=0, stderr='')

        with mock.patch.object(workflow.shutil, 'which', return_value='pdftotext'), \
                mock.patch.object(workflow.subprocess, 'run', side_effect=empty):
            with self.assertRaisesRegex(ValueError, 'No readable text'):
                workflow.extract_txt(self.run)
        self.assertFalse((self.run / 'source.txt').exists())

    def test_editorial_evidence_requires_scope_current_hashes_and_no_findings(self):
        self.prepare()
        self.translated()
        evidence = self.evidence()
        evidence['coverage'].pop()
        with self.assertRaisesRegex(ValueError, 'every requested'):
            self.review(evidence)
        evidence = self.evidence()
        evidence['findings'] = ['missing caption']
        with self.assertRaisesRegex(ValueError, 'unresolved findings'):
            self.review(evidence)
        evidence = self.evidence()
        evidence['output_sha256'] = 'old-output'
        with self.assertRaisesRegex(ValueError, 'different source/translation'):
            self.review(evidence)

    def test_txt_requires_comparison_with_original_and_current_corrected_text(self):
        self.prepare('txt')
        export = self.parent / 'reader.txt'
        export.write_text('Extracted text')
        workflow.extract_txt(self.run, export)
        # Evidence can describe corrected TXT, without relabeling the raw extraction hash.
        (self.run / 'source.txt').write_text('Corrected from original pages')
        self.translated()
        evidence = self.evidence()
        evidence['extraction_checked_against_source'] = False
        with self.assertRaisesRegex(ValueError, 'original source'):
            self.review(evidence)
        self.review()

    def test_missing_review_blocks_before_any_rendering(self):
        self.prepare()
        self.translated()
        with mock.patch.object(academic_formats, 'build') as renderer:
            report = workflow.build_pdf(self.run, 'Title')
        self.assertEqual(report['status'], 'blocked')
        renderer.assert_not_called()

    def test_edits_to_translation_batches_terms_progress_or_images_invalidate_review(self):
        self.prepare()
        self.translated()
        (self.run / 'images').mkdir()
        names = ['output.md', 'drafts/chunk0001.md', 'terminology.json', 'progress.json', 'images/figure.png']
        for name in names[1:]:
            (self.run / name).write_bytes(b'initial')
        for name in names:
            with self.subTest(name=name):
                self.review()
                path = self.run / name
                previous = path.read_bytes()
                path.write_bytes(previous + b' edited')
                with mock.patch.object(academic_formats, 'build') as renderer:
                    self.assertEqual(workflow.build_pdf(self.run, 'Title')['status'], 'blocked')
                renderer.assert_not_called()
                path.write_bytes(previous)

    def test_changed_original_invalidates_run(self):
        self.prepare()
        self.source.write_bytes(b'changed original')
        with self.assertRaisesRegex(ValueError, 'Original source changed'):
            workflow.load(self.run)

    def test_pdf_remains_pending_until_actual_visual_review_and_inputs_are_explicit(self):
        self.prepare()
        self.translated()
        self.review()
        with mock.patch.object(merge_and_build, 'convert_md_to_html', return_value=True), \
                mock.patch.object(academic_formats, 'build', side_effect=self.fake_pdf):
            report = workflow.build_pdf(self.run, 'Title')
        self.assertEqual(report['status'], 'needs_visual_review')
        old = (self.run / 'book.pdf').read_bytes()
        with mock.patch.object(academic_formats, 'build') as renderer:
            report = workflow.build_pdf(self.run, 'Different title')
        self.assertEqual(report['status'], 'blocked')
        self.assertIn('--rebuild', report['error'])
        self.assertEqual((self.run / 'book.pdf').read_bytes(), old)
        renderer.assert_not_called()

    def test_legacy_cli_cannot_bypass_new_editorial_gate(self):
        self.prepare()
        with mock.patch.object(sys, 'argv', ['merge_and_build.py', '--temp-dir', str(self.run)]), \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as error:
            merge_and_build.main()
        self.assertEqual(error.exception.code, 1)

    def test_change_during_rendering_cannot_be_reported_complete(self):
        self.prepare()
        self.translated()
        self.review()

        def changed(root, *args, **kwargs):
            (Path(root) / 'output.md').write_text('Changed during render')
            return {'status': 'ok', 'path': 'book.pdf'}

        with mock.patch.object(merge_and_build, 'convert_md_to_html', return_value=True), \
                mock.patch.object(academic_formats, 'build', side_effect=changed):
            report = workflow.build_pdf(self.run, 'Title')
        self.assertEqual(report['status'], 'blocked')
        self.assertIn('during rendering', report['error'])


if __name__ == '__main__':
    unittest.main()
