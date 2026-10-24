#!/usr/bin/env python3
"""发布前质检门禁。

用法：
    python scripts/preflight.py                 # 只检查 git 中新增/修改的文章
    python scripts/preflight.py --all           # 全站检查
    python scripts/preflight.py _posts/x.md     # 指定文件

退出码 0 表示可以发布，1 表示必须拦下。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POSTS = ROOT / "_posts"
LEGACY_BEFORE = "2026-01-01"

REQUIRED = ["layout", "title", "date", "author", "header-img", "tags"]
RECOMMENDED = ["subtitle", "description", "catalog"]

PLACEHOLDERS = ["TODO", "待补充", "待完善", "Lorem ipsum", "此处省略", "XXX 占位"]

CN_DIGITS = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def cn2int(text: str):
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if "十" not in text:
        return CN_DIGITS.get(text) if len(text) == 1 else None
    left, _, right = text.partition("十")
    tens = CN_DIGITS.get(left, 1) if left else 1
    ones = CN_DIGITS.get(right, 0) if right else 0
    if (left and left not in CN_DIGITS) or (right and right not in CN_DIGITS):
        return None
    return tens * 10 + ones


def split_front_matter(text: str):
    if not text.startswith("---"):
        return None, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None, text
    return parts[1], parts[2]


def load_yaml(block: str):
    try:
        import yaml
    except ImportError:
        return _minimal_yaml(block), False
    try:
        return (yaml.safe_load(block) or {}), True
    except Exception as exc:  # noqa: BLE001
        return {"__error__": str(exc)}, True


def _minimal_yaml(block: str):
    """无 PyYAML 时的降级解析，只覆盖本站 front matter 的简单结构。"""
    data, key = {}, None
    for raw in block.splitlines():
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        if raw.lstrip().startswith("- ") and key:
            data.setdefault(key, [])
            if isinstance(data[key], list):
                data[key].append(raw.lstrip()[2:].strip())
            continue
        if ":" in raw:
            key, _, value = raw.partition(":")
            key, value = key.strip(), value.strip()
            data[key] = value if value else []
    return data


def gather_targets(argv):
    if "--all" in argv:
        return sorted(POSTS.glob("*.md"))
    files = [a for a in argv if a.endswith(".md")]
    if files:
        return [Path(f) if Path(f).is_absolute() else ROOT / f for f in files]
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all", "_posts"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout
    except Exception:  # noqa: BLE001
        return []
    targets = []
    for line in out.splitlines():
        path = line[3:].strip().strip('"')
        if path.endswith(".md"):
            targets.append(ROOT / path)
    return sorted(set(targets))


def strip_noise(body: str) -> str:
    """去掉代码块与数学公式，避免 LaTeX 的 }} 被误判成 Liquid 语法。"""
    body = re.sub(r"```.*?```", "", body, flags=re.S)
    body = re.sub(r"`[^`\n]*`", "", body)
    body = re.sub(r"\$\$.*?\$\$", "", body, flags=re.S)
    body = re.sub(r"(?<!\$)\$(?!\$)[^$\n]+\$", "", body)
    return body


def check_one(path: Path, errors, warnings, chapters, seen_titles):
    rel = path.relative_to(ROOT).as_posix()
    if not path.exists():
        errors.append((rel, "文件不存在"))
        return

    text = path.read_text(encoding="utf-8")
    fm_block, body = split_front_matter(text)
    if fm_block is None:
        errors.append((rel, "缺少 front matter（文件必须以 --- 开头）"))
        return

    fm, strict_yaml = load_yaml(fm_block)
    if "__error__" in fm:
        errors.append((rel, f"front matter YAML 语法错误：{fm['__error__']}"))
        return
    if not strict_yaml:
        warnings.append((rel, "未安装 PyYAML，已使用降级解析"))

    for key in REQUIRED:
        if key not in fm or fm[key] in ("", None, []):
            errors.append((rel, f"缺少必填字段：{key}"))
    for key in RECOMMENDED:
        if key not in fm or fm[key] in ("", None, []):
            warnings.append((rel, f"建议补充字段：{key}（影响 SEO 与分享卡片）"))

    if fm.get("layout") not in (None, "post"):
        errors.append((rel, f"layout 应为 post，实际为 {fm.get('layout')}"))

    loose = re.match(r"^(\d{4}-\d{1,2}-\d{1,2})-(.+)\.md$", path.name)
    is_legacy = bool(loose and loose.group(1) < LEGACY_BEFORE)

    date = fm.get("date")
    date_str = str(date)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
        detail = f"date 格式应为 YYYY-MM-DD，实际为 {date_str}"
        (warnings if is_legacy else errors).append((rel, detail))

    m = re.match(r"^(\d{4}-\d{2}-\d{2})-(.+)\.md$", path.name)
    if not m:
        detail = "文件名不符合 YYYY-MM-DD-slug.md"
        (warnings if is_legacy else errors).append((rel, detail))
    else:
        if m.group(1) != date_str:
            detail = f"文件名日期 {m.group(1)} 与 front matter 日期 {date_str} 不一致"
            (warnings if is_legacy else errors).append((rel, detail))
        slug = m.group(2)
        if not is_legacy and not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
            errors.append((rel, f"新文章 slug 必须是小写 ASCII 短横线格式，实际为「{slug}」"))

    header = fm.get("header-img")
    if header:
        if not (ROOT / str(header)).exists():
            errors.append((rel, f"header-img 指向的文件不存在：{header}"))

    tags = fm.get("tags") or []
    if not isinstance(tags, list):
        errors.append((rel, "tags 必须是列表"))
        tags = []
    if len(tags) > 6:
        warnings.append((rel, f"标签过多（{len(tags)} 个），建议不超过 6 个"))
    if len(set(tags)) != len(tags):
        warnings.append((rel, "存在重复标签"))

    title = str(fm.get("title", "")).strip()
    if title:
        if len(title) > 60:
            warnings.append((rel, f"标题 {len(title)} 字，超过 60 字不利于搜索展示"))
        if title in seen_titles:
            errors.append((rel, f"标题与 {seen_titles[title]} 重复"))
        else:
            seen_titles[title] = rel

    noise_free = strip_noise(body)
    if noise_free.count("{%") != noise_free.count("%}"):
        errors.append((rel, "Liquid 标签 {%% %%} 未配对"))
    if noise_free.count("{{") != noise_free.count("}}"):
        errors.append((rel, "Liquid 变量 {{ }} 未配对"))

    for marker in PLACEHOLDERS:
        if marker in noise_free:
            errors.append((rel, f"正文残留占位符：{marker}"))

    for link in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", body):
        link = link.split(" ")[0].strip()
        if link.startswith(("http://", "https://", "//", "#", "mailto:")):
            continue
        local = link.lstrip("/").split("#")[0].split("?")[0]
        if not local or local.endswith("/"):
            continue
        if re.match(r"^\d{4}/\d{2}/", local):
            continue
        if not (ROOT / local).exists():
            errors.append((rel, f"站内链接指向的文件不存在：{link}"))

    cjk = len(re.findall(r"[\u4e00-\u9fff]", body))
    if cjk < 1200:
        warnings.append((rel, f"正文仅 {cjk} 个中文字，篇幅偏短"))
    if cjk == 0:
        errors.append((rel, "正文没有中文内容，疑似空文件"))

    for raw in re.findall(r"^##\s*第([零一二三四五六七八九十百\d]+)章", body, re.M):
        num = cn2int(raw)
        if num is None:
            continue
        if num in chapters:
            errors.append((rel, f"第 {num} 章 与 {chapters[num]} 重复"))
        else:
            chapters[num] = rel
    if not is_legacy and not re.search(r"^##\s", body, re.M):
        warnings.append((rel, "正文没有二级标题，目录与结构会单调"))


def main(argv):
    targets = gather_targets(argv)
    if not targets:
        print("没有需要检查的文章。")
        return 0

    errors, warnings = [], []
    chapters, titles = {}, {}

    for path in targets:
        check_one(path, errors, warnings, chapters, titles)

    all_chapters = {}
    for post in sorted(POSTS.glob("*.md")):
        _, body = split_front_matter(post.read_text(encoding="utf-8"))
        for raw in re.findall(r"^##\s*第([零一二三四五六七八九十百\d]+)章", body, re.M):
            num = cn2int(raw)
            if num is not None:
                all_chapters.setdefault(num, post.name)
    if all_chapters:
        nums = sorted(all_chapters)
        gaps = [n for n in range(1, nums[-1] + 1) if n not in all_chapters]
        if gaps:
            warnings.append(("系列", f"章节号不连续，缺少：{gaps}"))

    print(f"检查 {len(targets)} 篇\n")
    for rel, msg in warnings:
        print(f"  [警告] {rel}: {msg}")
    for rel, msg in errors:
        print(f"  [拦截] {rel}: {msg}")

    if errors:
        print(f"\n结果：拦截（{len(errors)} 个错误，{len(warnings)} 个警告）")
        return 1
    print(f"\n结果：通过（{len(warnings)} 个警告）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
