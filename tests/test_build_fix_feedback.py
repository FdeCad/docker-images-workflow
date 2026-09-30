"""build-fix 复验失败回流测试。

回归的是 2026-09-30 那次链路空转：arm64 复验失败后回退到 amd64 再修，但
`build_mode` 只把**最初的** ci-analysis 喂给 agent，复验的构建输出从未被读取。
于是 agent 每轮都把已经修好的部分再"修"一遍、交出同一份 Dockerfile，verify
撞上同一个真正的阻塞点再次失败——amd64 两轮分别只花 3m25s / 4m33s，而 verify
每次都在 `install_deps.sh` 上 43 秒失败。
"""

import importlib.util
import os
import subprocess
import sys
import time
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
            'chain_id': '36677467180',
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


def test_chain_id_is_carried_into_next_dispatch(harness):
    """回修轮次 dispatch 下一个阶段时必须带上原 chain_id。

    带丢不会有任何报错——`dispatch_phase` 会把空值填成**当前 run 的 id**，
    于是一条链在 Actions 列表里静默断成两条。这种故障只能靠测试拦住。
    """
    mod, _, dispatched, env = harness
    mod.build_mode(env(verify_count=1))

    assert dispatched
    assert dispatched[0]['chain_id'] == '36677467180'


def _touch_old(path, age: float = 10.0):
    """把 mtime 拨到 age 秒前，绕过「文件太新则跳过」的保护。"""
    t = time.time() - age
    os.utime(path, (t, t))


class TestIncrementalDerivedUpload:
    """derived_file 一落盘就增量上传到 ci-fix-log 分支。

    回归的是 2026-09-30 那次整机失联：agent 攻坚 2h19m 挖出三个真实阻塞点、
    derived_file 已写到 2373 字节，但上传发生在 `run_agent` **返回之后**，
    而 fixarm 在编译途中整机失去响应、job 被切断 —— 产物只剩在那台机器的本地
    磁盘上，只能事后按逐字重建抢救回来。增量上传把最坏损失从「整轮攻坚」压到
    「最后 20 秒」。
    """

    def _mod(self):
        return _load_build_fix()

    def test_uploads_once_when_file_appears(self, tmp_path):
        mod = self._mod()
        f = tmp_path / 'derived-dockerfile'
        f.write_text('FROM openeuler/openeuler:24.03-lts-sp4\n', encoding='utf-8')
        _touch_old(f)
        sent, state = [], {}

        assert mod._maybe_upload_derived(str(f), state, sent.append) is True
        assert sent == ['FROM openeuler/openeuler:24.03-lts-sp4']  # 上传前 strip
        assert state['size'] == f.stat().st_size

    def test_does_not_re_upload_unchanged_content(self, tmp_path):
        """没变化就不该重复 PUT——否则 3 小时的构建会打上百次 Contents API。"""
        mod = self._mod()
        f = tmp_path / 'derived-dockerfile'
        f.write_text('FROM x\n', encoding='utf-8')
        _touch_old(f)
        sent, state = [], {}

        assert mod._maybe_upload_derived(str(f), state, sent.append) is True
        assert mod._maybe_upload_derived(str(f), state, sent.append) is False
        assert len(sent) == 1

    def test_uploads_again_after_the_agent_lands_a_new_version(self, tmp_path):
        mod = self._mod()
        f = tmp_path / 'derived-dockerfile'
        sent, state = [], {}

        f.write_text('RUN echo a\n', encoding='utf-8'); _touch_old(f)
        mod._maybe_upload_derived(str(f), state, sent.append)

        f.write_text('RUN echo a\nRUN echo b\n', encoding='utf-8'); _touch_old(f)
        mod._maybe_upload_derived(str(f), state, sent.append)

        assert len(sent) == 2
        assert 'RUN echo b' in sent[1]

    def test_skips_a_file_that_is_still_being_written(self, tmp_path):
        """刚写过的文件先不传，避免抓半截内容（下一轮再传）。"""
        mod = self._mod()
        f = tmp_path / 'derived-dockerfile'
        f.write_text('FROM x\n', encoding='utf-8')  # mtime = 现在
        sent = []

        assert mod._maybe_upload_derived(str(f), {}, sent.append) is False
        assert sent == []

    def test_missing_file_is_not_an_error(self, tmp_path):
        """agent 还没开始写时不能炸——这个轮询在整轮构建里一直在跑。"""
        mod = self._mod()
        sent = []
        assert mod._maybe_upload_derived(str(tmp_path / 'nope'), {}, sent.append) is False
        assert sent == []


def test_upload_happens_while_the_agent_is_still_running(harness, monkeypatch):
    """集成：增量上传必须发生在 `run_agent` **返回之前**。

    这正是 2026-09-30 丢工作的根因——上传挂在 run_agent 之后，机器一失联就全丢。
    这里让假 agent 写完 derived_file 后继续睡 1 秒，断言上传事件排在它返回之前。
    """
    mod, _, _, env = harness
    work = Path(mod.WORK_BASE) / '4723'
    events = []

    def slow_agent(**kwargs):
        f = work / 'derived-dockerfile'
        f.write_text('FROM openeuler/openeuler:24.03-lts-sp4\nRUN echo hi\n', encoding='utf-8')
        _touch_old(f)          # 绕过「文件太新则跳过」的保护
        time.sleep(1.0)        # 模拟 agent 还在继续攻坚
        events.append('agent-returned')
        return {'output_file': kwargs['output_file']}

    monkeypatch.setattr(mod, 'run_agent', slow_agent)
    monkeypatch.setattr(mod, 'DERIVED_UPLOAD_INTERVAL', 0.05)
    monkeypatch.setattr(
        mod.ci_data, 'write_file',
        lambda path, content, msg, **kw: events.append(('upload', path)))

    mod.build_mode(env(verify_count=0))

    uploads = [i for i, e in enumerate(events)
               if e != 'agent-returned' and str(e[1]).endswith('derived-dockerfile')]
    assert uploads, f'run_agent 运行期间从未增量上传过 derived：{events}'
    assert uploads[0] < events.index('agent-returned'), \
        '增量上传发生在 agent 返回之后 —— 那样整机失联仍会丢工作'
