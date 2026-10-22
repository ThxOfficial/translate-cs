"""Source-first translation: model assessment, optional TXT, editorial review, PDF.

The host model reads/translates/reviews the source; this helper never calls an LLM.
Markdown is used only for the edited translation's typesetting, not PDF ingestion.
"""
import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from content_guard import _atomic_write
from manifest import file_hash


WORKFLOW = 'source-first-v1'
CHECKS = {'coverage', 'terminology', 'math_code', 'tables_figures', 'claims_citations', 'language'}


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _save(path, data):
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _units(value):
    return (isinstance(value, list) and bool(value) and all(_nonempty(v) for v in value)
            and len(set(value)) == len(value))


def _validate_assessment(data):
    if not isinstance(data, dict) or data.get('mode') not in ('direct', 'txt'):
        raise ValueError('Model assessment must select direct or txt')
    if not all(_nonempty(data.get(k)) for k in ('reader', 'reason', 'length_assessment')):
        raise ValueError('Record the actual source reader, length assessment and routing reason')
    if (not _units(data.get('source_units')) or not _units(data.get('inspected_units'))
            or not set(data['inspected_units']).issubset(data['source_units'])):
        raise ValueError('List all requested source units and the subset actually inspected')


def prepare(source, assessment_file, target_lang='zh', temp_root=None):
    source = Path(source).resolve(strict=True)
    if not source.is_file() or not _nonempty(target_lang):
        raise ValueError('A source file and target language are required')
    assessment = _read(assessment_file)
    _validate_assessment(assessment)
    root = (Path(temp_root) if temp_root else Path.cwd()) / (source.stem + '_temp')
    root = root.resolve()
    state = {'schema_version': 1, 'workflow': WORKFLOW,
             'source': {'path': str(source), 'sha256': file_hash(source)},
             'target_lang': target_lang, 'assessment': assessment}
    record = root / 'source_workflow.json'
    if record.is_file():
        if _read(record) != state:
            raise ValueError('Source/assessment/language changed; choose a fresh --temp-root')
        load(root)
        return root
    if root.exists() and any(root.iterdir()):
        raise ValueError('Non-empty run directory; do not mix source-first and legacy runs')
    root.mkdir(parents=True, exist_ok=True)
    (root / 'drafts').mkdir(exist_ok=True)
    _save(record, state)
    return root


def load(root):
    state = _read(Path(root) / 'source_workflow.json')
    if state.get('workflow') != WORKFLOW or state.get('schema_version') != 1:
        raise ValueError('Not a supported source-first run')
    _validate_assessment(state['assessment'])
    if file_hash(state['source']['path']) != state['source']['sha256']:
        raise ValueError('Original source changed; start a new run')
    return state


def extract_txt(root, text_file=None):
    root = Path(root)
    state = load(root)
    if state['assessment']['mode'] != 'txt':
        raise ValueError('TXT extraction requires the model to select txt mode first')
    if ((root / 'source.txt').exists() or (root / 'output.md').exists()
            or any((root / 'drafts').glob('chunk*.md'))):
        raise ValueError('Existing text or translation retained; use a fresh run for re-extraction')
    source = Path(state['source']['path'])
    with tempfile.TemporaryDirectory(prefix='.extract-', dir=root) as work:
        pending = Path(work) / 'source.txt'
        if text_file is not None or source.suffix.lower() == '.txt':
            original = Path(text_file).resolve(strict=True) if text_file is not None else source
            text = original.read_text(encoding='utf-8-sig')
            engine = {'kind': 'provided-txt', 'path': str(original), 'sha256': file_hash(original)}
        elif source.suffix.lower() == '.pdf':
            executable = shutil.which('pdftotext')
            if not executable:
                raise ValueError('No pdftotext installed; provide UTF-8 TXT from an available reader via --text-file')
            command = [executable, '-layout', '-enc', 'UTF-8', str(source), str(pending)]
            process = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                                     errors='replace', timeout=180)
            if process.returncode:
                raise ValueError('TXT extraction failed: ' + process.stderr)
            text = pending.read_text(encoding='utf-8')
            engine = {'kind': 'pdftotext-layout', 'command': command, 'stderr': process.stderr}
        else:
            raise ValueError('Use the host reader to export UTF-8 TXT and pass --text-file')
        if not text.strip():
            raise ValueError('No readable text; inspect original pages/use an available OCR reader, do not translate an empty extraction')
        _atomic_write(root / 'source.txt', text)
        _save(root / 'text_extraction.json', {'schema_version': 1, 'source_sha256': state['source']['sha256'],
                                            'extracted_sha256': file_hash(root / 'source.txt'), 'engine': engine})
    return root / 'source.txt'


def snapshot(root):
    root = Path(root)
    state = load(root)
    paths = [root / 'source_workflow.json', root / 'output.md']
    if not (root / 'output.md').read_text(encoding='utf-8').strip():
        raise ValueError('Edited translation output.md is empty')
    if state['assessment']['mode'] == 'txt':
        if not (root / 'source.txt').read_text(encoding='utf-8').strip():
            raise ValueError('TXT source is empty')
        if _read(root / 'text_extraction.json').get('source_sha256') != state['source']['sha256']:
            raise ValueError('TXT extraction is not bound to this original source')
        paths += [root / 'source.txt', root / 'text_extraction.json']
    paths += sorted((root / 'drafts').glob('chunk*.md'))
    paths += [root / name for name in ('progress.json', 'terminology.json') if (root / name).is_file()]
    paths += sorted(p for p in (root / 'images').rglob('*') if p.is_file())
    for path in paths:
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('Run resources must stay inside the run directory')
    return {str(p.relative_to(root).as_posix()): file_hash(p) for p in paths}


def validate_evidence(state, current, evidence):
    if not isinstance(evidence, dict):
        raise ValueError('Review evidence must be an object')
    if (not all(_nonempty(evidence.get(k)) for k in ('reviewer', 'reader'))
            or not isinstance(evidence.get('checks'), list)
            or set(evidence['checks']) != CHECKS or evidence.get('findings') != []):
        raise ValueError('Actual reviewer/reader, all editorial checks and no unresolved findings are required')
    if (evidence.get('source_sha256') != state['source']['sha256']
            or evidence.get('output_sha256') != current['output.md']):
        raise ValueError('Review evidence is for different source/translation bytes')
    coverage = evidence.get('coverage')
    if (not isinstance(coverage, list) or not coverage
            or any(not isinstance(c, dict) or not _nonempty(c.get('unit'))
                   or not _nonempty(c.get('location')) for c in coverage)):
        raise ValueError('Map every source unit to an inspected translation location')
    units = [c['unit'] for c in coverage]
    if len(set(units)) != len(units) or set(units) != set(state['assessment']['source_units']):
        raise ValueError('Editorial review must cover every requested source unit exactly once')
    if state['assessment']['mode'] == 'txt' and (
            evidence.get('extraction_checked_against_source') is not True
            or evidence.get('text_sha256') != current['source.txt']):
        raise ValueError('TXT reading order, math, figures and omissions must be checked against the original source')


def record_review(root, evidence_file):
    root = Path(root)
    state = load(root)
    evidence = _read(evidence_file)
    current = snapshot(root)
    validate_evidence(state, current, evidence)
    record = {'schema_version': 1, 'workflow': WORKFLOW, 'snapshot': current, 'evidence': evidence}
    _save(root / 'editorial_review.json', record)
    return record


def build_pdf(root, title, rebuild=False):
    root = Path(root)
    report = {'schema_version': 1, 'workflow': WORKFLOW, 'status': 'blocked',
              'formats': {'pdf': {'path': 'book.pdf', 'status': 'not_built'}}}
    try:
        state = load(root)
        current = snapshot(root)
        review = _read(root / 'editorial_review.json')
        if review.get('workflow') != WORKFLOW or review.get('snapshot') != current:
            raise ValueError('Editorial review is stale; check the edited translation against the source again')
        # Revalidate the stored evidence, rather than trusting a status label.
        validate_evidence(state, current, review['evidence'])
        if not _nonempty(title):
            raise ValueError('A translated title is required')
        renderer_files = ['source_workflow.py', 'academic_formats.py', 'merge_and_build.py',
                          'academic.css', 'template.html', 'template_ebook.html', 'word_pdf.ps1']
        identity = {'snapshot': current, 'title': title, 'target_lang': state['target_lang'],
                    'renderer_files': {n: file_hash(Path(__file__).with_name(n)) for n in renderer_files}}
        previous = _read(root / 'build_report.json') if (root / 'build_report.json').is_file() else {}
        if (root / 'book.pdf').exists() and not rebuild and previous.get('identity') != identity:
            raise ValueError('Existing PDF has different inputs; use --rebuild after editorial review')
        report.update(identity=identity, editorial_review='current', status='failed')
        import academic_formats
        import merge_and_build
        lang = merge_and_build.get_lang_config(state['target_lang'])
        # Bind title/language to the existing native renderer cache identity.
        _atomic_write(root / 'config.txt', 'original_title=' + title.replace('\n', ' ') +
                      '\noutput_lang=' + state['target_lang'] + '\n')
        if not merge_and_build.convert_md_to_html(str(root), title, lang, academic=True, force=rebuild):
            raise ValueError('Edited translation could not be typeset; inspect Pandoc diagnostics')
        result = academic_formats.build(root, 'pdf', lang['lang_attr'], force=rebuild)
        report['formats']['pdf'] = result
        if snapshot(root) != current:
            report['status'] = 'blocked'
            raise ValueError('Inputs changed during rendering; recheck and review before rebuilding')
        report['status'] = ('complete' if result['status'] == 'ok' else
                            'needs_visual_review' if result['status'] == 'needs_visual_review' else 'failed')
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        report['error'] = str(exc)
    _save(root / 'build_report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prep = commands.add_parser('prepare', help='Register a model-assessed source without converting it')
    prep.add_argument('source')
    prep.add_argument('--assessment-file', required=True)
    prep.add_argument('--target-lang', default='zh')
    prep.add_argument('--temp-root')
    extract = commands.add_parser('extract-txt', help='Only for a model-selected long TXT run')
    extract.add_argument('temp_dir')
    extract.add_argument('--text-file', help='UTF-8 text exported by the available source reader')
    review = commands.add_parser('record-review')
    review.add_argument('temp_dir')
    review.add_argument('--evidence-file', required=True)
    build = commands.add_parser('build-pdf')
    build.add_argument('temp_dir')
    build.add_argument('--title', required=True)
    build.add_argument('--rebuild', action='store_true')
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            result = {'temp_dir': str(prepare(args.source, args.assessment_file, args.target_lang, args.temp_root))}
        elif args.command == 'extract-txt':
            result = {'text_file': str(extract_txt(args.temp_dir, args.text_file))}
        elif args.command == 'record-review':
            result = record_review(args.temp_dir, args.evidence_file)
        else:
            result = build_pdf(args.temp_dir, args.title, args.rebuild)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get('status') in ('blocked', 'failed') else 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
