"""Bounded local LaTeX project import via Pandoc without executing TeX."""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import content_guard
from manifest import file_hash


def inside(root, path):
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError(f'Project dependency is missing or outside the root: {path}')
    return resolved


def expand_project(project, main_file=None):
    project = Path(project).resolve()
    root = project.parent if project.is_file() else project
    if project.is_file():
        main = project
    elif main_file:
        main = inside(root, root / main_file)
    else:
        choices = [p for p in root.rglob('*.tex') if re.search(r'\\documentclass\b', p.read_text(encoding='utf-8'))]
        if len(choices) != 1:
            raise ValueError('Ambiguous LaTeX main file; specify --main-file')
        main = choices[0]
    files, assets, bibs, macros, active, literals = {}, {}, {}, [], [], {}

    def visit(path):
        path = inside(root, path)
        if path in active:
            raise ValueError('Recursive LaTeX include cycle')
        active.append(path)
        text = path.read_text(encoding='utf-8')
        files[str(path.relative_to(root))] = file_hash(path)
        # Ignore commented commands and literal environments during dependency discovery.
        def literal(match):
            token = f'TEXLITERAL{len(literals)}END'
            literals[token] = match[0]
            return token
        work = re.sub(r'\\begin\{(verbatim|lstlisting|minted)\}.*?\\end\{\1\}', literal, text, flags=re.S)
        work = re.sub(r'\\verb\*?([^\w\s]).*?\1', literal, work)
        work = re.sub(r'(?<!\\)%[^\n]*', '', work)
        if re.search(r'\\(?:write18|openout|directlua|ShellEscape|write)\b|\\input\s*\|', work):
            raise ValueError('Executable TeX directives are unsupported; no shell escape is allowed')
        macros.extend(re.findall(r'\\(?:newcommand|renewcommand|providecommand|newtheorem)\b[^\n]*', work))
        def include(match):
            name = match[1]
            child = path.parent / name
            if not child.suffix:
                child = child.with_suffix('.tex')
            return visit(child)
        if re.search(r'\\(?:input|include)\b', re.sub(r'\\(?:input|include)\s*\{([^{}]+)\}', '', work)):
            raise ValueError('Only braced, literal LaTeX includes are supported')
        def asset(match):
            name = match[2]
            candidate = path.parent / name
            if not candidate.suffix:
                choices = [candidate.with_suffix(ext) for ext in ('.png', '.jpg', '.jpeg', '.pdf', '.svg') if candidate.with_suffix(ext).is_file()]
                if len(choices) != 1:
                    raise ValueError('Graphic extension is missing or ambiguous')
                candidate = choices[0]
            candidate = inside(root, candidate)
            relative = candidate.relative_to(root).as_posix()
            assets[relative] = file_hash(candidate)
            return r'\includegraphics' + (match[1] or '') + '{' + relative + '}'
        work = re.sub(r'\\includegraphics(\[[^\]]*\])?\{([^{}]+)\}', asset, work)
        for match in re.finditer(r'\\(?:bibliography|addbibresource)\{([^{}]+)\}', work):
            for name in match[1].split(','):
                candidate = path.parent / name.strip()
                if not candidate.suffix:
                    candidate = candidate.with_suffix('.bib')
                candidate = inside(root, candidate)
                bibs[str(candidate.relative_to(root))] = file_hash(candidate)
        work = re.sub(r'\\(?:input|include)\s*\{([^{}]+)\}', include, work)
        active.pop()
        return work
    expanded = visit(main)
    for token, value in literals.items():
        if expanded.count(token) != 1:
            raise ValueError('Literal-code guard collision in LaTeX project')
        expanded = expanded.replace(token, value)
    return root, expanded, {'main_file': str(main.relative_to(root)), 'files': files, 'assets': assets,
                             'bibliographies': bibs, 'macro_declarations': macros}


def parse_project(project, output_dir, main_file=None):
    root, source, provenance = expand_project(project, main_file)
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if (out / 'source.md').exists():
        raise ValueError('Use a fresh LaTeX extraction directory')
    pandoc = shutil.which('pandoc')
    if not pandoc:
        raise ValueError('Pandoc is required for LaTeX import')
    # Guard mathematical source slices before Pandoc can expand custom macros.
    spans = []
    for match in re.finditer(r'\\begin\{(verbatim|lstlisting|minted)\}(?:\{[^}]*\})?\n?(.*?)\\end\{\1\}', source, re.S):
        body = match[2]
        width = max([len(m[0]) for m in re.finditer(r'`+', body)] + [2]) + 1
        spans.append((match.start(), match.end(), '\n' + '`' * width + '\n' + body + '\n' + '`' * width + '\n'))
    for match in re.finditer(r'\\verb\*?([^\w\s])(.*?)\1', source):
        if not any(a < match.end() and match.start() < b for a, b, _ in spans):
            ticks = '`' * (max([len(m[0]) for m in re.finditer(r'`+', match[2])] + [0]) + 1)
            spans.append((match.start(), match.end(), ticks + match[2] + ticks))
    for pattern in content_guard._MATH_PATTERNS:
        for match in pattern.finditer(source):
            if not any(a < match.end() and match.start() < b for a, b, _ in spans):
                spans.append((match.start(), match.end(), match[0]))
    nonce = hashlib.sha256(source.encode()).hexdigest()[:16]
    replacements = {}
    masked, cursor = '', 0
    for index, (a, b, text) in enumerate(sorted(spans)):
        token = f'TEXGUARD{nonce}X{index}END'
        if token in source:
            raise ValueError('LaTeX guard collision')
        replacements[token] = text
        masked += source[cursor:a] + token
        cursor = b
    masked += source[cursor:]
    # Make theorem types explicit without inventing automatic TeX counter values.
    types = {'theorem': 'Theorem', 'lemma': 'Lemma', 'definition': 'Definition', 'proof': 'Proof', 'corollary': 'Corollary', 'proposition': 'Proposition'}
    types.update(dict(re.findall(r'\\newtheorem\{([^}]+)\}(?:\[[^\]]*\])?\{([^}]+)\}', masked)))
    for env, title in types.items():
        masked = re.sub(r'\\begin\{' + re.escape(env) + r'\}(?:\[([^\]]+)\])?',
                        lambda m: r'\paragraph{' + title + (' ' + m[1] if m[1] else '') + '}', masked)
        masked = masked.replace(r'\end{' + env + '}', '')
    expanded = out / 'expanded.tex'
    expanded.write_text(masked, encoding='utf-8')
    markdown = out / 'source.md'
    command = [pandoc, str(expanded), '--from=latex-latex_macros', '--to=markdown+raw_tex', '--wrap=none',
               '--resource-path=' + str(root), '--extract-media=' + str(out / 'media'), '-o', str(markdown)]
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=180)
    provenance.update(schema_version=1, parser='pandoc-latex-guarded', command=command, returncode=result.returncode,
                      stderr=result.stderr, project_root=str(root), shell_escape=False)
    if result.returncode:
        raise ValueError('Pandoc LaTeX import failed: ' + result.stderr)
    text = markdown.read_text(encoding='utf-8')
    for token, original in replacements.items():
        if text.count(token) != 1:
            raise ValueError('LaTeX math guard was lost or duplicated during parsing')
        text = text.replace(token, original)
    markdown.write_text(text, encoding='utf-8')
    provenance['markdown_sha256'] = file_hash(markdown)
    content_guard._atomic_write(out / 'project_report.json', json.dumps(provenance, ensure_ascii=False, indent=2))
    return markdown, provenance


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('project')
    p.add_argument('--main-file')
    p.add_argument('--output-dir', required=True)
    a = p.parse_args()
    path, _ = parse_project(a.project, a.output_dir, a.main_file)
    print(path)


if __name__ == '__main__':
    main()
