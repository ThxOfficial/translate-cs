"""Opt-in real-engine format generation; inspect rendered PNGs separately."""
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import academic_formats
import merge_and_build


def main():
    root = ROOT / 'tests/.artifacts/formats-bc'
    root.mkdir(parents=True, exist_ok=True)
    text = (ROOT / 'tests/fixtures/academic/math-rendering.md').read_text(encoding='utf-8')
    text += '\n\n## 中文排版与表格\n\n在这些假设下，结论并不一定成立。[^scope]\n\n| 方法 | 准确率 | 说明 |\n|---|---:|---|\n| 基线 | 91.2% | 观察结果不能推广为普遍保证。 |\n| 新方法 | 92.1% | 保留百分比与百分点的区别。 |\n\n[^scope]: 此处仅检查格式，不构成语义质量评测。\n'
    (root / 'output.md').write_bytes(text.encode('utf-8'))
    (root / 'translation_spec.json').write_text(json.dumps({'schema_version': 1, 'profile': 'academic'}), encoding='utf-8')
    if not merge_and_build.convert_md_to_html(str(root), 'Academic format check', merge_and_build.get_lang_config('zh'), academic=True):
        raise RuntimeError('Real academic HTML rendering failed')
    results = {fmt: academic_formats.build(root, fmt, 'zh-CN', force=True) for fmt in ('docx', 'epub', 'pdf')}
    (root / 'result.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    assert all(r['status'] in ('ok', 'needs_visual_review') for r in results.values()), results
    # Extract actual EPUB spine/CSS/assets for an offline browser reading check.
    with zipfile.ZipFile(root / 'book.epub') as archive:
        for name in archive.namelist():
            target = (root / 'epub-reader' / name).resolve()
            if not target.is_relative_to((root / 'epub-reader').resolve()):
                raise ValueError('Unsafe EPUB member')
        archive.extractall(root / 'epub-reader')
    spine = sorted((root / 'epub-reader').rglob('ch*.xhtml'))
    assert spine
    results['epub']['reader_spine_files'] = [str(path.relative_to(root)) for path in spine]
    (root / 'result.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
