"""Opt-in local PDF parser adapters with probes, scope and provenance."""
import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from content_guard import _atomic_write
from manifest import file_hash


def _run(command, log, timeout=600):
    environment = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', MODELSCOPE_OFFLINE='1')
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout, env=environment)
    _atomic_write(log, json.dumps({'command': command, 'returncode': result.returncode,
                                  'stdout': result.stdout, 'stderr': result.stderr}, ensure_ascii=False, indent=2))
    if result.returncode:
        raise ValueError(f'Parser failed; see {log}')
    return result.stdout + result.stderr


def parse_pdf(source, output_dir, parser, pages=None, simple_pdf=False):
    source, out = Path(source).resolve(), Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if parser not in ('marker', 'mineru', 'poppler'):
        raise ValueError('Select an explicit installed local parser: marker, mineru or poppler')
    if parser == 'poppler' and not simple_pdf:
        raise ValueError('Poppler is limited to explicitly declared simple text PDFs; use --simple-pdf')
    kit = shutil.which('mineru-kit') if parser == 'mineru' else None
    executable = kit or shutil.which({'marker': 'marker_single', 'mineru': 'mineru', 'poppler': 'pdftotext'}[parser])
    pdfinfo = shutil.which('pdfinfo')
    if not executable or not pdfinfo:
        raise ValueError('Selected parser and pdfinfo must already be installed; no model downloads are performed')
    info = _run([pdfinfo, str(source)], out / 'pdfinfo.json', 30)
    count = re.search(r'^Pages:\s+(\d+)', info, re.M)
    if not count:
        raise ValueError('Could not establish total PDF page count')
    total = int(count[1])
    start, end = (1, total) if pages is None else pages
    if type(start) is not int or type(end) is not int or not 1 <= start <= end <= total:
        raise ValueError('Page scope lies outside the PDF')
    help_arg = '-h' if parser == 'poppler' else '--help'
    help_text = _run([executable, 'parse', '--help'] if kit else [executable, help_arg], out / 'capabilities.json', 30)
    # Probe versions without installing packages or enabling remote services.
    version_arg = '-v' if parser == 'poppler' else '--version'
    try:
        version = _run([executable, version_arg], out / 'version.json', 30).strip()
    except ValueError:
        version = 'version flag unsupported; capability output retained'
    markdown = out / (source.stem + '.md')
    if markdown.exists():
        raise ValueError('Parser output already exists; use a fresh directory to preserve prior extraction')
    if parser == 'poppler':
        command = [executable, '-layout', '-enc', 'UTF-8', '-f', str(start), '-l', str(end), str(source), str(markdown)]
    else:
        if parser == 'marker':
            if '--output_dir' not in help_text or '--output_format' not in help_text:
                raise ValueError('Installed Marker CLI lacks the required Markdown output capabilities')
            command = [executable, str(source), '--output_dir', str(out), '--output_format', 'markdown']
            if (start, end) != (1, total):
                if '--page_range' not in help_text:
                    raise ValueError('Installed Marker cannot select a page range')
                command += ['--page_range', ','.join(str(n) for n in range(start - 1, end))]
        elif kit:
            if '-o' not in help_text and '--output' not in help_text:
                raise ValueError('MinerU kit does not expose Markdown output in its help')
            command = [executable, 'parse', str(source), '-o', str(markdown)]
            if '--pages' in help_text:
                command += ['--pages', 'all' if (start, end) == (1, total) else f'{start}-{end}']
            elif (start, end) != (1, total):
                raise ValueError('Installed MinerU kit does not expose page selection')
        else:
            for flag in ('-p', '-o', '-s', '-e'):
                if flag not in help_text:
                    raise ValueError('Installed MinerU CLI does not match the probed adapter contract')
            command = [executable, '-p', str(source), '-o', str(out), '-s', str(start - 1), '-e', str(end - 1)]
    _run(command, out / 'execution.json')
    if not markdown.is_file():
        choices = list(out.rglob('*.md'))
        if len(choices) != 1:
            raise ValueError('Parser did not produce one unambiguous Markdown document')
        markdown = choices[0]
    text = markdown.read_text(encoding='utf-8')
    if not text.strip():
        raise ValueError('Parser returned no text; scanned PDFs need an installed layout/OCR parser')
    report = {'schema_version': 1, 'parser': parser, 'engine_path': executable, 'engine_version': version,
              'source_path': str(source), 'source_sha256': file_hash(source),
              'total_pages': total, 'page_start': start, 'page_end': end,
              'scope': 'full_document' if (start, end) == (1, total) else 'page_subset',
              'markdown_path': str(markdown), 'markdown_sha256': file_hash(markdown),
              'limitations': ['reading order, equations, tables and image completeness require source inspection'],
              'network': 'no cloud API invoked by this adapter'}
    _atomic_write(out / 'parser_report.json', json.dumps(report, ensure_ascii=False, indent=2))
    return markdown, report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source')
    p.add_argument('--output-dir', required=True)
    p.add_argument('--parser', choices=['marker', 'mineru', 'poppler'], required=True)
    p.add_argument('--simple-pdf', action='store_true')
    p.add_argument('--pages', help='Inclusive one-based START:END; produces an isolated page subset')
    a = p.parse_args()
    pages = tuple(map(int, a.pages.split(':'))) if a.pages else None
    _, report = parse_pdf(a.source, a.output_dir, a.parser, pages, a.simple_pdf)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
