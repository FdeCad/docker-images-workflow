"""stage_common 纯逻辑单元测试。"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.lib.stage_common import dispatch_phase, normalize_repo


def test_plain_owner_repo_unchanged():
    assert normalize_repo('openeuler/openeuler-docker-images') == \
        'openeuler/openeuler-docker-images'


def test_full_https_url():
    """仓库 Variable GITCODE_FORK_REPO 的**真实**形态是完整 URL。

    消费方按 owner/repo 拼接，原样使用会拼出 Fix PR head `https::fix/4723`
    和 `gitcode.com/https://gitcode.com/m0_....git` 这种远端地址。
    """
    assert normalize_repo('https://gitcode.com/m0_46560176/openeuler-docker-images') == \
        'm0_46560176/openeuler-docker-images'


def test_https_url_with_dot_git_suffix():
    assert normalize_repo('https://gitcode.com/o/r.git') == 'o/r'


def test_scp_like_url():
    assert normalize_repo('git@gitcode.com:o/r.git') == 'o/r'


def test_surrounding_whitespace_is_ignored():
    assert normalize_repo('  o/r  ') == 'o/r'


def test_empty_and_none_return_empty():
    assert normalize_repo('') == ''
    assert normalize_repo(None) == ''


def test_deep_path_keeps_last_two_segments():
    assert normalize_repo('https://gitcode.com/group/sub/o/r') == 'o/r'


# ── dispatch_phase：链路标识 chain_id ─────────────────────────────────────────

def _capture_dispatch(monkeypatch, run_id: str) -> dict:
    """把 requests.post 换成捕获器，返回收到的 dispatch 请求体。"""
    import scripts.lib.stage_common as sc

    monkeypatch.setenv('GITHUB_RUN_ID', run_id)
    seen: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.update(json or {})
        resp = MagicMock()
        resp.status_code = 204
        return resp

    monkeypatch.setattr(sc.requests, 'post', fake_post)
    return seen


def test_chain_origin_fills_chain_id_from_its_own_run_id(monkeypatch):
    """链路起点（payload 里还没有 chain_id）用本 run 的 id 当链路标识。"""
    seen = _capture_dispatch(monkeypatch, '36677467180')
    dispatch_phase({'phase': 'ci-log-analysis'})
    assert seen['client_payload']['chain_id'] == '36677467180'


def test_existing_chain_id_is_passed_through_not_replaced(monkeypatch):
    """后续阶段必须沿用起点的 chain_id；被本 run id 顶替就等于另起一条链。"""
    seen = _capture_dispatch(monkeypatch, '99999999999')
    dispatch_phase({'phase': 'verify-arm', 'chain_id': '36677467180'})
    assert seen['client_payload']['chain_id'] == '36677467180'
