"""stage_common 纯逻辑单元测试。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.lib.stage_common import normalize_repo


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
