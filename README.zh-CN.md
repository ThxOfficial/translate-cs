# Translate-CS

[English](README.md) | 中文

`translate-cs` 是面向 Codex 的 Skill，用于翻译计算机科学、AI、LLM、Agent 和相关数学方向的论文与书籍。模型先读原文件，自行判断直接翻译还是对长文提取 TXT，编辑并对照原文核对后再生成 PDF。

---

## 工作原理

```
原始文件 (PDF/DOCX/EPUB/TXT/其他可读源文件)
  │
  ▼
模型直接读取原页/章节，判断长度和上下文预算
  │
  ▼
可直接处理 → 直接翻译
较长       → 提取 source.txt → 按完整章节/页段翻译
  │  公式、表格和图片始终以原文件为准
  ▼
编辑译稿，逐项对照所请求的原文范围，记录当前核对证据
  │
  ▼
已核对译稿排版 → book.pdf → 外观检查
```

默认没有源文件转 Markdown 的前置步骤。模型结合实际内容密度和上下文判断，不硬编码页数阈值；短文无需 TXT，长文保留源位置、分批译稿与术语记录。`output.md` 仅是翻译过程中或完成后编辑得到的目标语言排版稿。

## 功能特性

- **先读原文件** — 使用实际可用的文档、页面或视觉工具，再决定处理路径
- **模型判断 TXT 回退** — 长文提取 UTF-8 TXT，并回看原页检查提取损失
- **核对后生成 PDF** — 覆盖范围、术语、公式代码、图表、结论引文及语言均先核对
- **证据绑定版本** — 原文件、TXT、分批译稿、术语/进度、编辑稿或图片变化都会使核对过期
- **默认 PDF** — 使用已安装 Pandoc 及 Word 或 Chromium/Edge，并单独记录外观检查
- **长文持久进度** — 保存章节/页段、分批译稿和术语；可选使用新的工作上下文
- **多语言** — zh、en、ja、ko、fr、de、es（可扩展）
- **旧模式兼容** — 既有通用/学术 Markdown 流水线、词表、manifest、精确重译及 DOCX/EPUB 输出继续支持显式选择和旧任务续跑

## 默认：直接读源文件

让模型直接翻译源文件。模型先记录实际阅读和长度判断，再登记未修改的原文件；仅在模型选择长文 TXT 路径时提取文本：

```bash
python3 scripts/source_workflow.py prepare paper.pdf --assessment-file assessment.json --target-lang zh
# 仅当模型已选择 "txt"：
python3 scripts/source_workflow.py extract-txt paper_temp
```

翻译和编辑完成后，记录完整的源文对照核对，再生成 PDF：

```bash
python3 scripts/source_workflow.py record-review paper_temp --evidence-file editorial-evidence.json
python3 scripts/source_workflow.py build-pdf paper_temp --title "译后标题"
```

模型实际填写的判断/核对 JSON、页面覆盖、TXT 纠错、进度和外观检查契约见[执行说明](references/source-first.md)。脚本不调用模型，也不证明语义正确；缺失或过期核对会阻止 PDF 构建。文件生成后仍保持 `needs_visual_review`，实际检查 PDF 外观后才完成。标题或排版输入改变时显式使用 `--rebuild`；原生格式重建失败保留旧 PDF，但不会将它标为当前交付。

模型必须具备可靠的源文件阅读能力。TXT 不保留全部 PDF 公式和版面，纠错时须回看原文件；不会自动启用模型、OCR 服务或上传文献。下面的旧模式测试证据不代表新流程的真实翻译准确率已获验证。

本次验证：改造前 320 项基线通过，改造后全部 333 项 stdlib 单测通过，其中新增 13 项直接读源文件流程回归。真实双页合成 PDF 通过新 prepare/extract CLI 和本机 `pdftotext`，保留两页内容与分页符，没有生成源 Markdown；编译和 Skill 校验通过。本轮没有实际评测模型翻译，也没有重跑 Word/Chromium PDF 渲染器。

## 旧学术流水线（阶段 A、B 和 C）

本节只适用于明确选择[旧流水线](references/legacy-pipeline.md)或续跑既有旧任务。其 `academic` profile 和 Markdown/HTML 默认输出不能覆盖上面的直接读源文件、最终 PDF 流程。

学术模式接受已检查的版面感知 Markdown、显式选择的本地 PDF 解析器，以及有边界的 LaTeX 工程。DOCX/EPUB 或不支持的版面先提取并检查 Markdown。Markdown 入口示例：

```bash
python3 scripts/convert.py paper.md --profile academic \
  --document-kind paper --subject ai --subject llm --subject agent \
  --olang zh --review-mode risk_based --review-sample-rate 0.1 \
  --format md --format html
```

学术模式转换会生成 `ingestion.json`，记录原始来源指纹、规范化处理和图片资源映射；同时生成 `document_map.json`，记录精确源切片、Unicode 字符偏移、结构块类型、章节路径及 hash。开始翻译前，检查 `input.md` 和映射文件中的阅读顺序、公式、表格与图片目标，并记录检查结果：

```bash
python3 scripts/quality.py <temp_dir> --record-input-review \
  --findings-file <temp_dir>/drafts/input-review.json
```

检查文件必须列出 `ingestion.json` 中的全部检查项；只有逐项检查后，才可使用空 findings 列表。目标语言为中文时，`scripts/glossary.py seed-academic` 会补入精选术语和语义说明，不覆盖手工编辑的词条；其他目标语言不会套用中文种子译法。翻译流程会先掩码受保护内容，仅在占位符与结构检查通过后恢复，并在续跑规划前记录每块使用的术语、邻块和正文首现上下文。

学术审校模式包括：`risk_based`（每块记录译者自检，并对高风险块和按源 hash 可复现抽取的 10% 普通块做协调审校；普通块至少抽一块）、`full`（每块都做协调审校）和 `structural_only`。`structural_only` 始终标记为 `needs_review`。最终质量门禁还要求当前有效的来源检查 sidecar；来源记录过期、资源 hash 改变或存在未解决的审校警告时不能标记为已验证。默认正式构建要求质量报告为 `validated`；用户明确要求中间稿时可用 `--allow-draft` 构建，并在 `build_report.json` 中标记草稿。任何阶段都不证明解析器没有漏内容，也不能证明数学或语义完全正确，因此要检查解析后的 Markdown 和生成的报告。

学术模式默认输出 `output.md` 和 `book.html`。只有用户需要时才添加 `--format docx`、`--format epub` 或 `--format pdf`；构建报告会分别记录请求格式的状态。模板或渲染器变化后可用 `--rebuild` 强制重建派生产物，同时保留译文、术语表和审校记录。中间 chunk、掩码任务、审校记录和质量报告都会保留以供检查。

阶段 B 新增带原文证据的全文上下文。在记录任务快照前，运行 `python3 scripts/document_context.py <temp_dir> --prepare`，检查优先阅读块及定义、缩略语候选，再用 `--prepare --facts-file <facts.json>` 确认有来源支持的内容。文件须包含 `topics`、`definitions`、`abbreviations`、`symbols` 四个列表；精确证据位置和文档/章节作用域见[上下文契约](references/document-context.md)。每个任务只接收相关父级定义和局部符号；同义项冲突会显式报告，上下文超限会报错，修订只使实际使用该内容的任务快照失效。没有此文件的旧阶段 A 运行保持兼容。

[B/C 执行契约](references/advanced-academic.md) 说明了已实现的命令与 schema：

- 显式执行 `glossary.py migrate-v3`，兼容旧词表并增加义项、章节作用域、出现位置绑定、翻译策略与人工锁。meta v2 提议通过事务合并；同名歧义须明确裁决。
- 添加 `--independent-review`，要求审校者及执行上下文与译者不同。`review_tasks.py` 生成任务包，每个源块最多一次持久记录的修复尝试，最终术语协调最多两轮。没有独立审校者时，所需覆盖保持未完成。
- `convert.py paper.pdf --profile academic --pdf-parser marker`（或 `mineru`）调用已安装的本地解析器。`poppler --simple-pdf` 须显式指定，只适用纯文本 PDF。`pdf_ingest.py --pages 3:5` 单独重解析局部页面，不静默替换整本来源；不自动安装模型或上传文献。
- LaTeX 支持选定主文件、工程内递归 include、宏/BibTeX/图片来源，以及经 Pandoc 处理的受支持定理和公式，不执行 TeX。任意 TeX 程序、复杂宏绘图和依赖特定包的文献样式须提供已检查的 Markdown。
- 学术 DOCX 使用原生 OMML，EPUB 使用 MathML，PDF 使用 Windows 已安装 Word 或已安装 Chromium。生成成功后须通过 `format_review.py` 记录独立的外观检查；缺失或过期检查使构建保持 partial。原生格式重建失败保留旧产物；通用格式流程保持兼容。
- 可选 `academic_views.py` 输出精确块对齐的双语 Markdown/HTML，单独呈现已确认译注及图中文字对照。`table_tasks.py` 将超长独立管道表格拆为行组，验证后提交一个规范源块对应译文。复杂 HTML rowspan 表格仍按原子块处理。数学审校规则扩展到线性代数、概率、优化与逻辑。

2026-10-22 验证：320 项 stdlib 单测通过，另有真实 Pandoc HTML、Poppler 全文/局部页面、递归 LaTeX，以及五块模型翻译/独立审校/修复演示。已实际生成并检查 Pandoc 3.9 DOCX/EPUB、Word 16 PDF 样本，涵盖五个公式、中文、表格及脚注；EPUB 在内置 Chromium 中检查了 spine 内容与脚注导航。这是合成流程演示，不是准确率基准或所有阅读器的兼容认证。未安装 Marker/MinerU 模型，对应适配器只完成调用契约测试。最后一次需隐藏 Word 的原生格式集成重跑未获授权；保留先前真实渲染证据及当前单测结果。生成证据放在被忽略的 `tests/.artifacts/`。

学术 HTML 显式启用美元和反斜杠公式分隔符，将 Pandoc 结构化诊断保存为 `output.html.pandoc.json`。即使 Pandoc 返回成功，不支持的数学内容也会阻止构建完成；不影响公式的本地化警告保留供检查。将 Pandoc 加入 PATH 后，可运行真实引擎检查 `python3 tests/integration/check_academic_html.py`，检查分式、根式、矩阵、上下标的 MathML 及不支持宏的失败行为；这不等同于 EPUB/PDF 外观或语义准确性验证。

运行 `python3 tests/integration/check_document_context.py` 可验证上下文与续跑规划的 CLI 流程。生成产物放在 `tests/.artifacts/academic-stageB1/`，源文复制输出明确标记为流水线 mock；检查末尾定义修订只重新规划 12 块中的 2 块。此检查不依赖 Pandoc，不属于翻译质量基准。

## 前置要求

- **Agent 运行环境** — Codex、Claude Code 或 OpenClaw，已安装并可运行 Skill
- **源文件阅读工具** — Agent 宿主提供原文件、页面或视觉读取能力
- **Python 3.12+** — 新流程脚本只依赖标准库
- **pdftotext** — 可选，用于模型选定的长 PDF → TXT；也可接受阅读工具导出的 UTF-8 TXT
- **Pandoc + 已安装 Word 或 Chromium/Edge** — 仅最终 PDF 排版/渲染需要
- **仅旧流水线需要**：Calibre（`ebook-convert`，[下载](https://calibre-ebook.com/)）、[Pandoc](https://pandoc.org/) 和 Python 包：
  - `pypandoc` — 必需（`pip install pypandoc`）
  - `beautifulsoup4` — 可选，用于更好的目录生成（`pip install beautifulsoup4`）
- **MinerU 或 Marker** — 可选的旧模式 Markdown 适配器，不是默认预处理

## 快速开始

### 1. 安装 Skill

#### Codex

把 GitHub 仓库克隆到 Codex 的个人 Skill 目录：

```bash
mkdir -p ~/.agents/skills
git clone https://github.com/ThxOfficial/translate-cs.git ~/.agents/skills/translate-cs
```

Windows PowerShell 命令：

```powershell
$target = Join-Path $HOME ".agents\skills\translate-cs"
New-Item -ItemType Directory -Force -Path (Split-Path $target) | Out-Null
git clone https://github.com/ThxOfficial/translate-cs.git $target
```

Codex 从 `~/.agents/skills` 发现个人 Skill。运行 `/skills` 查看列表，或输入 `$translate-cs` 显式调用。Codex 会自动发现 Skill 变更；如果没有显示，再重启 Codex。

#### Claude Code

```bash
npx skills add ThxOfficial/translate-cs -a claude-code -g
```

或手动安装：

```bash
mkdir -p ~/.claude/skills
git clone https://github.com/ThxOfficial/translate-cs.git ~/.claude/skills/translate-cs
```

#### OpenClaw

如需在 OpenClaw 中使用，请遵循当前运行环境的本地 Skill 目录说明。

### 2. 翻译一本书

#### Codex

在 Codex CLI 或 IDE 扩展中输入：

```text
$translate-cs Translate /path/to/paper.pdf into Chinese, preserving formulas, terminology, and citation structure.
```

当请求与 Skill 描述匹配时，Codex 也可以自动选择该 Skill。

#### Claude Code 和 OpenClaw

直接告诉 Agent：

```text
translate /path/to/book.pdf to Chinese
```

在 Claude Code 中也可以使用斜杠命令：

```text
/translate-cs translate /path/to/book.pdf to Japanese
```

Skill 负责直接读取源文件、判断是否需要 TXT、翻译、编辑对照、生成 PDF 并检查外观。

### 3. 查看输出

所有文件在 `{book_name}_temp/` 目录下：

| 文件 | 说明 |
|------|------|
| `source_workflow.json` | 原文件身份与模型路径判断 |
| `source.txt` | 可选的长文源文本 |
| `drafts/chunk*.md` | 可选的分批译稿 |
| `output.md` | 编辑完成的目标语言排版稿 |
| `editorial_review.json` | 源文覆盖及版本绑定的编辑核对证据 |
| `book.pdf` | 报告为 complete 时的最终 PDF |
| `build_report.json` | 实际生成和外观检查状态 |

HTML/原生 DOCX 可能作为渲染中间文件保留。DOCX/EPUB 交付与原 manifest 流水线仍可显式选择旧模式使用。

## 仓库测试资产

- 需要纳入仓库的基准书输入，统一放在 `tests/baselines/<book-id>/`。
- 完整流水线跑出来的产物统一放在 `tests/.artifacts/`，不提交到版本库。
- 由于 `scripts/convert.py` 会把 `{book_name}_temp/` 写到**当前工作目录**下，仓库内的 baseline 测试应从 `tests/.artifacts/` 目录里启动，这样生成文件不会散落到仓库根目录。

### 旧流水线完整基准测试示例

```bash
mkdir -p tests/.artifacts
cd tests/.artifacts
python3 ../../scripts/convert.py ../baselines/standard-alice/standard-alice.epub --olang zh
# 然后通过 skill 完成翻译
python3 ../../scripts/merge_and_build.py --temp-dir standard-alice_temp --title "test"
```

## 推送到 GitHub

当前配置的 `origin` 是 `https://github.com/ThxOfficial/translate-cs.git`，工作分支为 `main`。提交前先检查将要暂存的文件：

```bash
git status --short --branch
git add -A
git diff --cached --check
git diff --cached --stat
git commit -m "feat: specialize translate-cs for academic translation"
git push origin main
```

`git add -A` 会暂存所有已跟踪修改和未跟踪项目文件，提交前应检查暂存摘要。推送 `main` 会更新仓库；带版本号的 GitHub Release 是单独操作。

## 反馈与贡献

请优先提交详细的 GitHub issue，而不是直接从 pull request 开始。本项目按 AI 辅助的 skill pipeline 维护，任何变更都需要放在同一个由维护者掌握的上下文里，结合当前编排规则、chunk/manifest 契约、baseline 资产和发布流程一起评估。

Pull request 不是首选贡献入口，可能会被关闭并转为 issue 继续讨论。如果你已经有 patch，可以把思路、关键 diff、失败用例或验证结果写进 issue；维护者可能会据此重写或拆分实现，再决定是否合入。

一个有用的 issue 应包含：

- 当前行为与期望行为
- 输入格式和运行环境，例如 PDF/DOCX/EPUB、操作系统、Python、Calibre、Pandoc 版本
- 尽量小的复现步骤，或可公开使用的小样本文件
- 能说明问题的日志、截图或生成文件名

## 联系方式

Bug 和功能需求请提交到 [ThxOfficial/translate-cs](https://github.com/ThxOfficial/translate-cs/issues)。

## 旧流水线详解（显式选择）

以下命令保留给既有任务和显式旧模式请求。新翻译遵循[直接读源文件契约](references/source-first.md)，不会以下面的 PDF 转 Markdown 为第一步。

### 第一步：转换

```bash
python3 scripts/convert.py /path/to/book.pdf --olang zh
```

Calibre 将输入文件转为 HTMLZ，解压后转为 Markdown，再拆分为 chunk（每个约 6000 字符）。`manifest.json` 记录每个源 chunk 的 SHA-256 hash，用于后续校验；`source_fingerprint.json` 则把 temp 目录与生成它的源文件字节绑定 — 若源文件被替换后重跑，会直接报错中止，而不是静默复用过时的 chunk。指纹机制之前创建的 temp 目录会在首次重跑时打印警告并被接管。

默认工作目录是当前目录下的 `{book_name}_temp/`。如果要换父目录，可使用 `--temp-root /path/to/work`；叶子目录名仍保持 `{book_name}_temp/`。

#### 公式、表格密集的 PDF：改为转换 Markdown（可选）

Calibre 靠坐标启发式重排 PDF 文本，对普通正文够用，但学术、技术类 PDF 会丢失结构：公式被拆成碎片，表格被拍平成一行一个单元格，多栏版面相互交错。对这类 PDF，先用版面感知解析器转出 Markdown，再把 Markdown 交给 `convert.py`。`.md` / `.markdown` 输入完全不经过 Calibre：

```bash
pip install -U "mineru>=4.0,<5"                # 或：pip install marker-pdf
mineru-kit parse paper.pdf -o paper.md         # MinerU >= 4（纯 CPU 的 basic 档约需 0.8 GB 模型）
# marker_single paper.pdf --output_dir out/    # Marker：输出为 out/paper/paper.md
python3 scripts/convert.py paper.md --olang zh
```

解析器会把公式保留为 `$...$` / `$$...$$` LaTeX，表格保留为表格。`convert.py` 会把 Markdown 旁边引用的图片复制到 `{book_name}_temp/images/`，MinerU 以 base64 内嵌的图片也会解码成文件放进去，chunk 中不会夹带图片数据。公式块（`$$ ... $$`）不会被拆到两个 chunk 中。YAML front matter（`title`、`author`、`lang`）会写入 `config.txt`，不会送去翻译；没有 front matter 时，开头的 `#` 标题作为 `original_title`。temp 目录按 Markdown 文件名命名，`paper.md` 对应 `paper_temp/`；如果该目录之前由 `paper.pdf` 生成，`convert.py` 会因源文件指纹不一致而中止，需先删除它。两个解析器首次运行都会下载模型。`--strip-page-numbers` 只对 Calibre 输入生效。

生成成品时，Pandoc 会把 `$...$` / `$$...$$` 渲染为 MathML，因此 `book.html`、`book.pdf` 中的公式会被正确排版，`book.epub` 在支持 MathML 的阅读器中同样如此。Calibre 生成的 DOCX 无法排版 MathML，其中的公式会显示为压平的文本加上对应的 TeX 源码。

### 第一步半：术语表（保证全书译名一致）

每个 chunk 由独立的 fresh-context subagent 翻译 — 这意味着同一个专有名词在 100 个 chunk 之间可能出现多种译法。为此，skill 在翻译前会先构建术语表：

1. 抽样 5 个 chunk（首章、末章、3 个均匀分布的中间章节）。
2. 提取专有名词和反复出现的领域术语，给每个术语确定一个标准译法。
3. 写入 `<temp_dir>/glossary.json`（schema 见下，可手动编辑）。
4. 运行 `python3 scripts/glossary.py count-frequencies <temp_dir>`，统计每个术语在全书的出现次数（ASCII 术语用单词边界正则，避免 `cat` 误匹配 `category`；中日韩术语用子串匹配；单字汉字术语会被拒绝以防过度匹配；别名也计入所属术语的频次）。
5. 翻译每个 chunk 之前，主 agent 调用 `python3 scripts/glossary.py print-terms-for-chunk <temp_dir> chunkNNNN.md`，将输出的 3 列（`原文 | 别名 | 译文`）markdown 表格作为硬性约束注入到该 chunk 的 prompt。术语选取 = (本 chunk 中出现原文或任一别名的术语) ∪ (全书出现频率 top-N 的术语)。

```json
{
  "version": 2,
  "terms": [
    {"id": "Manhattan", "source": "Manhattan", "target": "曼哈顿",
     "category": "place", "aliases": [], "gender": "unknown",
     "confidence": "medium", "frequency": 12,
     "evidence_refs": [], "notes": ""}
  ],
  "high_frequency_top_n": 20,
  "applied_meta_hashes": {}
}
```

已有的 v1 `glossary.json` 会在首次加载时自动升级为 v2。v2 禁止同一个表面词（原文或别名）同时归属于两个不同术语；如果 v1 文件存在同名（polysemous）的重复 source，升级会终止并给出消歧提示 — 手工修复后重新加载即可。

可在两次运行之间编辑 `glossary.json` 修正译法。已存在的 `glossary.json` 不会被覆盖 — 删除它才会重建。`scripts/run_state.py` 会记录每个 chunk 用到的术语表状态，因此后续术语表变化（包括 `target`、`category`、`aliases` 的编辑）只会重译受影响的 chunk（前提是该 chunk 已写入 run_state）。

### 第二步：翻译（并行 subagent）

Skill 分批启动 subagent（默认 8 路并发）。每个 subagent：

1. 读取一个源 chunk（如 `chunk0042.md`）
2. 翻译为目标语言
3. 使用该 chunk 的术语表和相邻 chunk 的短只读上下文
4. 将结果写入 `output_chunk0042.md`
5. 写入 `output_chunk0042.meta.json`，供术语表反馈合并

启动 subagent 前，`scripts/run_state.py plan <temp_dir>` 会判断哪些 chunk 需要翻译、哪些已有输出只需记录状态、哪些无需处理。只有在接管旧 temp 目录且明确希望现有输出按当前术语表重译时，才使用 `--retranslate-untracked`。如果运行中断，重新运行会跳过已有合法输出且状态仍有效的 chunk。翻译失败的 chunk 会自动重试一次。

### 第三步：合并与构建

```bash
python3 scripts/merge_and_build.py --temp-dir book_temp --title "《译后书名》"
```

可选输出参数：

```bash
python3 scripts/merge_and_build.py --temp-dir book_temp --title "《译后书名》" --cover cover.jpg --export-name "译后书名"
```

`--cover` 会把显式封面图传给 EPUB 的 Calibre 步骤。`--export-name` 会额外生成如 `译后书名.epub` 的别名副本，同时保留内部 canonical 的 `book.*` 产物。

合并前校验：
- 每个源 chunk 都有对应的输出文件（1:1 匹配）
- 源 chunk hash 与 manifest 一致（无过时输出）
- 输出文件不为空、非纯空白（whitespace-only）、可正常读取 — 空白 chunk 会中止合并，而不是静默丢失该段内容

校验通过后：合并 → Pandoc 生成 HTML → 注入目录 → Calibre 生成 DOCX、EPUB、PDF。

**注意：** `{book_name}_temp/` 是单次翻译运行的工作目录。如果修改了标题、作者、输出语言、模板或图片资源，建议使用新的 temp 目录，或先删除已有的最终产物（`output.md`、`book*.html`、`book.docx`、`book.epub`、`book.pdf`）再重跑。

## 项目结构

| 文件 | 用途 |
|------|------|
| `SKILL.md` | Agent Skill 定义 — 编排完整流程 |
| `scripts/source_workflow.py` | 源文件登记、可选 TXT、编辑核对证据及核对后 PDF |
| `references/source-first.md` | 当前默认的阅读/翻译/编辑/PDF 契约 |
| `references/legacy-pipeline.md` | 兼容的显式旧 Markdown 流水线 |
| `scripts/convert.py` | PDF/DOCX/EPUB → Markdown chunks（经 Calibre HTMLZ）；Markdown 输入不经过 Calibre |
| `scripts/manifest.py` | Chunk manifest：SHA-256 追踪与合并校验 |
| `scripts/glossary.py` | 术语表管理：为每个 chunk 生成专属术语对照表，保证全书译名一致 |
| `scripts/chunk_context.py` | 为 subagent prompt 提供上一/下一 chunk 的只读摘录 |
| `scripts/meta.py` | 子 agent 单 chunk 观察文件 schema（`output_chunkNNNN.meta.json`） |
| `scripts/merge_meta.py` | 批次边界合并：子 agent 观察 → canonical 术语表 |
| `scripts/run_state.py` | 精确重译规划器和 `run_state.json` 记录器 |
| `scripts/merge_and_build.py` | 合并 chunks → HTML → DOCX/EPUB/PDF |
| `scripts/calibre_html_publish.py` | Calibre 格式转换封装 |
| `scripts/template.html` | 网页 HTML 模板，含浮动目录 |
| `scripts/template_ebook.html` | 电子书 HTML 模板 |
| `tests/baselines/` | 纳入仓库的完整链路 baseline 输入 |
| `tests/.artifacts/` | 被忽略的完整链路测试产物 |

## 常见问题

| 问题 | 解决方案 |
|------|----------|
| `Calibre ebook-convert not found` | 安装 Calibre，确保 `ebook-convert` 在 PATH 中 |
| `Manifest validation failed` | 源 chunk 在拆分后被修改 — 重新运行 `convert.py` |
| `was created from different source bytes` | temp 目录属于另一个源文件 — 删除 temp 目录或换一个 `--temp-root` |
| TXT 中公式、表格不完整 | 回看原页，校正 TXT 并保留图片，不猜测缺失技术内容；Markdown 适配器仍作为旧模式选项保留。 |
| `Blank output` / `Empty output` | 某个 subagent 写出了空白或空的 chunk — 重新运行 skill 让它重译 |
| `Missing source chunk` | 源文件被删除 — 重新运行 `convert.py` 重新生成 |
| 翻译不完整 | 重新运行 Skill，会从中断处继续 |
| 修改标题、模板或图片后输出未更新 | 新流程中，跟踪内容改变后先更新核对，再执行 `source_workflow.py build-pdf ... --rebuild`，保留编辑稿 `output.md`。旧模式清理方式见上文。 |
| TXT 中的页码 | 保留分页符和源位置，移除页脚前先与原页核对；`convert.py --strip-page-numbers` 仅适用于旧模式 Calibre 输入。 |
| `output.md exists but manifest invalid` | 旧输出已过时 — 脚本会自动删除并重新合并 |
| `Glossary upgrade rejected: duplicate source` | 旧版 v2 词表要求表面词唯一。学术翻译可显式运行 `python3 scripts/glossary.py migrate-v3 <temp_dir>` 迁移到 v3，再用经过审阅的词面绑定解决多义词。 |
| 默认 PDF 生成失败 | 检查 `build_report.json`，确认 Pandoc 及 Word/Chromium/Edge 可用 |
| PDF 在渲染前被阻止 | 完成当前源文对照核对；文件改动后需要重新核对 |

## 旧通用流水线能力

这些能力保留在可选的旧流水线中，其尚未完成的验证事项见[学术交付状态](docs/CS_AI_TRANSLATION_SPEC.md#135-当前尚未完成的验收和发布工作)。

通用流水线支持有作用域的术语、来源证据上下文和精确重译。真实技术书籍的整本翻译尚未完成自然度验证。

### 设计原则

- **脚本做记账，LLM 做语义合并**。状态管理、schema 校验、去重、hash、IO 是确定性的 Python；命名、性别归属、别名判定、冲突解决交给 LLM。
- **共享状态单写者**。`glossary.json` 和 `run_state.json` 仅由主 agent 写入；子 agent 只读共享状态，并写入各自的 chunk meta 文件。无需加锁。
- **保守合并**。新实体必须有证据；别名合并需要 LLM 判断,不能仅靠字符串相似度；性别默认 `unknown`,仅在显式证据下才升级；canonical 值在冲突时不会被静默覆盖。
- **三层状态,三个独立文件**。`glossary.json`（canonical,子 agent 读取）、`output_chunkNNNN.meta.json`（子 agent 原始观察）、`run_state.json`（编排状态）。

### Phase 1 — 子 agent 反馈 + 术语表合并（已发布）

闭合读写回路。术语表 v2 新增 `id`、`aliases`、`gender`、`confidence`、`evidence_refs`、`notes`（v1 文件首次加载时自动升级；术语表现在是 3 列，`aliases` 参与选词链路）。子 agent 在输出译文的同时生成 `output_chunkNNNN.meta.json`。新增 `scripts/merge_meta.py`（`prepare-merge` / `apply-merge` / `status`）按批次执行保守合并：跨术语 surface form 唯一性、坏 meta 隔离（warn + skip + count）、`evidence_chunks` 与 `used_term_sources` 双路 confidence 升级、FIFO 上限 5。详见[旧流程第 4–5 步](references/legacy-pipeline.md)。

### Phase 2 — 代词的邻居上下文（已发布）

`scripts/chunk_context.py` 为每个子 agent prompt 注入 `prev_excerpt`（上一个 chunk 末尾约 300 字）和 `next_excerpt`（下一个 chunk 开头约 300 字），仅作只读上下文参考。不新增状态文件。

### Phase 3 — 精确重译（已发布）

Phase 1 的批次反馈只能向前优化。精确重译通过 `scripts/run_state.py` 和 `run_state.json` 闭合向后的回路：按 chunk 跟踪 `glossary_version_used`、`entity_ids_used`、`output_hash`、源 hash、以及选中实体的 hash；五条规划规则覆盖缺失/空输出、manifest 源文件漂移、未记录输出、记录后的源文件漂移、以及术语选择/术语 hash 变化。

### Phase 4 — 冷启动预热（实验性,依赖 Phase 1 的实际数据）

Phase 1 让术语表按批次增长,因此第一批看到的术语表最小,drift 风险最高。可能的方案:顺序冷启动、可变并发、或跳过预热。决策权属于实际跑过完整书的人。

> Phase 4 仍取决于真实书籍运行数据。已发布的 schema 后续如果暴露问题，也应通过兼容性迁移继续演进。

### Pipeline / UX 能力（部分完成）

最近几轮 PR 讨论也暴露出一些有价值的工作流改进,但它们都不属于“一次性小补丁”：会触及仓库契约（产物命名、temp-dir 行为、清理语义、或 EPUB 兼容性边界）。当前状态：

- **显式 EPUB 封面支持（已实现）**。`merge_and_build.py --cover <image>` 会在 HTML -> EPUB 的 Calibre 步骤透传封面图。`--cover-from <epub>` / EPUB 封面自动提取仍不纳入当前范围，待项目准备好承担不同 EPUB 包布局的解析兼容性后再考虑。
- **可配置的 temp 工作目录位置（已实现）**。`convert.py --temp-root <dir>` 保留默认 cwd-local `{book_name}_temp/` 行为，只有显式传参时才改变父目录。
- **更安全的 Calibre/Pandoc 噪声清理（部分实现）**。页码和 Calibre marker 清理已有回归测试保护，保留年份、章节编号和非单调独立数字。后续清理规则继续在测试下增量增加。
- **可选的面向用户导出文件名（已实现）**。`merge_and_build.py --export-name <stem>` 生成 alias/copy，同时流水线内部 canonical 产物仍保持 `book.html`、`book_doc.html`、`book.docx`、`book.epub`、`book.pdf`。

## Star History

如果这个项目对您有帮助，请考虑为其点亮一颗 Star ⭐！

[![Star History Chart](https://api.star-history.com/svg?repos=ThxOfficial/translate-cs&type=Date)](https://star-history.com/#ThxOfficial/translate-cs&Date)

## 模型执行

`translate-cs` 不直接调用模型 API。翻译和审校由用户自己的 Agent 宿主执行，例如 Codex、Claude Code 或 OpenClaw。

## License

[MIT](LICENSE)
