#!/usr/bin/env python3
"""
手动触发入口：读取 PR 号 + 目标仓库，拉取 PR 详情后 dispatch ci-log-analysis 阶段。

由 workflow_dispatch 触发（pr-ci-fix-trigger.yml 的 manual-trigger job）。
"""

import os
import sys
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, PROJECT_ROOT)

from scripts.lib.ci_api import get_api, normalize_repo
from scripts.lib.stage_common import dispatch_phase, log_stage


def _resolve_platform(source_platform: str, repo: str) -> str:
    """优先从仓库 URL 识别平台，其次用输入值，最后退回 github。"""
    if 'gitcode.com' in repo:
        return 'gitcode'
    if 'github.com' in repo:
        return 'github'
    return source_platform or 'github'


def parse_env() -> dict:
    source_repo = os.getenv('SOURCE_REPO', '')
    pr_number = os.getenv('PR_NUMBER', '')
    if not source_repo or not pr_number:
        raise ValueError('SOURCE_REPO and PR_NUMBER are required')
    platform = _resolve_platform(os.getenv('SOURCE_PLATFORM', ''), source_repo)
    source_repo = normalize_repo(source_repo, platform)
    token = os.getenv('GITHUB_TOKEN', '')
    if platform == 'gitcode':
        token = os.getenv('GITCODE_TOKEN') or token
    return {
        'source_repo': source_repo,
        'source_platform': platform,
        'pr_number': int(pr_number),
        'token': token,
    }


def main():
    env = parse_env()
    api = get_api(env['source_platform'])
    log_stage('manual-trigger',
              f"repo={env['source_repo']} pr=#{env['pr_number']} platform={env['source_platform']}")

    pr = api.get_pr_detail(env['source_repo'], env['pr_number'], env['token'])
    if not pr:
        raise RuntimeError(f"PR #{env['pr_number']} not found in {env['source_repo']}")

    head_sha = (pr.get('head') or {}).get('sha', '')
    title = pr.get('title', '')
    base_ref = (pr.get('base') or {}).get('ref', 'main')

    if not head_sha:
        raise RuntimeError(f"PR #{env['pr_number']} has no head.sha — 无法继续")

    dispatch_phase({
        'phase': 'ci-log-analysis',
        'source_repo': env['source_repo'],
        'source_platform': env['source_platform'],
        'pr_number': env['pr_number'],
        'pr_title': title,
        'head_sha': head_sha,
        'fix_branch': f"fix/{env['pr_number']}",
        'pr_base_branch': base_ref,
        'fix_pr_number': '0',
    })
    log_stage('manual-trigger', f'✅ dispatched ci-log-analysis (sha={head_sha[:8]}, base={base_ref})')


if __name__ == '__main__':
    main()
