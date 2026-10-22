"""Academic native formats: Pandoc OMML/EPUB and an installed browser PDF engine."""
import json
import os
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import content_guard
from manifest import file_hash

READER = 'markdown-smart+east_asian_line_breaks+tex_math_single_backslash+tex_math_double_backslash'


def math_count(text):
    _, guard = content_guard.mask_text(text, 'chunk0000')
    return sum(s['kind'] in ('inline_math', 'display_math', 'math_environment') for s in guard['protected_spans'])


def validate_package(path, fmt, expected_math=0):
    with zipfile.ZipFile(path) as archive:
        if fmt == 'docx':
            document = ET.fromstring(archive.read('word/document.xml'))
            count = sum(1 for node in document.iter() if node.tag == '{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath')
        else:
            if archive.read('mimetype') != b'application/epub+zip':
                raise ValueError('Invalid EPUB mimetype')
            container = ET.fromstring(archive.read('META-INF/container.xml'))
            package = next(n.attrib['full-path'] for n in container.iter() if n.tag.endswith('rootfile'))
            opf = ET.fromstring(archive.read(package))
            spine = [n.attrib['idref'] for n in opf.iter() if n.tag.endswith('itemref')]
            items = {n.attrib['id']: n.attrib for n in opf.iter() if n.tag.endswith('}item')}
            if not spine or any(i not in items for i in spine):
                raise ValueError('EPUB spine has missing documents')
            count = 0
            for item_id in spine:
                name = (Path(package).parent / items[item_id]['href']).as_posix()
                document = ET.fromstring(archive.read(name))
                count += sum(n.tag == '{http://www.w3.org/1998/Math/MathML}math' for n in document.iter())
        if count < expected_math:
            raise ValueError(f'{fmt} lost native math: expected {expected_math}, found {count}')
    return {'native_math_count': count, 'structural_validation': 'passed', 'visual_validation': 'not_recorded'}


def browser_engine():
    for name in ('chromium', 'chromium-browser', 'google-chrome', 'msedge', 'chrome'):
        executable = shutil.which(name)
        if executable:
            return executable
    for relative in ('Google/Chrome/Application/chrome.exe', 'Microsoft/Edge/Application/msedge.exe'):
        for base in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
            if os.environ.get(base):
                executable = Path(os.environ[base]) / relative
                if executable.is_file():
                    return str(executable)
    raise ValueError('No installed Chromium/Edge engine for PDF; no browser is downloaded automatically')


def print_pdf(html_path, output, engine=None):
    engine = engine or browser_engine()
    output = Path(output).resolve()
    with tempfile.TemporaryDirectory(prefix='academic-pdf-', dir=output.parent) as profile:
        command = [engine, '--headless', '--disable-gpu', '--no-first-run', '--disable-background-networking',
                   '--disable-sync', '--no-pdf-header-footer', '--blink-settings=scriptEnabled=false',
                   '--host-resolver-rules=MAP * ~NOTFOUND', '--user-data-dir=' + profile,
                   '--print-to-pdf=' + str(output), Path(html_path).resolve().as_uri()]
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
        content_guard._atomic_write(str(output) + '.engine.json', json.dumps(
            {'command': command, 'returncode': result.returncode, 'stderr': result.stderr}, ensure_ascii=False, indent=2))
        # Some Windows browser launchers exit before the print subprocess.
        deadline = time.monotonic() + 30
        while result.returncode == 0 and not output.is_file() and time.monotonic() < deadline:
            time.sleep(.2)
        if result.returncode or not output.is_file() or not output.read_bytes().startswith(b'%PDF-'):
            raise ValueError('Browser did not generate a valid PDF; inspect engine log')
    return engine


def word_available():
    if os.name != 'nt':
        return False
    import winreg
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\WINWORD.EXE') as key:
                return Path(winreg.QueryValue(key, None)).is_file()
        except OSError:
            continue
    return False


def build(temp_dir, fmt, lang, force=False, cover=None):
    root = Path(temp_dir)
    output = root / ('book.' + fmt)
    text = (root / 'output.md').read_text(encoding='utf-8')
    expected = math_count(text)
    result = {'status': 'failed', 'path': output.name}
    candidate = None
    try:
        cached = output.is_file() and not force
        if not cached:
            descriptor, name = tempfile.mkstemp(prefix='.book-pending-', suffix='.' + fmt, dir=root)
            os.close(descriptor)
            candidate = Path(name)
            candidate.unlink()
        render_output = output if cached else candidate
        identity = {'schema_version': 1, 'render_policy': 'academic-native-v2',
                    'source_sha256': file_hash(root / 'output.md'), 'lang': lang,
                    'config_sha256': file_hash(root / 'config.txt') if (root / 'config.txt').is_file() else '',
                    'css_sha256': file_hash(Path(__file__).with_name('academic.css')) if fmt != 'docx' else '',
                    'cover_sha256': file_hash(cover) if cover and fmt == 'epub' else ''}
        identity_path = Path(str(output) + '.build.json')
        if cached and (not identity_path.is_file() or json.loads(identity_path.read_text(encoding='utf-8')) != identity):
            raise ValueError('Cached academic format has different build inputs; use --rebuild')
        if fmt == 'pdf':
            if word_available():
                intermediate = build(root, 'docx', lang, force, cover)
                if intermediate['status'] not in ('ok', 'needs_visual_review'):
                    raise ValueError('Native DOCX intermediary failed: ' + intermediate.get('error', 'unknown'))
                engine = shutil.which('powershell')
                command = [engine, '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-File',
                           str(Path(__file__).with_name('word_pdf.ps1')), '-InputPath', str((root / 'book.docx').resolve()),
                           '-OutputPath', str(render_output.resolve())]
                if not cached:
                    process = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
                    content_guard._atomic_write(str(output) + '.engine.json', json.dumps(
                        {'command': command, 'returncode': process.returncode, 'stdout': process.stdout, 'stderr': process.stderr}, indent=2))
                    if process.returncode:
                        raise ValueError('Word PDF export failed: ' + process.stderr)
                result.update(renderer='word-native-omml', intermediary='book.docx')
            else:
                engine = browser_engine()
                if not cached:
                    print_pdf(root / 'book_doc.html', render_output, engine)
                result.update(renderer='chromium-print')
            if not render_output.read_bytes().startswith(b'%PDF-'):
                raise ValueError('Invalid cached PDF')
            result.update(engine=engine, structural_validation='pdf_header_only', visual_validation='not_recorded')
        else:
            executable = shutil.which('pandoc')
            if not executable:
                raise ValueError('Pandoc is required for native mathematical formats')
            log = str(output) + '.pandoc.json'
            if not cached:
                render_source = root / 'output.md'
                if fmt == 'docx':
                    # Pandoc's DOCX writer can discard bare raw align/equation
                    # blocks. Supply an explicit math AST boundary in a derived
                    # rendering copy, preserving the original TeX slice inside.
                    _, guarded = content_guard.mask_text(text, 'chunk0000')
                    normalized = text
                    for span in reversed(guarded['protected_spans']):
                        if span['kind'] == 'math_environment':
                            normalized = normalized[:span['start']] + '\n$$\n' + span['source'] + '\n$$\n' + normalized[span['end']:]
                    render_source = root / 'output.native-math.md'
                    render_source.write_bytes(normalized.encode('utf-8'))
                command = [executable, str(render_source), '--from=' + READER, '--standalone',
                           '--metadata=lang:' + lang, '--resource-path=' + str(root.resolve()), '--log=' + log,
                           '-o', str(render_output)]
                if fmt == 'epub':
                    command += ['--mathml', '--css=' + str(Path(__file__).with_name('academic.css'))]
                    if cover:
                        command += ['--epub-cover-image=' + str(Path(cover).resolve())]
                process = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=180)
                if process.returncode:
                    raise ValueError(process.stderr)
                diagnostics = json.loads(Path(log).read_text(encoding='utf-8'))
                if any(d.get('type') == 'CouldNotConvertTeXMath' for d in diagnostics):
                    raise ValueError('Unsupported mathematics; see Pandoc diagnostics')
            result.update(validate_package(render_output, fmt, expected))
            version = subprocess.run([executable, '--version'], capture_output=True, text=True, check=True).stdout.splitlines()[0]
            result.update(engine=executable, engine_version=version, renderer='pandoc-direct')
        if candidate:
            os.replace(candidate, output)
        content_guard._atomic_write(identity_path, json.dumps(identity, ensure_ascii=False, sort_keys=True))
        import format_review
        visual = format_review.status(root, fmt)
        result.update(status='ok' if visual == 'passed' else 'needs_visual_review',
                      visual_validation=visual, cache_used=cached, file_size_bytes=output.stat().st_size)
    except (OSError, ValueError, subprocess.SubprocessError, zipfile.BadZipFile, ET.ParseError) as exc:
        result['error'] = str(exc)
    finally:
        if candidate and candidate.exists():
            candidate.unlink()
    return result
