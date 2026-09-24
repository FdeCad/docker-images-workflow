#!/usr/bin/env python3
"""
Stage: 容器构建修复（build-fix / verify-arm）

build 模式：
  AI builder agent 在自托管 runner 上：起容器 → 逐条执行 Dockerfile 指令 → 失败即修 →
  反推 Dockerfile → docker build 验证。arm64 首修；amd64 在 arm 冻结版上继续修。
verify 模式：
  在 arm64 上对最终 Dockerfile 做确定性 docker build 复验（无 AI）。

输入: PR 信息 + ci-analysis 提示 + (amd64/verify 时) 前序 derived Dockerfile
输出: derived-dockerfile / dockerfile-target / build-log-<arch>（写入 ci-fix-log 分支）
副作用: dispatch 下一阶段
"""

import os
import sys
import subprocess
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, PROJECT_ROOT)

from scripts.lib.ai_runner import run_agent
from scripts.lib.stage_common import agent_prompt_file, log_stage, get_conventions_file, dispatch_phase
from scripts.lib.ci_api import get_api
from scripts.lib import ci_data
from scripts.lib import build_runner

WORK_BASE = os.path.join(PROJECT_ROOT, 'ci-fix-log')
SOURCE_REPO_DIR = os.path.join(PROJECT_ROOT, 'source-repo')

# arm 复验失败后的最大回修轮数（amd64 → verify → amd64 → verify → 放弃）
MAX_VERIFY_ROUNDS = 2


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
        'arch': os.getenv('ARCH', 'arm64'),
        'mode': os.getenv('MODE', 'build'),
        'verify_count': int(os.getenv('VERIFY_COUNT', '0')),
        'token': token,
    }


def git(args: list, cwd: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(['git'] + args, cwd=cwd, check=check, capture_output=True, text=True)


def _get_pr_files(env: dict) -> list:
    """返回原始 PR 涉及的文件列表（git diff 优先，API fallback）。"""
    result = git(['diff', '--name-only', f"origin/{env['pr_base_branch']}...{env['pr_head_sha']}"],
                 cwd=SOURCE_REPO_DIR, check=False)
    pr_files = [f.strip() for f in (result.stdout or '').strip().splitlines() if f.strip()] \
        if result.returncode == 0 else []
    if pr_files:
        return pr_files
    try:
        api = get_api(env['source_platform'])
        pr_files = api.get_pr_file_names(env['source_repo'], env['pr_number'], env['token'])
        log_stage('build-fix', f'PR files via API: {len(pr_files)} file(s)')
    except Exception as e:
        log_stage('build-fix', f'⚠️ API file list fallback failed: {e}')
    return pr_files


def _determine_target(env: dict, pr_files: list) -> str:
    """确定要修复的 Dockerfile 在源仓库中的相对路径。

    amd64/verify 阶段复用 arm 阶段写入 ci-fix-log 的 dockerfile-target；
    arm64 首修阶段从 PR 文件列表推断。
    """
    target = ci_data.read_file(ci_data.dockerfile_target_path(env['pr_number']))
    if target.strip():
        return target.strip()

    target = build_runner.find_dockerfile_target(pr_files)
    if not target:
        raise RuntimeError(
            f'无法唯一定位 Dockerfile（PR 文件: {pr_files}）。'
            '请确认 PR 只改了一个 Dockerfile，或手动写入 dockerfile-target。'
        )
    return target


def _apply_derived_to_repo(target: str, derived: str) -> str:
    """把 derived Dockerfile 内容写回源仓库对应路径，返回绝对路径。"""
    target_abs = os.path.join(SOURCE_REPO_DIR, target)
    Path(target_abs).parent.mkdir(parents=True, exist_ok=True)
    with open(target_abs, 'w', encoding='utf-8') as f:
        f.write(derived)
    return target_abs


def _base_payload(env: dict) -> dict:
    return {
        'source_repo': env['source_repo'],
        'source_platform': env['source_platform'],
        'pr_number': env['pr_number'],
        'pr_title': env['pr_title'],
        'pr_head_sha': env['pr_head_sha'],
        'fix_branch': env['fix_branch'],
        'pr_base_branch': env['pr_base_branch'],
    }


def build_mode(env: dict):
    pr = env['pr_number']
    arch = env['arch']
    work_dir = os.path.join(WORK_BASE, str(pr))
    Path(work_dir).mkdir(parents=True, exist_ok=True)

    output_file = os.path.join(work_dir, f'build-log-{arch}.md')
    derived_file = os.path.join(work_dir, 'derived-dockerfile')

    log_stage('build-fix', f"repo={env['source_repo']} pr=#{pr} arch={arch} mode=build")

    if not os.path.isdir(SOURCE_REPO_DIR):
        raise RuntimeError(f'source-repo directory not found: {SOURCE_REPO_DIR}')

    pr_files = _get_pr_files(env)
    log_stage('build-fix', f'PR touched {len(pr_files)} file(s): {pr_files}')

    target = _determine_target(env, pr_files)
    log_stage('build-fix', f'dockerfile target: {target}')

    # amd64：把 arm 冻结的 derived Dockerfile 写回仓库，作为本次构建的起点
    if arch == 'amd64':
        derived = ci_data.read_file(ci_data.derived_dockerfile_path(pr))
        if not derived:
            raise RuntimeError('derived-dockerfile missing — arm64 build-fix must run first')
        _apply_derived_to_repo(target, derived)
        log_stage('build-fix', 'applied arm64-derived Dockerfile as amd64 starting point')

    analysis = ci_data.read_file(ci_data.analysis_path(pr)) or '(无诊断提示)'

    dockerfile_abs = os.path.join(SOURCE_REPO_DIR, target)
    with open(dockerfile_abs, 'r', encoding='utf-8') as f:
        dockerfile_content = f.read()
    base_img = build_runner.base_image(dockerfile_content) or '(unknown)'

    if os.path.exists(derived_file):
        os.remove(derived_file)

    container = build_runner.container_name(pr, arch)

    context = {
        'pr': {
            'number': pr,
            'title': env['pr_title'],
            'changed_files': pr_files,
        },
        'arch': arch,
        'dockerfile_path': target,
        'base_image': base_img,
        'ci_analysis': analysis,
    }

    instruction = (
        f'修复 Dockerfile `{target}` 在当前架构 `{arch}` 上的构建失败。'
        f'容器名用 `{container}`，基础镜像 `{base_img}`。'
        f'把最终 Dockerfile 完整内容写入 derived_file（绝对路径 `{derived_file}`，纯文本、无代码围栏）。'
        f'把构建日志写入 output_file（绝对路径 `{output_file}`）。'
    )
    if arch == 'amd64':
        instruction += (
            ' 注意：当前 Dockerfile 是 arm64 已验证通过的版本，你的修复必须架构中立或用架构守卫'
            '（如 if [ "$(uname -m)" = "x86_64" ]），不得破坏 arm64 构建。'
        )

    try:
        run_agent(
            prompt_file=agent_prompt_file('dockerfile-builder'),
            context=context,
            instruction=instruction,
            work_dir=SOURCE_REPO_DIR,
            output_file=output_file,
            label=f'build-fix-{arch}',
            conventions_file=get_conventions_file(),
        )
    finally:
        # 无论成败都清理容器，避免在自托管 runner 上残留
        build_runner.docker_rm(container)

    if not os.path.exists(derived_file):
        raise RuntimeError(f'derived Dockerfile not produced: {derived_file}')

    with open(derived_file, 'r', encoding='utf-8') as f:
        derived = f.read().strip()
    if not derived:
        raise RuntimeError('derived Dockerfile is empty')

    ci_data.write_file(ci_data.derived_dockerfile_path(pr), derived,
                       f"build-fix: {env['source_repo']} PR #{pr} arch={arch}")
    ci_data.write_file(ci_data.dockerfile_target_path(pr), target,
                       f"build-fix: dockerfile target PR #{pr}")
    with open(output_file, 'r', encoding='utf-8') as f:
        build_log = f.read()
    ci_data.write_file(ci_data.build_log_path(pr, arch), build_log,
                       f"build-fix: build log PR #{pr} arch={arch}")
    log_stage('build-fix', '✅ derived Dockerfile + build log written to ci-fix-log')

    if arch == 'arm64':
        payload = _base_payload(env)
        payload.update({'phase': 'build-fix', 'arch': 'amd64', 'verify_count': '0'})
        dispatch_phase(payload)
    else:
        payload = _base_payload(env)
        payload.update({'phase': 'verify-arm', 'arch': 'arm64', 'verify_count': str(env['verify_count'])})
        dispatch_phase(payload)
    log_stage('build-fix', '✅ done')


def verify_mode(env: dict):
    pr = env['pr_number']
    arch = 'arm64'  # verify 固定发生在 arm64
    verify_log_name = f'verify-{arch}'
    log_stage('build-fix', f"repo={env['source_repo']} pr=#{pr} mode=verify round={env['verify_count']}")

    if not os.path.isdir(SOURCE_REPO_DIR):
        raise RuntimeError(f'source-repo directory not found: {SOURCE_REPO_DIR}')

    derived = ci_data.read_file(ci_data.derived_dockerfile_path(pr))
    target = ci_data.read_file(ci_data.dockerfile_target_path(pr)).strip()
    if not derived or not target:
        raise RuntimeError('derived-dockerfile / dockerfile-target missing — build-fix must run first')

    _apply_derived_to_repo(target, derived)

    log_stage('build-fix', f'verify: docker build -f {target} on {arch}')
    try:
        result = build_runner.docker_build(target, SOURCE_REPO_DIR)
    except Exception as e:
        result = None
        log_stage('build-fix', f'⚠️ docker build raised: {e}')

    log_text = ''
    if result is not None:
        log_text = ((result.stdout or '') + '\n' + (result.stderr or ''))[-20000:]
    ci_data.write_file(ci_data.build_log_path(pr, verify_log_name), log_text,
                       f"verify-arm: build log PR #{pr}")
    ok = result is not None and result.returncode == 0

    if ok:
        payload = _base_payload(env)
        payload.update({'phase': 'code-fix'})
        dispatch_phase(payload)
        log_stage('build-fix', '✅ verify passed → code-fix')
    elif env['verify_count'] < MAX_VERIFY_ROUNDS:
        # arm 复验失败：回 amd64 修，要求架构中立
        payload = _base_payload(env)
        payload.update({
            'phase': 'build-fix',
            'arch': 'amd64',
            'verify_count': str(env['verify_count'] + 1),
        })
        dispatch_phase(payload)
        log_stage('build-fix', f"⚠️ verify failed → back to amd64 (round {env['verify_count'] + 1})")
    else:
        api = get_api(env['source_platform'])
        try:
            api.add_pr_comment(
                env['source_repo'], pr,
                f'⚠️ 自动修复在 arm64 复验阶段连续失败（round {env["verify_count"]}），请人工介入。'
                f'构建日志见 ci-fix-log 分支 `{ci_data.build_log_path(pr, verify_log_name)}`。',
                env['token'],
            )
        except Exception as e:
            log_stage('build-fix', f'⚠️ comment PR failed: {e}')
        raise RuntimeError(f'verify failed after {env["verify_count"]} rounds — human intervention needed')


def main():
    env = parse_env()
    if env['mode'] == 'verify':
        verify_mode(env)
    else:
        build_mode(env)


if __name__ == '__main__':
    main()
