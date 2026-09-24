#!/usr/bin/env python3
"""
Stage 1: CI 日志分析

输入: PR 信息 + CI 失败日志
输出: $HOME/tech-design-data/ci-fix/<pr_number>/ci-analysis.md
副作用: 评论 PR，dispatch code-fix 阶段
"""

import os
import sys
import json
import requests
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, PROJECT_ROOT)

from scripts.lib.ai_runner import run_agent
from scripts.lib.stage_common import agent_prompt_file, log_stage, get_conventions_file, dispatch_phase
from scripts.lib.ci_api import get_api
from scripts.lib import ci_data

WORK_BASE = os.path.join(PROJECT_ROOT, 'ci-fix-log')


def parse_env() -> dict:
    source_repo = os.getenv('SOURCE_REPO', '')
    pr_number = int(os.getenv('PR_NUMBER', '0'))
    if not source_repo or not pr_number:
        raise ValueError('SOURCE_REPO and PR_NUMBER are required')
    owner, repo = source_repo.split('/', 1)
    platform = os.getenv('SOURCE_PLATFORM', 'github')
    # GitCode 优先用 GITCODE_TOKEN，fallback GITHUB_TOKEN
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
        'head_sha': os.getenv('HEAD_SHA', ''),
        'fix_branch': os.getenv('FIX_BRANCH', f'fix/{pr_number}'),
        'pr_base_branch': os.getenv('PR_BASE_BRANCH', 'main'),
        # 重试时为 fix PR 编号（CI 评论在 fix PR 上），首次为 0（评论在原始 PR 上）
        'fix_pr_number': int(os.getenv('FIX_PR_NUMBER', '0')),
        'token': token,
        'dispatch_token': os.getenv('GITHUB_TOKEN', ''),
    }


def dispatch_build_fix(env: dict):
    """推进到 build-fix 阶段（arm64 首修）。"""
    dispatch_phase({
        'phase': 'build-fix',
        'arch': 'arm64',
        'source_repo': env['source_repo'],
        'source_platform': env['source_platform'],
        'pr_number': env['pr_number'],
        'pr_title': env['pr_title'],
        'pr_head_sha': env['head_sha'],
        'fix_branch': env['fix_branch'],
        'pr_base_branch': env['pr_base_branch'],
    })


def main():
    env = parse_env()
    work_dir = os.path.join(WORK_BASE, str(env['pr_number']))
    Path(work_dir).mkdir(parents=True, exist_ok=True)
    output_file = os.path.join(work_dir, 'ci-analysis.md')

    log_stage('ci-log-analysis', f"repo={env['source_repo']} [{env['source_platform']}] pr=#{env['pr_number']}")

    api = get_api(env['source_platform'])

    # 获取 PR diff
    log_stage('ci-log-analysis', 'fetching PR diff...')
    pr_diff = ''
    try:
        pr_diff = api.get_pr_diff(env['source_repo'], env['pr_number'], env['token'])
        log_stage('ci-log-analysis', f'PR diff: {len(pr_diff)} chars')
    except Exception as e:
        log_stage('ci-log-analysis', f'⚠️ PR diff fetch failed: {e}')

    # 获取 CI 失败日志
    # 重试时：head_sha 指向 fix branch 最新 commit，CI 机器人将 build URL 评论在 fix PR 上；
    # 首次时：head_sha 是原始 PR 的 HEAD，CI 评论在原始 PR 上。
    comment_pr_number = env['fix_pr_number'] if env['fix_pr_number'] else env['pr_number']
    log_stage('ci-log-analysis', f"fetching CI logs for sha={env['head_sha'][:8]} "
              f"(comment lookup PR=#{comment_pr_number})...")
    ci_logs = ''
    run_info = ''
    try:
        run = api.get_latest_failed_run(
            env['source_repo'], env['head_sha'], env['token'],
            pr_number=comment_pr_number,
        )
        if run:
            run_info = f"Pipeline/Run: {run.get('name', run.get('id', ''))}, id={run['id']}"
            log_stage('ci-log-analysis', f'Found failed run: {run_info}')
            ci_logs = api.get_failed_job_logs(
                env['source_repo'], run['id'], env['token'],
                target_url=run.get('target_url', ''),
            )
            log_stage('ci-log-analysis', f'CI logs: {len(ci_logs)} chars')
        else:
            log_stage('ci-log-analysis', '⚠️ No failed CI run found for this SHA')
    except Exception as e:
        log_stage('ci-log-analysis', f'⚠️ CI log fetch failed: {e}')

    # 读取历史失败模式知识库
    knowledge = ''
    try:
        knowledge = ci_data.read_knowledge()
        if knowledge:
            log_stage('ci-log-analysis', f'knowledge base: {len(knowledge)} chars')
        else:
            log_stage('ci-log-analysis', 'knowledge base: empty (first run)')
    except Exception as e:
        log_stage('ci-log-analysis', f'⚠️ knowledge base read failed: {e}')

    context = {
        'pr': {
            'number': env['pr_number'],
            'title': env['pr_title'],
            'diff': pr_diff or '(not available)',
        },
        'ci': {
            'run_info': run_info or '(not available)',
            'logs': ci_logs or '(not available — analyze based on PR diff only)',
        },
        'historical_patterns': knowledge or '(暂无历史记录)',
    }

    run_agent(
        prompt_file=agent_prompt_file('ci-failure-analyst'),
        context=context,
        instruction='根据 CI 日志和 PR diff，产出完整的 CI 失败分析报告。',
        work_dir=work_dir,
        output_file=output_file,
        label='ci-log-analysis',
        conventions_file=get_conventions_file(),
    )

    with open(output_file, 'r', encoding='utf-8') as f:
        analysis = f.read()

    # 写入 ci-data 分支（完整内容，不截断）
    try:
        ci_data.write_file(
            ci_data.analysis_path(env['pr_number']),
            analysis,
            f"ci-analysis: {env['source_repo']} PR #{env['pr_number']}",
        )
        log_stage('ci-log-analysis', '✅ analysis written to ci-data branch')
    except Exception as e:
        log_stage('ci-log-analysis', f'⚠️ ci-data write failed: {e}')

    # 自动推进到 build-fix 阶段（analysis 已在 ci-data 分支，作为 build agent 的提示）
    dispatch_build_fix(env)
    log_stage('ci-log-analysis', '✅ done')


if __name__ == '__main__':
    main()
