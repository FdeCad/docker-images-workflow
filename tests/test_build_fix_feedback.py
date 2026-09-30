"""build-fix 复验失败回流测试。

回归的是 2026-09-30 那次链路空转：arm64 复验失败后回退到 amd64 再修，但
`build_mode` 只把**最初的** ci-analysis 喂给 agent，复验的构建输出从未被读取。
于是 agent 每轮都把已经修好的部分再"修"一遍、交出同一份 Dockerfile，verify
撞上同一个真正的阻塞点再次失败——amd64 两轮分别只花 3m25s / 4m33s，而 verify
每次都在 `install_deps.sh` 上 43 秒失败。
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

VERIFY_FAILURE = """\
#11 [builder 4/4] RUN git clone -b v3.0.2 https://github.com/milvus-io/milvus.git && \
    cd milvus/ && ./scripts/install_deps.sh && make build-cpp
#11 46.17 [ERROR] Unsupported Linux distribution: openEuler
#11 ERROR: process "/bin/sh -c ..." did not complete successfully: exit code: 1
"""


def _load_build_fix():
    """build-fix.py 文件名带连字符，不能按模块名导入，只能按路径加载。"""
    path = ROOT / 'scripts' / 'stages' / 'build-fix.py'
    spec = importlib.util.spec_from_file_location('build_fix_under_test', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """把 build-fix 的外部依赖（仓库、ci-fix-log、agent、dispatch）全部替换为内存桩。"""
    mod = _load_build_fix()

    src = tmp_path / 'source-repo'
    src.mkdir()
    work_base = tmp_path / 'ci-fix-log'
    (work_base / '4723').mkdir(parents=True)

    monkeypatch.setattr(mod, 'SOURCE_REPO_DIR', str(src))
    monkeypatch.setattr(mod, 'WORK_BASE', str(work_base))
    monkeypatch.setattr(mod, '_get_pr_files', lambda env: ['Database/milvus/3.0.2/Dockerfile'])
    monkeypatch.setattr(mod, '_determine_target',
                        lambda env, files: 'Database/milvus/3.0.2/Dockerfile')

    def fake_read_file(path, branch=None):
        if path.endswith('derived-dockerfile'):
            return 'FROM openeuler/openeuler:24.03-lts-sp4\nRUN echo hi\n'
        if path.endswith('dockerfile-target'):
            return 'Database/milvus/3.0.2/Dockerfile'
        if path.endswith('build-log-verify-arm64.md'):
            return VERIFY_FAILURE
        if path.endswith('ci-analysis.md'):
            return '# CI 失败分析报告\n根因: MinIO 下载源 410\n'
        return ''

    monkeypatch.setattr(mod.ci_data, 'read_file', fake_read_file)
    monkeypatch.setattr(mod.ci_data, 'write_file', lambda *a, **k: None)

    captured = {}
    dispatched = []

    monkeypatch.setattr(mod, 'dispatch_phase', lambda payload: dispatched.append(payload))
    monkeypatch.setattr(mod.build_runner, 'container_name', lambda pr, arch: f'c-{pr}-{arch}')
    monkeypatch.setattr(mod.build_runner, 'docker_rm', lambda name: None)
    monkeypatch.setattr(mod.build_runner, 'base_image',
                        lambda content: 'openeuler/openeuler:24.03-lts-sp4')
    # 绝不能让测试真去调 docker
    monkeypatch.setattr(mod.build_runner, 'docker_build',
                        lambda target, ctx, **kw: subprocess.CompletedProcess(
                            ['docker', 'build'], 1, '', 'build failed'))

    def fake_run_agent(**kwargs):
        captured.update(kwargs)
        Path(kwargs['output_file']).write_text('# 构建修复日志\n', encoding='utf-8')
        # agent 被要求「每有进展就落盘」，这里模拟它交付了一份 derived Dockerfile
        (work_base / '4723' / 'derived-dockerfile').write_text(
            'FROM openeuler/openeuler:24.03-lts-sp4\nRUN echo hi\n', encoding='utf-8')
        return {'output_file': kwargs['output_file']}

    monkeypatch.setattr(mod, 'run_agent', fake_run_agent)

    def env(verify_count):
        return {
            'source_repo': 'openeuler/openeuler-docker-images',
            'source_platform': 'gitcode',
            'owner': 'openeuler',
            'repo': 'openeuler-docker-images',
            'pr_number': 4723,
            'pr_title': 'milvus 升级至 3.0.2',
            'pr_head_sha': 'deadbeef',
            'fix_branch': 'fix/4723',
            'pr_base_branch': 'master',
            'arch': 'amd64',
            'mode': 'build',
            'verify_count': verify_count,
            'token': 'x',
        }

    return mod, captured, dispatched, env


def test_verify_failure_reaches_the_agent_context(harness):
    """verify_count > 0 时，agent 必须拿到 arm64 复验的构建输出。"""
    mod, captured, _, env = harness
    mod.build_mode(env(verify_count=1))

    # build_mode 读入时会 strip 掉首尾空白
    assert captured['context']['arm64_verify_failure'] == VERIFY_FAILURE.strip()
    assert captured['context']['verify_round'] == 1
    # 指令要明确把它指为当前主线索，避免 agent 回头重复修 ci-analysis 里的旧问题
    assert 'arm64_verify_failure' in captured['instruction']
    assert '不要重复修' in captured['instruction']


def test_first_round_has_no_verify_feedback(harness):
    """首轮（verify_count == 0）没有复验输出，不应伪造一份出来。"""
    mod, captured, _, env = harness
    mod.build_mode(env(verify_count=0))

    assert captured['context']['arm64_verify_failure'] == '(无——本轮不是复验回修)'
    assert 'arm64_verify_failure' not in captured['instruction']


def test_later_round_still_dispatches_verify_arm(harness):
    """回修轮次跑完后仍应把 verify_count 原样传给 verify-arm，否则轮次计数会归零、无限循环。"""
    mod, _, dispatched, env = harness
    mod.build_mode(env(verify_count=1))

    assert len(dispatched) == 1
    assert dispatched[0]['phase'] == 'verify-arm'
    assert dispatched[0]['verify_count'] == '1'


def test_verify_exhaustion_does_not_comment_by_default(harness, monkeypatch):
    """连续失败终止链路时，默认**不**向源仓库回帖（outward-facing 动作不该是默认行为）。"""
    mod, _, _, env = harness
    monkeypatch.delenv('PR_COMMENT_ON_FAILURE', raising=False)

    commented = []
    fake_api = type('A', (), {'add_pr_comment': lambda *a, **k: commented.append(a)})
    monkeypatch.setattr(mod, 'get_api', lambda platform: fake_api)

    with pytest.raises(RuntimeError, match='human intervention'):
        mod.verify_mode({**env(verify_count=mod.MAX_VERIFY_ROUNDS), 'mode': 'verify'})

    assert commented == []


def test_verify_exhaustion_comments_when_explicitly_enabled(harness, monkeypatch):
    mod, _, _, env = harness
    monkeypatch.setenv('PR_COMMENT_ON_FAILURE', 'true')

    commented = []
    fake_api = type('A', (), {'add_pr_comment': lambda *a, **k: commented.append(a)})
    monkeypatch.setattr(mod, 'get_api', lambda platform: fake_api)

    with pytest.raises(RuntimeError, match='human intervention'):
        mod.verify_mode({**env(verify_count=mod.MAX_VERIFY_ROUNDS), 'mode': 'verify'})

    assert len(commented) == 1
    assert commented[0][0] == 'openeuler/openeuler-docker-images'
