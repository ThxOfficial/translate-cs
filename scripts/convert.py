#!/usr/bin/env python3
"""
convert.py - Convert PDF/DOCX/EPUB to Markdown chunks via Calibre HTMLZ
Combines the original steps 1-2 into a single script.

Markdown input (.md/.markdown) skips Calibre entirely. Use it for PDFs that
Calibre's coordinate-heuristic reflow mangles (formulas, tables, multi-column
layouts): extract them with a layout-aware parser such as MinerU or Marker
first, then pass the resulting Markdown here.
"""

import os
import sys
import subprocess
import zipfile
import shutil
import tempfile
import argparse
import base64
import binascii
import bisect
import glob
import hashlib
import json
import re
import urllib.parse
from pathlib import Path

from manifest import create_manifest, file_hash


def find_calibre_convert():
    """Find ebook-convert command from Calibre installation"""
    possible_paths = [
        "/Applications/calibre.app/Contents/MacOS/ebook-convert",
        "/usr/bin/ebook-convert",
        "/usr/local/bin/ebook-convert",
        "ebook-convert"  # If in PATH
    ]

    for path in possible_paths:
        try:
            result = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                print(f"Found Calibre ebook-convert: {path}")
                return path
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    return None


def convert_to_htmlz(input_file, htmlz_file, calibre_path):
    """Convert input file to HTMLZ using Calibre"""
    try:
        print(f"Converting {input_file} to HTMLZ...")
        cmd = [calibre_path, input_file, htmlz_file]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        if result.returncode == 0:
            file_size = os.path.getsize(htmlz_file)
            print(f"HTMLZ conversion successful: {htmlz_file} ({file_size} bytes)")
            return True
        else:
            print(f"HTMLZ conversion failed: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        print("HTMLZ conversion timed out")
        return False
    except Exception as e:
        print(f"HTMLZ conversion error: {e}")
        return False


def extract_metadata_from_htmlz(extract_dir):
    """Extract metadata from metadata.opf file in HTMLZ"""
    try:
        import xml.etree.ElementTree as ET

        metadata_file = None
        for root, dirs, files in os.walk(extract_dir):
            for file in files:
                if file.lower() == 'metadata.opf':
                    metadata_file = os.path.join(root, file)
                    break
            if metadata_file:
                break

        if not metadata_file:
            return {}

        tree = ET.parse(metadata_file)
        root = tree.getroot()

        namespaces = {
            'opf': 'http://www.idpf.org/2007/opf',
            'dc': 'http://purl.org/dc/elements/1.1/',
            'dcterms': 'http://purl.org/dc/terms/'
        }

        metadata = {}

        title_elem = root.find('.//dc:title', namespaces)
        if title_elem is not None and title_elem.text:
            metadata['title'] = title_elem.text.strip()

        creator_elem = root.find('.//dc:creator', namespaces)
        if creator_elem is not None and creator_elem.text:
            metadata['creator'] = creator_elem.text.strip()

        publisher_elem = root.find('.//dc:publisher', namespaces)
        if publisher_elem is not None and publisher_elem.text:
            metadata['publisher'] = publisher_elem.text.strip()

        language_elem = root.find('.//dc:language', namespaces)
        if language_elem is not None and language_elem.text:
            metadata['language'] = language_elem.text.strip()

        return metadata

    except Exception as e:
        print(f"Warning: Error extracting metadata: {e}")
        return {}


def extract_htmlz(htmlz_file, temp_dir):
    """Extract HTMLZ file and return paths to HTML and images"""
    try:
        with zipfile.ZipFile(htmlz_file, 'r') as zip_file:
            zip_file.extractall(temp_dir)

        html_file = None
        images_dir = None

        for root, dirs, files in os.walk(temp_dir):
            for file in files:
                if file.lower() in ['index.html', 'index.htm']:
                    html_file = os.path.join(root, file)
                    break
            for dir_name in dirs:
                if dir_name.lower() in ['images', 'image', 'pics', 'pictures']:
                    images_dir = os.path.join(root, dir_name)
                    break

        if not html_file:
            for root, dirs, files in os.walk(temp_dir):
                for file in files:
                    if file.lower().endswith(('.html', '.htm')):
                        html_file = os.path.join(root, file)
                        break
                if html_file:
                    break

        return html_file, images_dir

    except Exception as e:
        print(f"Error extracting HTMLZ: {e}")
        return None, None


def build_temp_dir(input_file, temp_root=None):
    """Return the working directory path for an input file.

    Default is the historical cwd-local {book_name}_temp/. When temp_root is
    provided, only the root changes; the leaf directory name stays compatible.
    """
    base_name = os.path.splitext(os.path.basename(input_file))[0]
    leaf = f"{base_name}_temp"
    if temp_root:
        return os.path.join(temp_root, leaf)
    return leaf


SOURCE_FINGERPRINT_FILE = "source_fingerprint.json"

# Files whose presence means the temp dir carries conversion state derived
# from some source book — and must therefore be tied to the current one.
_SOURCE_CACHE_MARKERS = (
    "input.html",
    "input.md",
    "manifest.json",
    "run_state.json",
    "glossary.json",
    "output.md",
)


def source_fingerprint(input_file):
    """Stable identity of the exact source bytes being converted."""
    return {
        "path": os.path.realpath(input_file),
        "size": os.path.getsize(input_file),
        "sha256": file_hash(input_file),
    }


def _write_source_fingerprint(temp_dir, fingerprint):
    os.makedirs(temp_dir, exist_ok=True)
    path = os.path.join(temp_dir, SOURCE_FINGERPRINT_FILE)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(fingerprint, f, indent=2, sort_keys=True)
        f.write('\n')


def _load_source_fingerprint(temp_dir):
    path = os.path.join(temp_dir, SOURCE_FINGERPRINT_FILE)
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _has_reusable_source_cache(temp_dir):
    if not os.path.isdir(temp_dir):
        return False
    for name in _SOURCE_CACHE_MARKERS:
        if os.path.exists(os.path.join(temp_dir, name)):
            return True
    for pattern in ('chunk*.md', 'output_chunk*.md'):
        if glob.glob(os.path.join(temp_dir, pattern)):
            return True
    return False


def check_source_cache(temp_dir, current_fingerprint):
    """Compare the temp dir's cached source identity against the current input.

    Returns (status, message):
      (None, None)        — fresh temp dir, or fingerprint matches; proceed.
      ('adopt', message)  — cache predates fingerprinting; adopt it and record
                            the current fingerprint (trust-on-first-use, keeps
                            pre-upgrade temp dirs resumable).
      ('mismatch', message) — cache was built from different source bytes;
                              the caller must abort.

    Only content identity (sha256 + size) is compared — moving or renaming the
    source file does not invalidate the cache.
    """
    if not _has_reusable_source_cache(temp_dir):
        return None, None

    stored = _load_source_fingerprint(temp_dir)
    if stored is None:
        return 'adopt', (
            f"{temp_dir}/ contains cached conversion artifacts without a "
            f"{SOURCE_FINGERPRINT_FILE} (created by an older version). "
            f"Assuming they were built from the current input file. "
            f"If you replaced the source file, delete {temp_dir}/ and re-run."
        )

    for key in ("sha256", "size"):
        if stored.get(key) != current_fingerprint.get(key):
            return 'mismatch', (
                f"{temp_dir}/ was created from different source bytes "
                f"(cached sha256 {str(stored.get('sha256', ''))[:12]}..., "
                f"current {current_fingerprint['sha256'][:12]}...). "
                f"Reusing its chunks would translate the wrong book."
            )
    return None, None


def _abort_on_source_cache_mismatch(status, message, temp_dir):
    if status == 'mismatch':
        print(f"Error: {message}")
        print(f"Delete {temp_dir}/ (or use a fresh --temp-root) and re-run.")
        sys.exit(1)
    if status == 'adopt':
        print(f"Warning: {message}")


def setup_temp_directory(input_file, html_file, images_dir, temp_root=None):
    """Setup temp directory with HTML and images"""
    try:
        temp_dir = build_temp_dir(input_file, temp_root)
        os.makedirs(temp_dir, exist_ok=True)

        input_html = os.path.join(temp_dir, "input.html")
        if os.path.exists(input_html):
            print(f"Skipping HTML copy - input.html already exists")
        else:
            shutil.copy2(html_file, input_html)
            print(f"Copied HTML to: {input_html}")

        if images_dir and os.path.exists(images_dir):
            target_images_dir = os.path.join(temp_dir, "images")
            if os.path.exists(target_images_dir):
                print(f"Skipping images copy - images directory already exists")
            else:
                shutil.copytree(images_dir, target_images_dir)
                print(f"Copied images to: {target_images_dir}")

        return temp_dir
    except Exception as e:
        print(f"Error setting up temp directory: {e}")
        return None


def convert_html_to_markdown(html_file, md_file, strip_page_numbers=False):
    """Convert HTML to Markdown using pandoc"""
    try:
        import pypandoc

        pypandoc.convert_file(
            html_file,
            'markdown',
            outputfile=md_file,
            extra_args=['--wrap=none']
        )

        if os.path.exists(md_file):
            with open(md_file, 'r', encoding='utf-8') as f:
                content = f.read()

            content = content.replace('\ufeff', '')
            content = content.replace('\u00a0', ' ')
            content = clean_calibre_markers(content, strip_page_numbers=strip_page_numbers)

            with open(md_file, 'w', encoding='utf-8') as f:
                f.write(content)

            print(f"Markdown conversion successful: {md_file}")
            return True
        else:
            print("Markdown file was not created")
            return False
    except ImportError:
        print("pypandoc not found. Install with: pip install pypandoc")
        return False
    except Exception as e:
        print(f"HTML to Markdown conversion failed: {e}")
        return False


_PAGE_SEQUENCE_MIN_LENGTH = 4
_PAGE_SEQUENCE_MIN_RATIO = 0.5


def _detect_page_number_lines(lines):
    """Detect standalone-digit lines that form a monotonic page-number sequence.

    Returns a set of line indices that should be dropped as page numbers.

    Algorithm: collect every standalone-digit line in document order, find the
    Longest Non-Decreasing Subsequence (LNDS) of their integer values via
    bisect_right with parent-pointer reconstruction. If the LNDS is long enough
    and covers a large enough fraction of all standalone digits, treat those
    elements as page numbers. Outliers (years like 1984, chapter numbers,
    citation indices) sit off the monotonic spine and stay preserved.
    """
    digit_indices = []
    digit_values = []
    for i, line in enumerate(lines):
        s = line.strip()
        if s.isdigit():
            digit_indices.append(i)
            digit_values.append(int(s))

    n = len(digit_values)
    if n < _PAGE_SEQUENCE_MIN_LENGTH:
        return set()

    tails = []
    tails_idx = []
    parents = [-1] * n

    for i, v in enumerate(digit_values):
        pos = bisect.bisect_right(tails, v)
        if pos > 0:
            parents[i] = tails_idx[pos - 1]
        if pos == len(tails):
            tails.append(v)
            tails_idx.append(i)
        else:
            tails[pos] = v
            tails_idx[pos] = i

    lnds = []
    cur = tails_idx[-1]
    while cur != -1:
        lnds.append(cur)
        cur = parents[cur]
    lnds.reverse()

    if len(lnds) < _PAGE_SEQUENCE_MIN_LENGTH:
        return set()
    if len(lnds) / n < _PAGE_SEQUENCE_MIN_RATIO:
        return set()

    return {digit_indices[i] for i in lnds}


def clean_calibre_markers(content, strip_page_numbers=False):
    """Clean up Calibre-specific markers from markdown content.

    Standalone digit lines are handled in two layers:
      1. If a line is adjacent to Calibre noise (::: fence, .ct}/.cn} marker),
         drop it — clearly leftover.
      2. Otherwise, run LNDS over all standalone digits to detect a monotonic
         page-number sequence and drop those. Outliers like years (1984),
         chapter numbers, and citation indices stay preserved.

    Pass strip_page_numbers=True to bypass both layers and aggressively delete
    every standalone-digit line (legacy behavior).
    """
    content = re.sub(r'\{\.calibre[^}]*\}', '', content)
    content = re.sub(r'\(#calibre_link-\d+\)', '', content)

    # Clean heading calibre attribute blocks: {#calibre_link-N .calibreN}
    content = re.sub(r'\s*\{#calibre_link-\d+[^}]*\}', '', content)

    # Clean [**text**] format to **text**
    content = re.sub(r'\[\*\*([^*]+)\*\*\]', r'**\1**', content)

    lines = content.split('\n')

    page_number_lines = set() if strip_page_numbers else _detect_page_number_lines(lines)

    def is_calibre_noise(line):
        s = line.strip()
        if not s:
            return False
        if s.startswith(':::'):
            return True
        if s.endswith('.ct}') or s.endswith('.cn}'):
            return True
        return False

    def prev_nonblank(idx):
        for j in range(idx - 1, -1, -1):
            if lines[j].strip():
                return lines[j]
        return None

    def next_nonblank(idx):
        for j in range(idx + 1, len(lines)):
            if lines[j].strip():
                return lines[j]
        return None

    cleaned_lines = []
    for i, line in enumerate(lines):
        stripped_line = line.strip()

        if stripped_line.startswith(':::'):
            continue
        if stripped_line.endswith('.ct}') or stripped_line.endswith('.cn}'):
            continue

        if re.match(r'^\s*\d+\s*$', line):
            if strip_page_numbers:
                continue
            if i in page_number_lines:
                continue
            prev = prev_nonblank(i)
            nxt = next_nonblank(i)
            if (prev is not None and is_calibre_noise(prev)) or \
               (nxt is not None and is_calibre_noise(nxt)):
                continue
            # else: preserve as real content

        cleaned_lines.append(line)

    content = '\n'.join(cleaned_lines)
    content = re.sub(r'\n{3,}', '\n\n', content)
    return content


# =============================================================================
# Markdown input: pre-extracted Markdown (e.g. from MinerU / Marker) skips Calibre
# =============================================================================

MARKDOWN_EXTENSIONS = ('.md', '.markdown')
CALIBRE_CONVERSION_METHOD = "calibre_htmlz"
MARKDOWN_CONVERSION_METHOD = "markdown"

_MD_IMG_RE = re.compile(r'(!\[[^\]]*\]\(\s*)(<[^>]+>|[^)\s]+)')
_HTML_IMG_RE = re.compile(r'(<img\b[^>]*?\bsrc\s*=\s*)(["\'])(.*?)\2', re.IGNORECASE | re.DOTALL)
_DATA_URI_RE = re.compile(r'^data:image/([A-Za-z0-9.+-]+);base64,(.*)$', re.DOTALL)
_URI_SCHEME_RE = re.compile(r'^[A-Za-z][A-Za-z0-9+.-]*:')
_DATA_URI_EXTENSIONS = {
    "jpeg": ".jpg", "jpg": ".jpg", "png": ".png", "gif": ".gif",
    "webp": ".webp", "svg+xml": ".svg", "bmp": ".bmp",
}

_FRONT_MATTER_RE = re.compile(r'\A---[ \t]*\n(.*?)\n(?:---|\.\.\.)[ \t]*(?:\n|\Z)', re.DOTALL)
_FRONT_MATTER_KEYS = {
    "title": "title",
    "author": "creator",
    "creator": "creator",
    "publisher": "publisher",
    "lang": "language",
    "language": "language",
}


def _unquote_yaml_scalar(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


def split_front_matter(content):
    """Split a leading YAML front matter block off content.

    Returns (metadata, body). Only flat `key: value` scalars and `- item`
    lists for the keys in _FRONT_MATTER_KEYS are read; anything fancier is
    ignored. A leading `---` block without any `key:` line is left alone,
    since it is more likely a thematic break than metadata.
    """
    match = _FRONT_MATTER_RE.match(content)
    if not match:
        return {}, content
    lines = match.group(1).split('\n')
    if not any(re.match(r'^[A-Za-z_][\w-]*\s*:', line) for line in lines):
        return {}, content

    metadata = {}
    current_key = None
    for line in lines:
        key_match = re.match(r'^([A-Za-z_][\w-]*)\s*:\s*(.*)$', line)
        if key_match:
            key = _FRONT_MATTER_KEYS.get(key_match.group(1).lower())
            value = _unquote_yaml_scalar(key_match.group(2))
            current_key = None
            if key and key not in metadata:
                if value:
                    metadata[key] = value
                else:
                    current_key = key  # value follows as a `- item` list
            continue
        item_match = re.match(r'^\s+-\s+(.+)$', line)
        if current_key and item_match:
            item = _unquote_yaml_scalar(item_match.group(1))
            if item:
                existing = metadata.get(current_key)
                metadata[current_key] = f"{existing}, {item}" if existing else item
    return metadata, content[match.end():]


def _first_h1_title(content):
    """Return the text of the document's first heading if it is level 1.

    Layout parsers put a paper's title first, as `# Title`. If the first
    heading is deeper than H1 it is a section, not the title, so return None.
    """
    in_fence = False
    for line in content.split('\n'):
        stripped = line.strip()
        if stripped.startswith('```'):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        heading = re.match(r'^(#{1,6})\s+(.*?)\s*#*\s*$', stripped)
        if heading:
            if len(heading.group(1)) != 1:
                return None
            title = re.sub(r'\s*\{[^}]*\}$', '', heading.group(2)).strip()
            return title or None
    return None


def markdown_metadata(content):
    """Book metadata for Markdown input: front matter first, then the first H1."""
    metadata, body = split_front_matter(content.replace('﻿', ''))
    if 'title' not in metadata:
        title = _first_h1_title(body)
        if title:
            metadata['title'] = title
    return metadata


def import_markdown(md_path, temp_dir, academic=False, ingestion_details=None):
    """Return Markdown content ready for chunking, with images moved under temp_dir/images/.

    Relative image paths are resolved against the Markdown file's directory
    and copied in; base64 data URIs (MinerU inlines images that way) are
    decoded to files so chunks handed to translators never carry image
    payloads. Remote URLs stay untouched. YAML front matter is dropped from
    the body — its metadata goes to config.txt via markdown_metadata().
    """
    with open(md_path, 'r', encoding='utf-8', newline='' if academic else None) as f:
        content = f.read()
    if academic:
        transformations = []
        if content.startswith('\ufeff'):
            content = content[1:]
            transformations.append({"kind": "leading_bom_removed", "count": 1})
        crlf_count = content.count('\r\n')
        bare_cr_count = content.count('\r') - crlf_count
        if crlf_count or bare_cr_count:
            content = content.replace('\r\n', '\n').replace('\r', '\n')
            transformations.append({
                "kind": "line_endings_normalized_to_lf",
                "crlf_count": crlf_count,
                "bare_cr_count": bare_cr_count,
            })
        if ingestion_details is not None:
            ingestion_details.setdefault("transformations", []).extend(transformations)
            ingestion_details["preserved_nbsp_count"] = content.count('\xa0')
    else:
        # Keep generic-profile behavior for existing users.
        content = content.replace('\ufeff', '').replace('\xa0', ' ')
    before_front_matter = content
    metadata, content = split_front_matter(content)
    if academic and ingestion_details is not None and content != before_front_matter:
        ingestion_details.setdefault("transformations", []).append({
            "kind": "front_matter_extracted",
            "metadata_keys": sorted(metadata),
            "source_sha256": hashlib.sha256((before_front_matter[:len(before_front_matter) - len(content)]).encode('utf-8')).hexdigest(),
        })

    md_dir = os.path.dirname(os.path.abspath(md_path))
    images_dir = os.path.join(temp_dir, "images")
    refs = {}
    stored = {}
    embedded_count = 0
    resource_rewrites = []

    def store(name, data):
        base, ext = os.path.splitext(name)
        digest = hashlib.sha256(data).hexdigest()
        candidate, n = name, 1
        while candidate in stored and stored[candidate] != digest:
            n += 1
            candidate = f"{base}-{n}{ext}"
        if candidate not in stored:
            os.makedirs(images_dir, exist_ok=True)
            with open(os.path.join(images_dir, candidate), 'wb') as out:
                out.write(data)
            stored[candidate] = digest
        return f"images/{candidate}"

    def localize(target):
        nonlocal embedded_count
        raw = target[1:-1] if target.startswith('<') and target.endswith('>') else target
        if raw in refs:
            return refs[raw]
        new_ref = target
        data_uri = _DATA_URI_RE.match(raw)
        if data_uri:
            try:
                data = base64.b64decode(data_uri.group(2))
            except (binascii.Error, ValueError):
                data = b''
            if data:
                subtype = data_uri.group(1).lower()
                ext = _DATA_URI_EXTENSIONS.get(subtype, '.' + re.sub(r'[^a-z0-9]', '', subtype))
                embedded_count += 1
                new_ref = store(f"embedded-{embedded_count:04d}{ext}", data)
                resource_rewrites.append({
                    "kind": "embedded_image",
                    "source_reference_sha256": hashlib.sha256(raw.encode('utf-8')).hexdigest(),
                    "localized_reference": new_ref,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size_bytes": len(data),
                })
            else:
                print("Warning: could not decode an embedded data-URI image; left inline")
        elif not _URI_SCHEME_RE.match(raw) and not raw.startswith('#'):
            src = os.path.normpath(os.path.join(md_dir, urllib.parse.unquote(raw)))
            if os.path.isfile(src):
                with open(src, 'rb') as f:
                    data = f.read()
                new_ref = store(os.path.basename(src), data)
                resource_rewrites.append({
                    "kind": "local_image",
                    "source_path": os.path.realpath(src),
                    "source_reference": raw,
                    "localized_reference": new_ref,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size_bytes": len(data),
                })
            else:
                print(f"Warning: image not found next to the Markdown file, reference kept as-is: {raw}")
                resource_rewrites.append({
                    "kind": "missing_local_image",
                    "source_reference": raw,
                    "localized_reference": raw,
                    "status": "unresolved",
                })
        elif _URI_SCHEME_RE.match(raw):
            resource_rewrites.append({
                "kind": "external_image",
                "source_reference": raw,
                "localized_reference": raw,
                "status": "not_fetched",
            })
        else:
            resource_rewrites.append({
                "kind": "fragment_image",
                "source_reference": raw,
                "localized_reference": raw,
                "status": "preserved",
            })
        refs[raw] = new_ref
        return new_ref

    content = _MD_IMG_RE.sub(lambda m: m.group(1) + localize(m.group(2)), content)
    content = _HTML_IMG_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{localize(m.group(3))}{m.group(2)}", content
    )

    if stored:
        print(f"Localized {len(stored)} image(s) into {images_dir}/")
    if academic and ingestion_details is not None:
        ingestion_details["resource_rewrites"] = resource_rewrites
    return content


def import_markdown_input(input_file, input_md, temp_dir, academic=False, ingestion_details=None):
    """Copy a Markdown source into temp_dir/input.md with its images localized."""
    os.makedirs(temp_dir, exist_ok=True)
    content = import_markdown(input_file, temp_dir, academic=academic, ingestion_details=ingestion_details)
    if not content.strip():
        print(f"Error: {input_file} has no content to translate")
        return False
    with open(input_md, 'w', encoding='utf-8', newline='\n' if academic else None) as f:
        f.write(content)
    print(f"Markdown import successful: {input_md}")
    return True


# =============================================================================
# Structural block parsing and chunk splitting (Step 3)
# =============================================================================

_ACADEMIC_ENV_OPEN_RE = re.compile(r'^ {0,3}\\begin\{(equation\*?|align\*?|gather\*?|multline\*?|cases|aligned|split)\}')


def _academic_math_block_end(lines, start_index):
    """Return (end_line, kind) for a display equation starting on this line."""
    line = lines[start_index].strip()
    if line.startswith("$$"):
        if re.search(r'(?<!\\)\$\$.*(?<!\\)\$\$', line):
            return start_index, "math_block"
        for index in range(start_index + 1, len(lines)):
            if re.search(r'(?<!\\)\$\$', lines[index]):
                return index, "math_block"
        raise ValueError(f"Unclosed academic $$ display math starting at line {start_index + 1}")
    if line.startswith(r"\["):
        if re.search(r'(?<!\\)\\\]', line):
            return start_index, "math_block"
        for index in range(start_index + 1, len(lines)):
            if re.search(r'(?<!\\)\\\]', lines[index]):
                return index, "math_block"
        raise ValueError(f"Unclosed academic \\[ display math starting at line {start_index + 1}")
    environment = _ACADEMIC_ENV_OPEN_RE.match(lines[start_index])
    if environment:
        name = environment.group(1)
        closing = re.compile(rf'\\end\{{{re.escape(name)}\}}')
        depth = 1
        for index in range(start_index + 1, len(lines)):
            depth += len(re.findall(rf'\\begin\{{{re.escape(name)}\}}', lines[index]))
            depth -= len(closing.findall(lines[index]))
            if depth == 0:
                return index, "math_block"
        raise ValueError(f"Unclosed academic LaTeX environment {name!r} starting at line {start_index + 1}")
    return None


def parse_structural_blocks(content, academic=False):
    """Parse markdown into structural blocks that should not be split.

    Returns list of (text, block_type) tuples where block_type is one of:
    'heading', 'code_block', 'math_block', 'table', 'list', 'blockquote', 'image', 'paragraph'
    """
    blocks = []
    lines = content.split('\n')
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Fenced code block. Academic mode also recognizes tilde fences and
        # requires a real close so a damaged fence cannot be split as prose.
        fence_open = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if fence_open:
            marker = fence_open.group(1)[0]
            fence_width = len(fence_open.group(1))
            block_lines = [line]
            i += 1
            closed = False
            while i < len(lines):
                block_lines.append(lines[i])
                if re.match(rf'^ {{0,3}}{re.escape(marker)}{{{fence_width},}}[ \t]*$', lines[i]):
                    i += 1
                    closed = True
                    break
                i += 1
            if academic and not closed:
                raise ValueError(f"Unclosed academic code fence starting at line {i - len(block_lines) + 1}")
            blocks.append(('\n'.join(block_lines), 'code_block'))
            continue

        if academic:
            math_end = _academic_math_block_end(lines, i)
            if math_end:
                end, kind = math_end
                blocks.append(('\n'.join(lines[i:end + 1]), kind))
                i = end + 1
                continue

            if line.startswith("    ") or line.startswith("\t"):
                end = i + 1
                while end < len(lines):
                    if lines[end].startswith("    ") or lines[end].startswith("\t"):
                        end += 1
                        continue
                    if lines[end].strip() == "":
                        lookahead = end
                        while lookahead < len(lines) and lines[lookahead].strip() == "":
                            lookahead += 1
                        if lookahead < len(lines) and (lines[lookahead].startswith("    ") or lines[lookahead].startswith("\t")):
                            end = lookahead
                            continue
                    break
                blocks.append(('\n'.join(lines[i:end]), 'code_block'))
                i = end
                continue

        # Display math ($$ ... $$). Lines inside may look like list items or
        # tables, so the block must be kept whole. Like pandoc, never pair
        # delimiters across a blank line — an unclosed $$ must not swallow
        # the rest of the chapter.
        if stripped.startswith('$$'):
            if academic:
                raise ValueError(f"Unclosed academic $$ display math starting at line {i + 1}")
            if len(stripped) >= 4 and stripped.endswith('$$'):
                blocks.append((line, 'math_block'))
                i += 1
                continue
            end = None
            for j in range(i + 1, len(lines)):
                s = lines[j].strip()
                if not s:
                    break
                if s.endswith('$$'):
                    end = j
                    break
            if end is not None:
                blocks.append(('\n'.join(lines[i:end + 1]), 'math_block'))
                i = end + 1
                continue

        # Heading
        if re.match(r'^#{1,6}\s', stripped):
            blocks.append((line, 'heading'))
            i += 1
            continue

        # Blockquote
        if stripped.startswith('>'):
            block_lines = [line]
            i += 1
            while i < len(lines) and (lines[i].strip().startswith('>') or
                                       (lines[i].strip() and not re.match(r'^#{1,6}\s', lines[i].strip())
                                        and not lines[i].strip().startswith('```')
                                        and not lines[i].strip().startswith('|')
                                        and not re.match(r'^[-*+]\s', lines[i].strip())
                                        and not re.match(r'^\d+\.\s', lines[i].strip())
                                        and block_lines[-1].strip().startswith('>'))):
                block_lines.append(lines[i])
                i += 1
            blocks.append(('\n'.join(block_lines), 'blockquote'))
            continue

        # Table (lines starting with |)
        if stripped.startswith('|'):
            block_lines = [line]
            i += 1
            while i < len(lines) and lines[i].strip().startswith('|'):
                block_lines.append(lines[i])
                i += 1
            blocks.append(('\n'.join(block_lines), 'table'))
            continue

        # List (unordered or ordered)
        if re.match(r'^[-*+]\s', stripped) or re.match(r'^\d+\.\s', stripped):
            block_lines = [line]
            i += 1
            while i < len(lines):
                s = lines[i].strip()
                # Continue list: list items, indented continuation, or blank lines within list
                if (re.match(r'^[-*+]\s', s) or re.match(r'^\d+\.\s', s) or
                        (lines[i].startswith('  ') and s) or
                        (s == '' and i + 1 < len(lines) and
                         (re.match(r'^[-*+]\s', lines[i+1].strip()) or
                          re.match(r'^\d+\.\s', lines[i+1].strip()) or
                          lines[i+1].startswith('  ')))):
                    block_lines.append(lines[i])
                    i += 1
                else:
                    break
            blocks.append(('\n'.join(block_lines), 'list'))
            continue

        # Image line (standalone or with surrounding caption)
        if re.match(r'!\[', stripped):
            blocks.append((line, 'image'))
            i += 1
            continue

        # Empty line — just a paragraph separator
        if stripped == '':
            blocks.append((line, 'paragraph'))
            i += 1
            continue

        # Regular paragraph — collect contiguous non-empty, non-special lines
        block_lines = [line]
        i += 1
        while i < len(lines):
            s = lines[i].strip()
            if (s == '' or re.match(r'^ {0,3}(?:`{3,}|~{3,})', lines[i]) or s.startswith('$$') or re.match(r'^#{1,6}\s', s) or
                    (academic and _ACADEMIC_ENV_OPEN_RE.match(lines[i])) or
                    (academic and s.startswith(r"\[")) or
                    (academic and (lines[i].startswith("    ") or lines[i].startswith("\t"))) or
                    s.startswith('>') or s.startswith('|') or
                    re.match(r'^[-*+]\s', s) or re.match(r'^\d+\.\s', s) or
                    re.match(r'!\[', s)):
                break
            block_lines.append(lines[i])
            i += 1
        blocks.append(('\n'.join(block_lines), 'paragraph'))
        continue

    return blocks


def _structural_blocks_with_offsets(content, academic=True):
    """Pair the bounded scanner's blocks with exact Unicode offsets in input.md."""
    lines = content.split('\n')
    line_starts = []
    offset = 0
    for line in lines:
        line_starts.append(offset)
        offset += len(line) + 1

    records = []
    line_index = 0
    for block_index, (text, block_type) in enumerate(parse_structural_blocks(content, academic=academic), 1):
        line_count = text.count('\n') + 1
        last_line = line_index + line_count - 1
        if last_line >= len(lines) or '\n'.join(lines[line_index:last_line + 1]) != text:
            raise ValueError(f"Could not map structural block {block_index} back to input.md")
        start = line_starts[line_index]
        end = line_starts[last_line] + len(lines[last_line])
        records.append({
            "block_id": f"block{block_index:06d}",
            "kind": block_type,
            "source_start": start,
            "source_end": end,
            "source_text": text,
        })
        line_index = last_line + 1

    if line_index != len(lines):
        raise ValueError("Structural scanner did not account for every input.md line")
    return records


def _write_document_map(temp_dir, input_md, chunk_files, block_groups=None):
    """Write a source-slice map for academic Markdown chunks."""
    with open(input_md, 'r', encoding='utf-8', newline='') as handle:
        content = handle.read()
    block_records = _structural_blocks_with_offsets(content, academic=True)

    chunks = []
    cursor = 0
    for order, filename in enumerate(chunk_files, 1):
        path = os.path.join(temp_dir, filename)
        with open(path, 'r', encoding='utf-8', newline='') as handle:
            chunk_text = handle.read()
        start = content.find(chunk_text, cursor) if chunk_text else cursor
        if start < 0:
            raise ValueError(f"Academic chunk is not an exact source slice: {filename}")
        end = start + len(chunk_text)
        if content[start:end] != chunk_text:
            raise ValueError(f"Academic chunk does not match its source slice: {filename}")
        chunks.append({
            "id": os.path.splitext(filename)[0],
            "order": order,
            "source_file": filename,
            "source_start": start,
            "source_end": end,
            "source_sha256": file_hash(path),
            "block_ids": [],
        })
        cursor = end

    block_to_chunk = {}
    if block_groups is not None:
        if len(block_groups) != len(chunks):
            raise ValueError("Academic chunk/block group count mismatch")
        for chunk, group in zip(chunks, block_groups):
            for block_index in group:
                block_to_chunk[block_index] = chunk["id"]
    else:
        for block_index, block in enumerate(block_records):
            for chunk in chunks:
                if chunk["source_start"] <= block["source_start"] and block["source_end"] <= chunk["source_end"]:
                    block_to_chunk[block_index] = chunk["id"]
                    break

    heading_stack = []
    mapped_blocks = []
    for index, block in enumerate(block_records):
        chunk_id = block_to_chunk.get(index)
        heading = re.match(r'^ {0,3}(#{1,6})\s+(.*)$', block["source_text"])
        if heading:
            level = len(heading.group(1))
            title = re.sub(r'\s*\{[^}]*\}\s*$', '', heading.group(2)).strip()
            heading_stack = [item for item in heading_stack if item["level"] < level]
            heading_stack.append({"level": level, "title": title})
        if chunk_id is not None:
            next(chunk for chunk in chunks if chunk["id"] == chunk_id)["block_ids"].append(block["block_id"])
        mapped_blocks.append({
            "block_id": block["block_id"],
            "kind": block["kind"],
            "section_path": [item["title"] for item in heading_stack],
            "source_start": block["source_start"],
            "source_end": block["source_end"],
            "source_sha256": hashlib.sha256(block["source_text"].encode('utf-8')).hexdigest(),
            "chunk_id": chunk_id,
            "page": None,
        })

    payload = {
        "schema_version": 1,
        "source_file": os.path.basename(input_md),
        "source_sha256": file_hash(input_md),
        "coordinate_unit": "python_unicode_codepoint",
        "range_convention": "zero_based_left_closed_right_open",
        "parser": "translate-cs-bounded-markdown-v1",
        "chunks": chunks,
        "blocks": mapped_blocks,
    }
    path = os.path.join(temp_dir, "document_map.json")
    temp_path = path + ".tmp"
    with open(temp_path, 'w', encoding='utf-8', newline='\n') as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write('\n')
    os.replace(temp_path, path)
    return payload


def merge_blocks_to_chunks(blocks, target_size=6000, academic=False, return_groups=False):
    """Merge structural blocks into chunks respecting target_size.

    Prefers to split at heading boundaries. Never splits within a single
    structural block unless the block itself exceeds target_size * 2.
    """
    chunks = []
    current_parts = []
    current_size = 0
    current_indices = []
    chunk_groups = []

    def flush():
        nonlocal current_parts, current_size, current_indices
        if current_parts:
            chunks.append('\n'.join(current_parts))
            chunk_groups.append(current_indices)
            current_parts = []
            current_size = 0
            current_indices = []

    for block_index, (text, btype) in enumerate(blocks):
        block_size = len(text)

        # Generic mode retains historical behavior. Academic mode preserves
        # oversized structural blocks instead of breaking formulas or code.
        if block_size > target_size * 2:
            flush()
            if academic:
                print(f"  WARNING: Oversized {btype} block ({block_size} chars), preserving as one academic chunk")
                chunks.append(text)
                chunk_groups.append([block_index])
                continue
            print(f"  WARNING: Oversized {btype} block ({block_size} chars), force-splitting")
            sub_chunks = _force_split_block(text, target_size)
            chunks.extend(sub_chunks)
            continue

        # Prefer to split at heading boundaries
        if btype == 'heading' and current_size > 0:
            flush()

        # Would adding this block exceed target?
        if current_size + block_size > target_size and current_parts:
            flush()

        current_parts.append(text)
        current_indices.append(block_index)
        current_size += block_size

    flush()
    return (chunks, chunk_groups) if return_groups else chunks


def _force_split_block(text, target_size):
    """Force-split an oversized block by paragraph (empty lines), then by lines.

    For fenced code blocks, each resulting chunk gets proper opening/closing fences
    so it remains valid Markdown.
    """
    stripped = text.strip()
    is_fenced_code = stripped.startswith('```')

    # Extract fence info for code blocks
    fence_opener = ''
    if is_fenced_code:
        first_line = stripped.split('\n', 1)[0]
        fence_opener = first_line  # e.g. "```python"

    # Try splitting by empty lines first (not applicable for code blocks — no empty lines expected)
    if not is_fenced_code:
        paragraphs = re.split(r'\n\n+', text)
        if len(paragraphs) > 1:
            chunks = []
            current = []
            current_size = 0
            for para in paragraphs:
                para_size = len(para)
                if current_size + para_size > target_size and current:
                    chunks.append('\n\n'.join(current))
                    current = [para]
                    current_size = para_size
                else:
                    current.append(para)
                    current_size += para_size
            if current:
                chunks.append('\n\n'.join(current))
            return chunks

    # Split by lines
    lines = text.split('\n')

    # For code blocks, strip the opening and closing fences before splitting content
    if is_fenced_code:
        # Remove opening fence line
        content_lines = lines[1:]
        # Remove closing fence line if present
        if content_lines and content_lines[-1].strip().startswith('```'):
            content_lines = content_lines[:-1]
        lines = content_lines

    chunks = []
    current = []
    current_size = 0
    for line in lines:
        line_size = len(line) + 1
        if current_size + line_size > target_size and current:
            chunks.append('\n'.join(current))
            current = [line]
            current_size = line_size
        else:
            current.append(line)
            current_size += line_size
    if current:
        chunks.append('\n'.join(current))

    # Re-wrap each chunk in fences for code blocks
    if is_fenced_code:
        chunks = [f"{fence_opener}\n{chunk}\n```" for chunk in chunks]

    return chunks


def split_markdown_structured(md_file, temp_dir, target_size=6000, academic=False):
    """Split markdown into structural chunks.

    Returns list of chunk filenames (e.g. ['chunk0001.md', ...]).
    """
    try:
        with open(md_file, 'r', encoding='utf-8', newline='' if academic else None) as f:
            content = f.read()

        blocks = parse_structural_blocks(content, academic=academic)
        block_groups = None
        if academic:
            block_records = _structural_blocks_with_offsets(content, academic=True)
            chunk_texts, block_groups = merge_blocks_to_chunks(
                blocks, target_size, academic=True, return_groups=True
            )
            source_slices = [
                content[block_records[group[0]]["source_start"]:block_records[group[-1]]["source_end"]]
                for group in block_groups
            ]
            if source_slices != chunk_texts:
                raise ValueError("Academic chunker output is not an exact input.md source slice")
            chunk_texts = source_slices
        else:
            chunk_texts = merge_blocks_to_chunks(blocks, target_size, academic=False)

        chunk_files = []
        for i, chunk_text in enumerate(chunk_texts, 1):
            filename = f"chunk{i:04d}.md"
            chunk_file = os.path.join(temp_dir, filename)
            with open(chunk_file, 'w', encoding='utf-8', newline='\n' if academic else None) as f:
                f.write(chunk_text)
            chunk_files.append(filename)

        if academic:
            _write_document_map(temp_dir, md_file, chunk_files, block_groups=block_groups)

        print(f"Split into {len(chunk_files)} chunks")
        for filename in chunk_files:
            filepath = os.path.join(temp_dir, filename)
            size = os.path.getsize(filepath)
            print(f"  {filename}: {size} characters")

        return chunk_files
    except Exception as e:
        print(f"Error splitting markdown: {e}")
        return []


def _find_existing_chunk_files(temp_dir):
    """Find existing chunk source filenames (excluding output_ prefixed), sorted."""
    chunk_files = glob.glob(os.path.join(temp_dir, 'chunk*.md'))
    chunk_files = [os.path.basename(f) for f in chunk_files if not os.path.basename(f).startswith('output_')]
    return sorted(chunk_files)


def create_config_file(temp_dir, input_file, input_lang, output_lang, metadata=None,
                       conversion_method=CALIBRE_CONVERSION_METHOD):
    """Create config.txt file for the pipeline"""
    try:
        config_file = os.path.join(temp_dir, "config.txt")

        config_content = f"""# Translation Configuration
input_file={input_file}
input_lang={input_lang}
output_lang={output_lang}
conversion_method={conversion_method}
"""
        if metadata:
            config_content += f"\n# Book Metadata\n"
            if 'title' in metadata:
                config_content += f"original_title={metadata['title']}\n"
            if 'creator' in metadata:
                config_content += f"creator={metadata['creator']}\n"
            if 'publisher' in metadata:
                config_content += f"publisher={metadata['publisher']}\n"
            if 'language' in metadata:
                config_content += f"source_language={metadata['language']}\n"

        with open(config_file, 'w', encoding='utf-8') as f:
            f.write(config_content)

        print(f"Created config file: {config_file}")
        return True
    except Exception as e:
        print(f"Error creating config file: {e}")
        return False


TRANSLATION_SPEC_VERSION = 1


def _load_translation_spec(temp_dir):
    path = os.path.join(temp_dir, "translation_spec.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        spec = json.load(handle)
    if not isinstance(spec, dict) or spec.get("schema_version") != TRANSLATION_SPEC_VERSION:
        raise ValueError(f"Unsupported translation spec: {path}")
    return spec


def _translation_prompt_hash(profile):
    if profile != "academic":
        return ""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    prompt_files = ("SKILL.md", os.path.join("references", "academic-translation.md"),
                    os.path.join("references", "document-context.md"),
                    os.path.join("references", "advanced-academic.md"), os.path.join("references", "math-domains.md"))
    digest = hashlib.sha256()
    for relative_path in prompt_files:
        path = os.path.join(root, relative_path)
        if not os.path.isfile(path):
            raise ValueError(f"Academic translation instructions are missing: {path}")
        with open(path, "rb") as handle:
            digest.update(relative_path.encode("utf-8") + b"\0" + handle.read())
    return digest.hexdigest()


def _resolve_translation_settings(args, temp_dir):
    existing = _load_translation_spec(temp_dir)
    profile = args.profile or (existing or {}).get("profile") or "generic"
    if profile not in ("generic", "academic"):
        raise ValueError(f"Unsupported translation profile: {profile}")
    settings = {
        "profile": profile,
        "document_kind": args.document_kind or (existing or {}).get("document_kind", "auto"),
        "subjects": args.subjects if args.subjects is not None else (existing or {}).get("subjects", []),
        "source_lang": args.ilang or (existing or {}).get("source_lang", "auto"),
        "target_lang": args.olang or (existing or {}).get("target_lang", "zh"),
        "style": "faithful",
        "term_display": "first_occurrence_bilingual",
        "code_policy": "verbatim",
        "prompt_example_policy": "verbatim",
        "bibliography_policy": "verbatim",
        "translator_notes": "separate",
        "review_mode": args.review_mode or (existing or {}).get("review_mode", "risk_based"),
        "review_sample_rate": args.review_sample_rate if args.review_sample_rate is not None else (existing or {}).get("review_sample_rate", 0.1),
        "review_policy_version": "academic-review-v1" if profile == "academic" else "legacy-v1",
        "requested_formats": args.requested_formats or (existing or {}).get("requested_formats", ["md", "html"]),
        "keep_intermediates": True,
        "custom_instructions": args.custom_instructions if args.custom_instructions is not None else (existing or {}).get("custom_instructions", ""),
        "translation_prompt_sha256": _translation_prompt_hash(profile),
        "chunk_size": args.chunk_size if args.chunk_size is not None else ((existing or {}).get("chunk_size") or 6000),
        "chunking_version": "academic-v1" if profile == "academic" else "generic-v1",
        "translation_prompt_version": "academic-v2" if profile == "academic" else "generic-v1",
        "protection_version": "content-guard-v2" if profile == "academic" else "none",
    }
    if settings["document_kind"] not in ("auto", "paper", "book"):
        raise ValueError("--document-kind must be auto, paper, or book")
    if getattr(args, 'independent_review', False) or (existing or {}).get('require_independent_review', False):
        settings['require_independent_review'] = True
        settings['review_policy_version'] = 'academic-review-v2'
    if settings["review_mode"] not in ("risk_based", "full", "structural_only"):
        raise ValueError("--review-mode must be risk_based, full, or structural_only")
    if isinstance(settings["chunk_size"], bool) or not isinstance(settings["chunk_size"], int) or settings["chunk_size"] <= 0:
        raise ValueError("--chunk-size must be a positive integer")
    if isinstance(settings["review_sample_rate"], bool) or not isinstance(settings["review_sample_rate"], (int, float)) or not 0 < settings["review_sample_rate"] <= 1:
        raise ValueError("--review-sample-rate must be greater than 0 and at most 1")
    if not isinstance(settings["subjects"], list) or not all(isinstance(value, str) for value in settings["subjects"]):
        raise ValueError("subjects must be a list of strings")
    if profile == "academic":
        normalized_subjects = [value.strip().lower() for value in settings["subjects"]]
        allowed_subjects = {"cs", "ai", "llm", "agent", "math"}
        unsupported_subjects = [value for value in normalized_subjects if value not in allowed_subjects]
        if unsupported_subjects:
            raise ValueError("Unsupported academic subject(s): " + ", ".join(unsupported_subjects))
        settings["subjects"] = list(dict.fromkeys(normalized_subjects))
    if not isinstance(settings["requested_formats"], list):
        raise ValueError("requested_formats must be a list")
    if not settings["requested_formats"] or any(value not in ("md", "html", "docx", "epub", "pdf") for value in settings["requested_formats"]):
        raise ValueError("requested_formats must contain one or more supported formats")
    if len(settings["requested_formats"]) != len(set(settings["requested_formats"])):
        raise ValueError("requested_formats must not contain duplicates")
    return settings


def _check_chunking_cache(temp_dir, settings):
    """Reject settings changes that would silently reuse incompatible chunks."""
    if not _find_existing_chunk_files(temp_dir):
        return
    old_spec = _load_translation_spec(temp_dir)
    if old_spec is None:
        if settings["profile"] == "academic":
            raise ValueError(
                "Academic profile cannot reuse legacy chunks without chunking metadata. "
                "Use a fresh --temp-root to preserve the existing run."
            )
        return
    changed = [
        key for key in ("chunking_version", "chunk_size")
        if old_spec.get(key) != settings.get(key)
    ]
    if changed:
        raise ValueError(
            "Chunking settings changed for a cached run (" + ", ".join(changed) +
            "). Use a fresh --temp-root so existing outputs remain recoverable."
        )


def _write_translation_spec(temp_dir, settings):
    path = os.path.join(temp_dir, "translation_spec.json")
    temp_path = path + ".tmp"
    payload = {"schema_version": TRANSLATION_SPEC_VERSION, **settings}
    with open(temp_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temp_path, path)
    return path


def _write_academic_ingestion(temp_dir, input_file, input_md, fingerprint, settings, details):
    unresolved = [
        item for item in details.get("resource_rewrites", [])
        if item.get("status") == "unresolved"
    ]
    report = {
        "schema_version": 1,
        "profile": "academic",
        "original_path": os.path.realpath(input_file),
        "source": fingerprint,
        "normalized_input": {
            "path": os.path.basename(input_md),
            "sha256": file_hash(input_md),
            "char_count": len(Path(input_md).read_text(encoding="utf-8")),
        },
        "parser": {
            "name": "pre-extracted-markdown",
            "version": "translate-cs-bounded-markdown-v1",
            "document_kind": settings["document_kind"],
            "subjects": settings["subjects"],
            "limitations": [
                "This scanner recognizes a bounded Markdown subset; it is not a full CommonMark or Pandoc AST parser.",
                "Reading order, parser omissions, table correctness, and equation completeness require human/model inspection.",
            ],
        },
        "normalizations": details.get("transformations", []),
        "preserved_nbsp_count": details.get("preserved_nbsp_count", 0),
        "resource_rewrites": details.get("resource_rewrites", []),
        "unresolved_resource_count": len(unresolved),
        "manual_checks_required": [
            "reading_order_and_scope",
            "equation_completeness_and_delimiters",
            "table_rows_columns_and_headers",
            "image_targets_and_missing_assets",
        ],
    }
    path = os.path.join(temp_dir, "ingestion.json")
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temp_path, path)
    return report


def _changed_academic_source_resources(ingestion):
    """Find linked local assets whose source bytes changed since import."""
    changed = []
    for resource in ingestion.get("resource_rewrites", []) if isinstance(ingestion, dict) else []:
        if not isinstance(resource, dict) or resource.get("kind") != "local_image":
            continue
        source_path = resource.get("source_path")
        expected_hash = resource.get("sha256")
        if not isinstance(source_path, str) or not expected_hash or not os.path.isfile(source_path):
            continue
        try:
            if file_hash(source_path) != expected_hash:
                changed.append(source_path)
        except OSError:
            changed.append(source_path)
    return changed


def _do_split_and_manifest(temp_dir, input_md, chunk_size, academic=False):
    """Split markdown and create manifest. Returns chunk count or 0 on failure."""
    existing = _find_existing_chunk_files(temp_dir)
    if existing:
        print(f"Skipping markdown splitting - found {len(existing)} existing chunk files")
        # Create/update manifest for existing files
        create_manifest(temp_dir, existing, input_md)
        if academic:
            _write_document_map(temp_dir, input_md, existing)
        return len(existing)

    chunk_files = split_markdown_structured(input_md, temp_dir, chunk_size, academic=academic)
    if not chunk_files:
        return 0
    create_manifest(temp_dir, chunk_files, input_md)
    return len(chunk_files)


def _check_strip_page_numbers_cache_conflict(strip_flag, temp_dir, input_md):
    """Return list of cached files that would silently neutralize --strip-page-numbers.

    The flag only takes effect inside clean_calibre_markers, which runs during
    HTML→Markdown conversion. If input.md or chunk*.md already exist from a
    prior run, both are reused as-is and the flag becomes a no-op. Surface
    that conflict so the user knows to clean up.
    """
    if not strip_flag:
        return []
    if not os.path.isdir(temp_dir):
        return []

    blockers = []
    if os.path.exists(input_md):
        blockers.append(input_md)

    existing_chunks = [
        f for f in glob.glob(os.path.join(temp_dir, 'chunk*.md'))
        if not os.path.basename(f).startswith('output_')
    ]
    if existing_chunks:
        blockers.append(f"{len(existing_chunks)} chunk file(s) under {temp_dir}/")

    return blockers


def _abort_on_strip_cache_conflict(blockers, temp_dir):
    if not blockers:
        return
    print("Error: --strip-page-numbers cannot take effect because cached files exist:")
    for b in blockers:
        print(f"  - {b}")
    print(f"Delete the cached files (or remove the entire {temp_dir}/ directory) and re-run.")
    sys.exit(1)


def main():
    """Main conversion function"""
    parser = argparse.ArgumentParser(
        description="Convert PDF/DOCX/EPUB (via Calibre HTMLZ) or Markdown to markdown chunks"
    )
    parser.add_argument(
        "input_file",
        help="Input file: PDF, DOCX, or EPUB (converted by Calibre), or Markdown (.md/.markdown), "
             "e.g. the output of a layout-aware PDF parser such as MinerU or Marker",
    )
    parser.add_argument("-l", "--ilang", default=None, help="Input language (default: auto)")
    parser.add_argument("--olang", default=None, help="Output language (default: zh)")
    parser.add_argument("--chunk-size", type=int, default=None, help="Target chunk size in characters (default: 6000)")
    parser.add_argument("--profile", choices=("generic", "academic"), default=None,
                         help="Translation profile (default: generic; academic preserves technical structure)")
    parser.add_argument("--document-kind", choices=("auto", "paper", "book"), default=None,
                         help="Academic document type")
    parser.add_argument("--subject", dest="subjects", action="append", default=None,
                         help="Academic subject; repeat for multiple values (cs, ai, llm, agent, math)")
    parser.add_argument("--review-mode", choices=("risk_based", "full", "structural_only"), default=None,
                         help="Requested academic review coverage")
    parser.add_argument("--review-sample-rate", type=float, default=None,
                         help="Risk-based sample rate for non-critical chunks (default: 0.1; at least one chunk)")
    parser.add_argument('--independent-review', action='store_true', help='Require a separate reviewer/context for semantic review')
    parser.add_argument('--pdf-parser', choices=['marker', 'mineru', 'poppler'])
    parser.add_argument('--simple-pdf', action='store_true')
    parser.add_argument('--latex-main', help='Main TeX file relative to a project directory')
    parser.add_argument("--format", dest="requested_formats", choices=("md", "html", "docx", "epub", "pdf"),
                         action="append", default=None, help="Requested output format; repeat as needed")
    parser.add_argument("--custom-instructions", default=None,
                         help="Additional translation instructions included in the run identity")
    parser.add_argument(
        "--temp-root",
        default=None,
        help="Directory under which {book_name}_temp/ will be created (default: current working directory)",
    )
    parser.add_argument(
        "--strip-page-numbers",
        action="store_true",
        help="Aggressively delete every standalone-digit line (legacy behavior). "
             "Default is off: standalone digits are preserved unless adjacent to Calibre noise.",
    )

    args = parser.parse_args()
    input_file = args.input_file

    if (Path(input_file).suffix.lower() == '.tex' or Path(input_file).is_dir()
            or (Path(input_file).suffix.lower() == '.pdf' and args.pdf_parser)):
        # Keep extraction isolated. The recursively invoked Markdown path keeps
        # its established identity; original project/PDF identity remains in the
        # parser report and ingestion attachment, never masquerading as MD bytes.
        import tempfile
        parent = Path(args.temp_root or Path.cwd()).resolve()
        parent.mkdir(parents=True, exist_ok=True)
        extraction = Path(tempfile.mkdtemp(prefix=Path(input_file).stem + '-parsed-', dir=parent))
        if Path(input_file).suffix.lower() == '.pdf':
            import pdf_ingest
            markdown, origin = pdf_ingest.parse_pdf(input_file, extraction, args.pdf_parser, simple_pdf=args.simple_pdf)
        else:
            import latex_ingest
            markdown, origin = latex_ingest.parse_project(input_file, extraction, args.latex_main)
        forwarded = sys.argv[1:]
        forwarded[0] = str(markdown)
        # Remove input-adapter-only options from the normalized Markdown call.
        for flag in ('--pdf-parser', '--latex-main'):
            if flag in forwarded:
                i = forwarded.index(flag)
                del forwarded[i:i + 2]
        if '--simple-pdf' in forwarded:
            forwarded.remove('--simple-pdf')
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), *forwarded], check=False)
        if result.returncode:
            sys.exit(result.returncode)
        imported = Path(build_temp_dir(str(markdown), args.temp_root))
        record = imported / 'ingestion.json'
        if record.is_file():
            payload = json.loads(record.read_text(encoding='utf-8'))
            payload['original_input'] = origin
            from content_guard import _atomic_write
            _atomic_write(record, json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if not os.path.exists(input_file):
        print(f"Error: Input file not found: {input_file}")
        sys.exit(1)

    file_ext = os.path.splitext(input_file)[1].lower()
    is_markdown = file_ext in MARKDOWN_EXTENSIONS
    if file_ext not in ['.pdf', '.docx', '.epub'] and not is_markdown:
        print(f"Error: Unsupported file type: {file_ext}")
        sys.exit(1)
    if is_markdown and args.strip_page_numbers:
        print("Error: --strip-page-numbers only cleans Calibre output; it does not apply to Markdown input.")
        sys.exit(1)

    if is_markdown:
        print("=== Markdown Import (no Calibre) ===")
    else:
        print("=== File Conversion via Calibre HTMLZ ===")
    print(f"Input file: {input_file}")
    if args.temp_root:
        print(f"Temp root: {args.temp_root}")

    htmlz_file = f"{os.path.splitext(input_file)[0]}.htmlz"

    try:
        temp_dir = build_temp_dir(input_file, args.temp_root)
        settings = _resolve_translation_settings(args, temp_dir)
        if args.independent_review:
            settings['require_independent_review'] = True
            settings['review_policy_version'] = 'academic-review-v2'
        print(f"Target chunk size: {settings['chunk_size']} characters")
        if settings["profile"] == "academic" and not is_markdown:
            raise ValueError(
                "The academic profile currently requires layout-aware Markdown input. "
                "Parse the PDF/DOCX/EPUB first, then pass the resulting .md file."
            )
        _check_chunking_cache(temp_dir, settings)
        current_fingerprint = source_fingerprint(input_file)
        _abort_on_source_cache_mismatch(
            *check_source_cache(temp_dir, current_fingerprint), temp_dir=temp_dir
        )

        if is_markdown:
            input_md = os.path.join(temp_dir, "input.md")
            ingestion_details = {"transformations": [], "resource_rewrites": []}
            with open(input_file, 'r', encoding='utf-8') as f:
                metadata = markdown_metadata(f.read())
            if os.path.exists(input_md):
                print("Skipping Markdown import - input.md already exists")
                if settings["profile"] == "academic":
                    ingestion_path = os.path.join(temp_dir, "ingestion.json")
                    if not os.path.isfile(ingestion_path):
                        raise ValueError(
                            "Academic cache has no ingestion.json provenance record. "
                            "Use a fresh --temp-root so source normalization can be audited."
                        )
                    with open(ingestion_path, "r", encoding="utf-8") as handle:
                        previous_ingestion = json.load(handle)
                    if previous_ingestion.get("normalized_input", {}).get("sha256") != file_hash(input_md):
                        raise ValueError("Academic ingestion record does not match input.md; use a fresh --temp-root.")
                    changed_resources = _changed_academic_source_resources(previous_ingestion)
                    if changed_resources:
                        raise ValueError(
                            "Academic source image bytes changed since import: " + ", ".join(changed_resources) +
                            ". Use a fresh --temp-root so existing translations remain recoverable."
                        )
                    ingestion_details = {
                        "transformations": previous_ingestion.get("normalizations", []),
                        "resource_rewrites": previous_ingestion.get("resource_rewrites", []),
                        "preserved_nbsp_count": previous_ingestion.get("preserved_nbsp_count", 0),
                    }
            elif not import_markdown_input(
                input_file, input_md, temp_dir, academic=settings["profile"] == "academic",
                ingestion_details=ingestion_details,
            ):
                sys.exit(1)

            chunk_count = _do_split_and_manifest(
                temp_dir, input_md, settings["chunk_size"], academic=settings["profile"] == "academic"
            )
            if chunk_count == 0:
                sys.exit(1)

            if settings["profile"] == "academic":
                _write_academic_ingestion(
                    temp_dir, input_file, input_md, current_fingerprint, settings, ingestion_details
                )
            create_config_file(temp_dir, input_file, settings["source_lang"], settings["target_lang"], metadata,
                               conversion_method=MARKDOWN_CONVERSION_METHOD)
            if settings["profile"] == "academic" or _load_translation_spec(temp_dir) is not None:
                _write_translation_spec(temp_dir, settings)
            _write_source_fingerprint(temp_dir, current_fingerprint)
            print("Conversion completed successfully!")
            print(f"Temp directory: {temp_dir}")
            print(f"Markdown chunks: {chunk_count} files")
            return

        calibre_path = find_calibre_convert()
        if not calibre_path:
            print("Error: Calibre ebook-convert not found")
            print("Please install Calibre: https://calibre-ebook.com/")
            sys.exit(1)

        input_html_path = os.path.join(temp_dir, "input.html")

        if os.path.exists(input_html_path):
            print(f"Skipping HTMLZ conversion - input.html already exists")

            metadata = {}
            config_file = os.path.join(temp_dir, "config.txt")
            if os.path.exists(config_file):
                try:
                    with open(config_file, 'r', encoding='utf-8') as f:
                        for line in f:
                            if '=' in line:
                                key, value = line.strip().split('=', 1)
                                if key == 'original_title':
                                    metadata['title'] = value
                                elif key == 'creator':
                                    metadata['creator'] = value
                                elif key == 'publisher':
                                    metadata['publisher'] = value
                                elif key == 'source_language':
                                    metadata['language'] = value
                except Exception as e:
                    print(f"Warning: Could not read metadata from config: {e}")

            input_md = os.path.join(temp_dir, "input.md")
            _abort_on_strip_cache_conflict(
                _check_strip_page_numbers_cache_conflict(args.strip_page_numbers, temp_dir, input_md),
                temp_dir,
            )
            if os.path.exists(input_md):
                print(f"Skipping HTML to Markdown conversion - input.md already exists")
            else:
                if not convert_html_to_markdown(input_html_path, input_md, strip_page_numbers=args.strip_page_numbers):
                    sys.exit(1)

            chunk_count = _do_split_and_manifest(
                temp_dir, input_md, settings["chunk_size"], academic=settings["profile"] == "academic"
            )
            if chunk_count == 0:
                sys.exit(1)

            create_config_file(temp_dir, input_file, settings["source_lang"], settings["target_lang"], metadata)
            if settings["profile"] == "academic" or _load_translation_spec(temp_dir) is not None:
                _write_translation_spec(temp_dir, settings)
            _write_source_fingerprint(temp_dir, current_fingerprint)
            print("Conversion completed successfully!")
            print(f"Temp directory: {temp_dir}")
            return

        if not convert_to_htmlz(input_file, htmlz_file, calibre_path):
            sys.exit(1)

        with tempfile.TemporaryDirectory() as extract_dir:
            html_file, images_dir = extract_htmlz(htmlz_file, extract_dir)
            if not html_file:
                sys.exit(1)

            metadata = extract_metadata_from_htmlz(extract_dir)

            temp_dir = setup_temp_directory(input_file, html_file, images_dir, temp_root=args.temp_root)
            if not temp_dir:
                sys.exit(1)

            input_html = os.path.join(temp_dir, "input.html")
            input_md = os.path.join(temp_dir, "input.md")

            _abort_on_strip_cache_conflict(
                _check_strip_page_numbers_cache_conflict(args.strip_page_numbers, temp_dir, input_md),
                temp_dir,
            )
            if os.path.exists(input_md):
                print(f"Skipping HTML to Markdown conversion - input.md already exists")
            else:
                if not convert_html_to_markdown(input_html, input_md, strip_page_numbers=args.strip_page_numbers):
                    sys.exit(1)

            chunk_count = _do_split_and_manifest(
                temp_dir, input_md, settings["chunk_size"], academic=settings["profile"] == "academic"
            )
            if chunk_count == 0:
                sys.exit(1)

            create_config_file(temp_dir, input_file, settings["source_lang"], settings["target_lang"], metadata)
            if settings["profile"] == "academic" or _load_translation_spec(temp_dir) is not None:
                _write_translation_spec(temp_dir, settings)
            _write_source_fingerprint(temp_dir, current_fingerprint)

            print("Conversion completed successfully!")
            print(f"Temp directory: {temp_dir}")
            print(f"Markdown chunks: {chunk_count} files")

            if os.path.exists(htmlz_file):
                os.remove(htmlz_file)

    except KeyboardInterrupt:
        print("\nConversion interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
