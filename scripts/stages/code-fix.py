#!/usr/bin/env python3
"""
Stage 4: 应用修复（code-fix）

输入: build 阶段反推出的最终 Dockerfile（ci-fix-log 分支的 derived-dockerfile）
输出: 源码修改（git commit）+ 修复摘要文件
副作用: 评论 PR

注：本阶段不再调用 AI 现改代码，而是把 build-fix 阶段「在容器里验证通过」的
Dockerfile 作为最小 diff 落回源码树，再走既有的 commit / push / 建 PR 流程。
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, PROJECT_ROOT)

from scripts.lib.stage_common import log_stage
from scripts.lib.ci_api import get_api
from scripts.lib import ci_data

WORK_BASE = os.path.join(PROJECT_ROOT, 'ci-fix-log')
SOURCE_REPO_DIR = os.path.join(PROJECT_ROOT, 'source-repo')


def parse_env() -> dict:
    source_repo = os.getenv('SOURCE_REPO', '')
    pr_number = int(os.getenv('PR_NUMBER', '0'))
    if not source_repo or not pr_number:
        raise ValueError('SOURCE_REPO and PR_NUMBER are required')
    owner, repo = source_repo.split('/', 1)
    platform = os.getenv('SOURCE_PLATFORM', 'github')
    token = os.getenv('GITHUB_TOKEN', '')
    if platform == 'gitcode':
        token = os.getenv('GITCODE_TOKEN') or token
    return {
        'source_repo': source_repo,
        'source_platform': platform,
        'owner': owner,
        'repo': repo,
        'pr_number': pr_number,
        'pr_title': os.getenv('PR_TITLE', ''),
        'pr_head_sha': os.getenv('PR_HEAD_SHA', ''),
        'fix_branch': os.getenv('FIX_BRANCH', f'fix/{pr_number}'),
        'pr_base_branch': os.getenv('PR_BASE_BRANCH', 'main'),
        'token': token,
    }


def git(args: list, cwd: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(['git'] + args, cwd=cwd, check=check,
                          capture_output=True, text=True)


def has_changes(cwd: str) -> bool:
    result = git(['diff', '--cached', '--quiet'], cwd=cwd, check=False)
    return result.returncode != 0


def get_pr_file_list(cwd: str, pr_head_sha: str, base_branch: str) -> list:
    """返回原始 PR diff 中涉及的文件列表。"""
    result = git(
        ['diff', '--name-only', f'origin/{base_branch}...{pr_head_sha}'],
        cwd=cwd, check=False
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []
    return [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]


AI_ARTIFACT_DIRS = {'.claude', '.opencode', '__pycache__', '.aider', '.agents'}
AI_ARTIFACT_SUFFIXES = {'.pyc', '.pyo'}


def _is_ai_artifact(filepath: str) -> bool:
    """判断文件路径是否属于 AI 工具产物，应禁止提交。"""
    parts = Path(filepath).parts
    return any(part in AI_ARTIFACT_DIRS for part in parts) or \
           any(filepath.endswith(s) for s in AI_ARTIFACT_SUFFIXES)


def _cleanup_ai_artifacts(repo_dir: str):
    root = Path(repo_dir)
    for name in AI_ARTIFACT_DIRS:
        for p in root.rglob(name):
            if p.is_dir():
                shutil.rmtree(p)
                log_stage('code-fix', f'removed dir: {p.relative_to(root)}')
            elif p.is_file():
                p.unlink()
                log_stage('code-fix', f'removed file: {p.relative_to(root)}')
    for suffix in AI_ARTIFACT_SUFFIXES:
        for p in root.rglob(f'*{suffix}'):
            p.unlink()
            log_stage('code-fix', f'removed file: {p.relative_to(root)}')


def _extract_fix_summary(build_log: str, pr_number: int) -> str:
    """从 build 日志提取「修复摘要」段；找不到则回退到日志首个正文行。"""
    if build_log:
        lines = build_log.splitlines()
        capturing = False
        collected = []
        for line in lines:
            if line.strip().startswith('#') and '修复摘要' in line:
                capturing = True
                continue
            if capturing:
                if line.strip().startswith('#'):
                    break
                if line.strip():
                    collected.append(line.strip())
        if collected:
            return '\n'.join(collected)
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith('#'):
                return stripped
    return f'自动容器构建修复 PR #{pr_number} 的 CI 失败'


def main():
    env = parse_env()
    pr = env['pr_number']
    work_dir = os.path.join(WORK_BASE, str(pr))
    Path(work_dir).mkdir(parents=True, exist_ok=True)
    output_file = os.path.join(work_dir, 'code-fix-summary.md')

    log_stage('code-fix', f"repo={env['source_repo']} [{env['source_platform']}] pr=#{pr}")

    if not os.path.isdir(SOURCE_REPO_DIR):
        raise RuntimeError(f'source-repo directory not found: {SOURCE_REPO_DIR}')

    # 读取 build 阶段反推出的最终 Dockerfile、目标路径与构建日志
    derived = ci_data.read_file(ci_data.derived_dockerfile_path(pr))
    target = ci_data.read_file(ci_data.dockerfile_target_path(pr)).strip()
    build_log = ci_data.read_file(ci_data.build_log_path(pr, 'amd64')) or \
                ci_data.read_file(ci_data.build_log_path(pr, 'arm64'))

    if not derived or not target:
        raise RuntimeError('derived-dockerfile / dockerfile-target missing — build-fix must run before code-fix')

    # 安全校验：目标 Dockerfile 必须是原始 PR 涉及的文件
    pr_files = get_pr_file_list(SOURCE_REPO_DIR, env['pr_head_sha'], env['pr_base_branch'])
    if not pr_files:
        log_stage('code-fix', '⚠️ git diff empty — trying platform API for file list...')
        try:
            api = get_api(env['source_platform'])
            pr_files = api.get_pr_file_names(env['source_repo'], pr, env['token'])
        except Exception as e:
            log_stage('code-fix', f'⚠️ API file list fallback failed: {e}')
    log_stage('code-fix', f'PR touched {len(pr_files)} file(s): {pr_files}')

    if target not in pr_files:
        raise RuntimeError(f'derived Dockerfile target `{target}` 不在 PR 文件列表内，拒绝提交')

    # 把最终 Dockerfile 写回源码树（最小 diff 的落点）
    target_abs = os.path.join(SOURCE_REPO_DIR, target)
    Path(target_abs).parent.mkdir(parents=True, exist_ok=True)
    with open(target_abs, 'w', encoding='utf-8') as f:
        f.write(derived)
    log_stage('code-fix', f'wrote derived Dockerfile → {target}')

    # 清理 build 阶段可能残留的 AI 工具产物
    _cleanup_ai_artifacts(SOURCE_REPO_DIR)

    # 只暂存原始 PR 涉及的文件，严禁暂存任何其他文件
    pr_files_set = set(pr_files)
    for f in pr_files:
        git(['add', '--', f], cwd=SOURCE_REPO_DIR, check=False)

    staged_result = git(['diff', '--cached', '--name-only'], cwd=SOURCE_REPO_DIR)
    staged_files = [f for f in staged_result.stdout.strip().splitlines() if f.strip()]
    extra = [f for f in staged_files if f not in pr_files_set]
    if extra:
        log_stage('code-fix', f'⚠️ unstaging {len(extra)} unexpected file(s): {extra}')
        for f in extra:
            git(['restore', '--staged', '--', f], cwd=SOURCE_REPO_DIR, check=False)

    summary = _extract_fix_summary(build_log, pr)
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(summary)

    if not has_changes(SOURCE_REPO_DIR):
        log_stage('code-fix', 'ℹ️ no code changes — derived Dockerfile identical to PR head')
        try:
            ci_data.write_file(
                ci_data.fix_summary_path(pr), summary,
                f"code-fix: {env['source_repo']} PR #{pr} (no changes)",
            )
        except Exception as e:
            log_stage('code-fix', f'⚠️ ci-data write failed (non-fatal): {e}')
        gh_output = os.environ.get('GITHUB_OUTPUT', '')
        if gh_output:
            with open(gh_output, 'a') as _f:
                _f.write('no_changes=true\n')
        log_stage('code-fix', '✅ done (no commit)')
        return

    first_line = summary.split('\n')[0].lstrip('#').strip()[:72] or f'rebuild Dockerfile for PR #{pr}'
    commit_msg = (
        f"fix(ci): {first_line}\n\n"
        f"Automated container-build fix for CI failure in PR #{pr}\n\n"
        f"🤖 Generated by ci-fix-team"
    )

    git(['commit', '-m', commit_msg], cwd=SOURCE_REPO_DIR)
    log_stage('code-fix', '✅ committed')

    try:
        ci_data.write_file(
            ci_data.fix_summary_path(pr), summary,
            f"code-fix: {env['source_repo']} PR #{pr}",
        )
        log_stage('code-fix', '✅ fix summary written to ci-fix-log branch')
    except Exception as e:
        log_stage('code-fix', f'⚠️ ci-data write failed (non-fatal): {e}')

    log_stage('code-fix', '✅ done')


if __name__ == '__main__':
    main()
