#!/usr/bin/env python3
"""
ci-data 分支读写工具

存储结构：
  ci-fix-log 分支（per-PR 记录）：
    ci-fix-log/{pr_number}/ci-analysis.md       — CI 失败分析报告
    ci-fix-log/{pr_number}/code-fix-summary.md  — 修复摘要
    ci-fix-log/{pr_number}/fix-notified         — 已通知标记

  main 分支（共享知识库）：
    docs/ci-failure-patterns.md             — 积累的失败模式知识库
"""

import os
import re
import base64
import requests
from datetime import datetime

GITHUB_API   = "https://api.github.com"
CI_FIX_BRANCH = "ci-fix-log"
MAIN_BRANCH   = "main"


def _repo() -> str:
    return os.environ.get('GITHUB_REPOSITORY', 'sunshuang1866/docker-images-workflow')


def _token() -> str:
    return os.environ.get('DISPATCH_TOKEN') or os.environ.get('GITHUB_TOKEN', '')


def _headers() -> dict:
    return {
        'Authorization': f'token {_token()}',
        'Accept': 'application/vnd.github.v3+json',
    }


def _ensure_ci_fix_branch() -> None:
    """ci-fix-log 分支不存在时从 main 创建。"""
    url = f"{GITHUB_API}/repos/{_repo()}/git/refs/heads/{CI_FIX_BRANCH}"
    if requests.get(url, headers=_headers(), timeout=15).ok:
        return
    resp = requests.get(
        f"{GITHUB_API}/repos/{_repo()}/git/refs/heads/main",
        headers=_headers(), timeout=15,
    )
    resp.raise_for_status()
    sha = resp.json()['object']['sha']
    requests.post(
        f"{GITHUB_API}/repos/{_repo()}/git/refs",
        headers=_headers(),
        json={'ref': f'refs/heads/{CI_FIX_BRANCH}', 'sha': sha},
        timeout=15,
    ).raise_for_status()


def read_file(path: str, branch: str = CI_FIX_BRANCH) -> str:
    """从指定分支读取文件，不存在返回空字符串。"""
    url = f"{GITHUB_API}/repos/{_repo()}/contents/{path}"
    resp = requests.get(url, headers=_headers(), params={'ref': branch}, timeout=30)
    if resp.status_code == 404:
        return ''
    resp.raise_for_status()
    return base64.b64decode(resp.json()['content']).decode('utf-8')


def write_file(path: str, content: str, message: str, branch: str = CI_FIX_BRANCH) -> None:
    """向指定分支写入文件（自动处理创建/更新）。"""
    if branch == CI_FIX_BRANCH:
        _ensure_ci_fix_branch()
    url = f"{GITHUB_API}/repos/{_repo()}/contents/{path}"

    sha = None
    resp = requests.get(url, headers=_headers(), params={'ref': branch}, timeout=30)
    if resp.ok:
        sha = resp.json().get('sha')

    payload: dict = {
        'message': message,
        'content': base64.b64encode(content.encode('utf-8')).decode('ascii'),
        'branch': branch,
    }
    if sha:
        payload['sha'] = sha

    requests.put(url, headers=_headers(), json=payload, timeout=30).raise_for_status()


# ── per-PR 路径（ci-fix-log 分支）───────────────────────────────────────

def analysis_path(pr_number: int) -> str:
    return f"ci-fix-log/{pr_number}/ci-analysis.md"


def fix_summary_path(pr_number: int) -> str:
    return f"ci-fix-log/{pr_number}/code-fix-summary.md"


def fix_notified_path(pr_number: int) -> str:
    return f"ci-fix-log/{pr_number}/fix-notified"


def derived_dockerfile_path(pr_number: int) -> str:
    """build 阶段反推出的最终 Dockerfile 内容（arm 冻结版 → amd64 更新版）。"""
    return f"ci-fix-log/{pr_number}/derived-dockerfile"


def dockerfile_target_path(pr_number: int) -> str:
    """被修复的 Dockerfile 在源仓库中的相对路径（如 AI/mlflow/3.12.0/Dockerfile）。"""
    return f"ci-fix-log/{pr_number}/dockerfile-target"


def build_log_path(pr_number: int, arch: str) -> str:
    return f"ci-fix-log/{pr_number}/build-log-{arch}.md"


def is_fix_notified(pr_number: int) -> bool:
    return bool(read_file(fix_notified_path(pr_number), branch=CI_FIX_BRANCH))


def mark_fix_notified(pr_number: int) -> None:
    write_file(fix_notified_path(pr_number), "notified",
               f"fix-notified: PR #{pr_number}", branch=CI_FIX_BRANCH)


# ── 共享知识库（main 分支）───────────────────────────────────────────────

KNOWLEDGE_PATH = "docs/ci-failure-patterns.md"

KNOWLEDGE_HEADER = """\
# CI 失败模式知识库

> **按失败模式分类**，每个模式包含：典型报错、根因分析、修复方法、历史案例。
> 处理新失败 PR 时，**用报错关键词搜索对应章节**，直接找到修复方法。

"""


def read_knowledge() -> str:
    """从 main 分支读取知识库。"""
    return read_file(KNOWLEDGE_PATH, branch=MAIN_BRANCH)


def _extract_field(text: str, field: str) -> str:
    for line in text.splitlines():
        if field in line and ':' in line:
            val = line.split(':', 1)[-1].strip().lstrip('*').strip()
            if val:
                return val
    return ''


def _extract_section(text: str, heading: str) -> str:
    lines = text.splitlines()
    capturing = False
    result = []
    for line in lines:
        if re.match(r'#+\s*' + re.escape(heading), line):
            capturing = True
            continue
        if capturing:
            if line.startswith('#'):
                break
            result.append(line)
    return '\n'.join(result).strip()[:300]


def _build_case_line(pr_number: int, changed_files: str, fix_desc: str) -> str:
    """构造一条历史案例行，如：- PR #2266: `AI/mlflow/3.12.0` — 添加 shadow 依赖"""
    # 取第一个反引号包裹的路径，或第一行去掉前缀
    file_match = re.search(r'`([^`]+)`', changed_files)
    if file_match:
        file_path = file_match.group(1)
    else:
        first = changed_files.split('\n')[0].lstrip('- ').strip()
        file_path = first.split(':')[0].strip() if ':' in first else first
    short_desc = (fix_desc.split('\n')[0].strip())[:60]
    return f'- PR #{pr_number}: `{file_path}` — {short_desc}'


def _count_patterns(content: str) -> int:
    return len(re.findall(r'^## 模式\d+[：:]', content, re.MULTILINE))


def _insert_case_into_pattern(content: str, pattern_num: int, case_line: str) -> str | None:
    """
    在指定模式章节的"历史案例"列表末尾插入 case_line。
    找不到该模式章节时返回 None。
    """
    pattern_id = f'{pattern_num:02d}'
    lines = content.splitlines(keepends=True)

    # 定位模式章节起止行
    section_start = None
    for i, line in enumerate(lines):
        if re.match(rf'^## 模式{pattern_id}[：:]', line):
            section_start = i
            break
    if section_start is None:
        return None

    section_end = len(lines)
    for i in range(section_start + 1, len(lines)):
        if re.match(r'^## ', lines[i]):
            section_end = i
            break

    # 在章节内找 **历史案例**: 后的最后一条列表项
    cases_heading_idx = None
    last_case_idx = None
    for i in range(section_start, section_end):
        stripped = lines[i].rstrip()
        if '**历史案例**' in stripped:
            cases_heading_idx = i
        elif cases_heading_idx is not None and stripped.strip().startswith('- '):
            last_case_idx = i

    insert_after = last_case_idx if last_case_idx is not None else cases_heading_idx
    if insert_after is None:
        return None

    lines.insert(insert_after + 1, case_line + '\n')
    return ''.join(lines)


def _append_new_pattern(content: str, pr_number: int, title: str, keywords: str,
                        root_cause: str, fix_desc: str, case_line: str) -> str:
    """在知识库末尾追加一个全新的模式章节。"""
    next_num = _count_patterns(content) + 1
    pattern_id = f'{next_num:02d}'
    keywords = keywords or '(待补充)'
    root_cause = root_cause or '(待补充)'
    fix_desc = fix_desc or '(待补充)'

    section = f"""
---

## 模式{pattern_id}：{title}

**症状关键词**: {keywords}

**根因**: {root_cause}

**修复方法**: {fix_desc}

**历史案例**:
{case_line}
"""
    return content.rstrip('\n') + '\n' + section


def append_pattern(pr_number: int, repo: str, analysis: str, fix_summary: str) -> None:
    """
    将新 PR 的失败模式写入 main 分支知识库：
    - 若分析报告标注匹配已有模式，向该模式的"历史案例"追加一行；
    - 若标注为新模式，在文件末尾新建模式章节。
    """
    # 从分析报告提取归类字段
    match_field = _extract_field(analysis, '知识库匹配')   # e.g. '模式05' or '新模式'
    new_title    = _extract_field(analysis, '新模式标题')
    new_keywords = _extract_field(analysis, '新模式症状关键词')
    root_cause   = _extract_section(analysis, '根因定位')

    # 从修复摘要提取
    fix_desc      = _extract_section(fix_summary, '修复的问题')
    changed_files = _extract_section(fix_summary, '修改的文件')

    case_line = _build_case_line(pr_number, changed_files, fix_desc)

    existing = read_knowledge()
    if not existing:
        existing = KNOWLEDGE_HEADER

    updated = None

    # 尝试匹配已有模式
    num_match = re.search(r'(\d+)', match_field) if match_field else None
    if num_match and '新模式' not in match_field:
        pattern_num = int(num_match.group(1))
        updated = _insert_case_into_pattern(existing, pattern_num, case_line)
        if updated is None:
            # 模式章节在文件中找不到，退化为新建
            pass

    # 新模式或插入失败时，新建章节
    if updated is None:
        title = new_title or _extract_field(analysis, '失败类型') or '未分类失败'
        updated = _append_new_pattern(existing, pr_number, title, new_keywords,
                                      root_cause, fix_desc, case_line)

    write_file(
        KNOWLEDGE_PATH,
        updated,
        f'knowledge: add pattern from {repo} PR #{pr_number}',
        branch=MAIN_BRANCH,
    )
