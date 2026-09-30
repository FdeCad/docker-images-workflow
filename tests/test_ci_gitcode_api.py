"""
Tests for scripts/lib/ci_gitcode_api.py

覆盖范围：
- _url_score: Jenkins URL 打分逻辑
- _find_jenkins_url_in_comments: 从 PR 评论中选取最优构建 URL
- get_latest_failed_run: 候选 URL 收集与择优（mock HTTP）
- _fetch_external_ci_log: 日志提取（尾部优先，不截断末尾关键段）
"""

import pytest
from unittest.mock import patch, MagicMock

from scripts.lib.ci_gitcode_api import (
    _url_score,
    _find_jenkins_url_in_comments,
    get_pr_detail,
    get_latest_failed_run,
    _fetch_external_ci_log,
    find_open_ci_successful_fix_pr,
)

# ── 共享测试 URL 常量 ──────────────────────────────────────────────────────────

TRIGGER  = 'https://ci.openeuler.openatom.cn/job/multiarch/job/openeuler/job/trigger/job/openeuler-docker-images/1396/console'
X86      = 'https://ci.openeuler.openatom.cn/job/multiarch/job/openeuler/job/x86-64/job/openeuler-docker-images/1391/console'
AARCH64  = 'https://ci.openeuler.openatom.cn/job/multiarch/job/openeuler/job/aarch64/job/openeuler-docker-images/1366/console'
ARM64    = 'https://ci.openeuler.openatom.cn/job/multiarch/job/openeuler/job/arm64/job/openeuler-docker-images/100/console'
S390X    = 'https://ci.openeuler.openatom.cn/job/multiarch/job/openeuler/job/s390x/job/openeuler-docker-images/200/console'
GATE     = 'https://ci.openeuler.openatom.cn/job/multiarch/job/openeuler/job/gate/job/openeuler-docker-images/300/console'
SHALLOW  = 'https://ci.openeuler.openatom.cn/job/check/123/console'


# ── _url_score ────────────────────────────────────────────────────────────────

class TestUrlScore:
    def test_trigger_scores_lowest(self):
        assert _url_score(TRIGGER) == (False, 4, False)

    def test_x86_build_scores_highest(self):
        assert _url_score(X86) == (True, 4, True)

    def test_aarch64_scores_same_as_x86(self):
        assert _url_score(AARCH64) == _url_score(X86)

    def test_arm64_detected_as_build(self):
        is_build, _, _ = _url_score(ARM64)
        assert is_build is True

    def test_s390x_detected_as_build(self):
        is_build, _, _ = _url_score(S390X)
        assert is_build is True

    def test_gate_is_orchestrator(self):
        is_build, _, not_orch = _url_score(GATE)
        assert is_build is False
        assert not_orch is False

    def test_depth_counted_correctly(self):
        _, depth, _ = _url_score(X86)
        assert depth == 4

    def test_shallow_url_has_lower_depth(self):
        _, shallow_depth, _ = _url_score(SHALLOW)
        _, deep_depth, _ = _url_score(X86)
        assert shallow_depth < deep_depth

    def test_build_beats_trigger_with_same_depth(self):
        assert _url_score(X86) > _url_score(TRIGGER)

    def test_deeper_non_arch_beats_shallower(self):
        # 同为非架构 URL，深的优先
        assert _url_score(TRIGGER) > _url_score(SHALLOW)


# ── _find_jenkins_url_in_comments ─────────────────────────────────────────────

def _comment(body: str) -> dict:
    return {'body': body}


class TestFindJenkinsUrlInComments:
    def test_empty_comments(self):
        assert _find_jenkins_url_in_comments([]) == ''

    def test_no_jenkins_url(self):
        assert _find_jenkins_url_in_comments([_comment('nothing here')]) == ''

    def test_trigger_url_filtered_out_returns_empty(self):
        # trigger URL 被过滤，没有任何 build URL → 返回空字符串
        comments = [_comment(f'门禁运行中 {TRIGGER}')]
        assert _find_jenkins_url_in_comments(comments) == ''

    def test_build_url_beats_trigger_in_same_failure_comment(self):
        # 真实 PR #2534 场景：一条评论含 trigger + x86 + aarch64，且有 FAILED 关键词
        body = (
            f'<table>x86_64 FAILED {X86}\n'
            f'aarch64 FAILED {AARCH64}\n'
            f'trigger {TRIGGER}</table>'
        )
        result = _find_jenkins_url_in_comments([_comment(body)])
        assert result in (X86, AARCH64)  # 任一构建 URL 均可
        assert 'trigger' not in result

    def test_failure_comment_beats_neutral_comment(self):
        # 失败评论里的 trigger URL 也比非失败评论里的 trigger URL 优先
        # 但如果失败评论里只有 trigger，仍返回 trigger
        comments = [
            _comment(f'CI 运行中 {TRIGGER}'),        # neutral
            _comment(f'build FAILED {X86}'),          # failure，有 build URL
        ]
        result = _find_jenkins_url_in_comments(comments)
        assert result == X86

    def test_all_urls_from_all_comments_are_considered(self):
        # build URL 在不同评论里，应被收集到
        comments = [
            _comment(f'check FAILED {TRIGGER}'),
            _comment(f'downstream build failed {X86}'),
        ]
        result = _find_jenkins_url_in_comments(comments)
        assert result == X86

    def test_url_trailing_punctuation_stripped(self):
        body = f'see {X86}.'
        result = _find_jenkins_url_in_comments([_comment(body)])
        assert result == X86

    def test_multiple_arch_urls_returns_one_of_them(self):
        body = f'x86 failed {X86} aarch64 failed {AARCH64} failed'
        result = _find_jenkins_url_in_comments([_comment(body)])
        assert result in (X86, AARCH64)

    def test_trigger_in_failure_comment_filtered_falls_back_to_neutral_build(self):
        # trigger URL 在失败评论里但被过滤；build URL 在中性评论里 → 返回 build URL
        comments = [
            _comment(f'passed {X86}'),       # neutral with build URL
            _comment(f'error {TRIGGER}'),    # failure with trigger URL (filtered)
        ]
        result = _find_jenkins_url_in_comments(comments)
        assert result == X86

    def test_chinese_failure_keyword(self):
        body = f'构建失败 {X86}'
        result = _find_jenkins_url_in_comments([_comment(body)])
        assert result == X86

    def test_real_pr2534_comment_structure(self):
        # 完整还原 PR #2534 的两条评论
        comment_running = _comment(
            f'门禁正在运行，查看实时结果:\n{TRIGGER}'
        )
        comment_table = _comment(
            f'<table>\n'
            f'check_sca SUCCESS {TRIGGER}\n'
            f'x86_64 check_build FAILED {X86}\n'
            f'aarch64 check_build FAILED {AARCH64}\n'
            f'</table>'
        )
        result = _find_jenkins_url_in_comments([comment_running, comment_table])
        assert result in (X86, AARCH64)
        assert 'trigger' not in result

    def test_mixed_table_only_failed_arch_selected(self):
        # PR #2546 真实场景：x86 行含 SUCCESS，aarch64 行含 FAILED
        # 逐行匹配时应只把 aarch64 放入 failed_urls，返回 AARCH64
        body = (
            f'<tr><td>x86_64</td><td>check_build</td>'
            f'<td>&#9989;<strong>SUCCESS</strong></td>'
            f'<td><a href={X86}>#1404</a></td></tr>\n'
            f'<tr><td>aarch64</td><td>check_build</td>'
            f'<td>&#10060;<strong>FAILED</strong></td>'
            f'<td><a href={AARCH64}>#1379</a></td></tr>'
        )
        result = _find_jenkins_url_in_comments([_comment(body)])
        assert result == AARCH64


# ── _fetch_external_ci_log ────────────────────────────────────────────────────

def _mock_http_200(text: str) -> MagicMock:
    m = MagicMock()
    m.status_code = 200
    m.text = text
    return m


class TestFetchExternalCiLog:
    def test_tail_wins_no_early_noise_cuts_critical_end(self):
        # 模拟 PR #2547 场景：日志很长，早期有大量含 'error'/'fail' 的噪声行，
        # 真正的失败行（anubis/libaio）在末尾。
        # 旧逻辑：error_lines[:150] 消耗过多预算，末尾关键行被截断。
        # 新逻辑：只取尾部，关键行必须出现。
        early_noise = ['CMake Error in test: irrelevant\n' * 1] * 200  # 200 行早期噪声
        critical_end = [
            'Assessing libaio...',
            'Download with https://pagure.io/libaio/...',
            'Content-Type: text/html; charset=utf-8',
            'Set-Cookie: techaro.lol-anubis-auth=...',
            '#11 ERROR: exit code: 1',
            'Finished: FAILURE',
        ]
        full_log = '\n'.join(early_noise + critical_end)
        mock_resp = _mock_http_200(full_log)
        with patch('scripts.lib.ci_gitcode_api.requests.get', return_value=mock_resp):
            result = _fetch_external_ci_log('https://ci.example.com/job/test/1/console')
        assert 'libaio' in result
        assert 'anubis' in result.lower()
        assert 'Finished: FAILURE' in result

    def test_short_log_returned_in_full(self):
        log_text = 'step 1\nstep 2\nFailed: build error\n'
        mock_resp = _mock_http_200(log_text)
        with patch('scripts.lib.ci_gitcode_api.requests.get', return_value=mock_resp):
            result = _fetch_external_ci_log('https://ci.example.com/job/test/2/console')
        assert 'Failed: build error' in result

    def test_very_long_tail_truncated_from_end(self):
        # 500 行 × 200 字节 = 100,000 字符 > MAX_LOG_CHARS(50,000)
        # 超限时应保留末尾（最新内容），最后一行不能丢失
        long_line = 'x' * 190
        lines = [long_line] * 500
        lines[-1] = 'FINAL_LINE_MUST_SURVIVE'
        mock_resp = _mock_http_200('\n'.join(lines))
        with patch('scripts.lib.ci_gitcode_api.requests.get', return_value=mock_resp):
            result = _fetch_external_ci_log('https://ci.example.com/job/test/3/console')
        assert 'FINAL_LINE_MUST_SURVIVE' in result
        assert len(result) <= 50_000

    def test_http_error_returns_empty(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        with patch('scripts.lib.ci_gitcode_api.requests.get', return_value=mock_resp):
            result = _fetch_external_ci_log('https://ci.example.com/job/test/4/console')
        assert result == ''

    def test_non_console_url_appends_consoletext(self):
        # URL 不以 /console 结尾时应追加 /consoleText
        captured = []
        mock_resp = _mock_http_200('ok')

        def side_effect(url, **kwargs):
            captured.append(url)
            return mock_resp

        with patch('scripts.lib.ci_gitcode_api.requests.get', side_effect=side_effect):
            _fetch_external_ci_log('https://ci.example.com/job/test/5')
        assert any('consoleText' in u for u in captured)


# ── find_open_ci_successful_fix_pr ────────────────────────────────────────────

FREPO = 'openeuler/openeuler-docker-images'
FTOKEN = 'test-token'


def _mock_prs_response(prs: list) -> MagicMock:
    m = MagicMock()
    m.ok = True
    m.json.return_value = prs
    return m


def _make_pr(number: int, title: str, labels: list) -> dict:
    return {
        'number': number,
        'title': title,
        'labels': [{'name': l} for l in labels],
        'state': 'open',
    }


class TestFindOpenCiSuccessfulFixPr:
    def test_finds_matching_pr(self):
        prs = [_make_pr(101, 'fix: etcd 3.6.11 (fix #2534)', ['ci_successful'])]
        with patch('scripts.lib.ci_gitcode_api.requests.get', return_value=_mock_prs_response(prs)):
            result = find_open_ci_successful_fix_pr(FREPO, 2534, FTOKEN)
        assert result is not None
        assert result['number'] == 101

    def test_returns_none_when_no_ci_successful_label(self):
        prs = [_make_pr(101, 'fix: etcd 3.6.11 (fix #2534)', ['ci_failed'])]
        with patch('scripts.lib.ci_gitcode_api.requests.get', return_value=_mock_prs_response(prs)):
            result = find_open_ci_successful_fix_pr(FREPO, 2534, FTOKEN)
        assert result is None

    def test_returns_none_when_title_does_not_match(self):
        prs = [_make_pr(101, 'fix: etcd 3.6.11 (fix #9999)', ['ci_successful'])]
        with patch('scripts.lib.ci_gitcode_api.requests.get', return_value=_mock_prs_response(prs)):
            result = find_open_ci_successful_fix_pr(FREPO, 2534, FTOKEN)
        assert result is None

    def test_returns_none_when_list_empty(self):
        with patch('scripts.lib.ci_gitcode_api.requests.get', return_value=_mock_prs_response([])):
            result = find_open_ci_successful_fix_pr(FREPO, 2534, FTOKEN)
        assert result is None

    def test_returns_none_on_http_error(self):
        m = MagicMock()
        m.ok = False
        with patch('scripts.lib.ci_gitcode_api.requests.get', return_value=m):
            result = find_open_ci_successful_fix_pr(FREPO, 2534, FTOKEN)
        assert result is None

    def test_ignores_pr_with_different_number(self):
        # (fix #2534) 不匹配 pr_number=100
        prs = [_make_pr(101, 'fix: something (fix #2534)', ['ci_successful'])]
        with patch('scripts.lib.ci_gitcode_api.requests.get', return_value=_mock_prs_response(prs)):
            result = find_open_ci_successful_fix_pr(FREPO, 100, FTOKEN)
        assert result is None

    def test_returns_none_on_exception(self):
        with patch('scripts.lib.ci_gitcode_api.requests.get', side_effect=Exception('timeout')):
            result = find_open_ci_successful_fix_pr(FREPO, 2534, FTOKEN)
        assert result is None


# ── get_latest_failed_run ─────────────────────────────────────────────────────

REPO  = 'openeuler/openeuler-docker-images'
SHA   = 'abc123def456'
TOKEN = 'test-token'


def _mock_pipeline_404():
    """GitCode 内置 pipeline API 返回 404（openEuler 用 Jenkins，无内置 pipeline）。"""
    m = MagicMock()
    m.status_code = 404
    return m


class TestGetLatestFailedRun:
    def _patch(self, pipeline_resp=None, statuses=None, comments=None):
        """返回三个 patch 的 context manager 元组。"""
        if pipeline_resp is None:
            pipeline_resp = _mock_pipeline_404()
        return (
            patch('scripts.lib.ci_gitcode_api.requests.get', return_value=pipeline_resp),
            patch('scripts.lib.ci_gitcode_api._get_commit_statuses', return_value=statuses or []),
            patch('scripts.lib.ci_gitcode_api._get_pr_comments',     return_value=comments or []),
        )

    def test_returns_none_when_nothing_found(self):
        with patch('scripts.lib.ci_gitcode_api.requests.get', return_value=_mock_pipeline_404()), \
             patch('scripts.lib.ci_gitcode_api._get_commit_statuses', return_value=[]), \
             patch('scripts.lib.ci_gitcode_api._get_pr_comments',     return_value=[]):
            result = get_latest_failed_run(REPO, SHA, TOKEN, pr_number=1)
        assert result is None

    def test_gitcode_pipeline_returned_directly(self):
        pipeline = MagicMock()
        pipeline.status_code = 200
        pipeline.json.return_value = [{'id': 99, 'status': 'failed'}]
        with patch('scripts.lib.ci_gitcode_api.requests.get', return_value=pipeline), \
             patch('scripts.lib.ci_gitcode_api._get_commit_statuses', return_value=[]), \
             patch('scripts.lib.ci_gitcode_api._get_pr_comments',     return_value=[]):
            result = get_latest_failed_run(REPO, SHA, TOKEN)
        assert result == {'id': 99, 'status': 'failed'}

    def test_build_url_from_comments_beats_trigger_from_status(self):
        # commit status → trigger URL；评论 → x86 build URL；应选 build URL
        statuses  = [{'state': 'failure', 'context': 'ci', 'target_url': TRIGGER}]
        comments  = [_comment(f'x86_64 FAILED {X86}')]
        with patch('scripts.lib.ci_gitcode_api.requests.get', return_value=_mock_pipeline_404()), \
             patch('scripts.lib.ci_gitcode_api._get_commit_statuses', return_value=statuses), \
             patch('scripts.lib.ci_gitcode_api._get_pr_comments',     return_value=comments):
            result = get_latest_failed_run(REPO, SHA, TOKEN, pr_number=42)
        assert result is not None
        assert result['target_url'] == X86

    def test_arch_build_url_from_status_kept_when_no_better_in_comments(self):
        # commit status 直接上报了 x86 build URL，评论里没有更好的
        statuses = [{'state': 'failure', 'context': 'build', 'target_url': X86}]
        with patch('scripts.lib.ci_gitcode_api.requests.get', return_value=_mock_pipeline_404()), \
             patch('scripts.lib.ci_gitcode_api._get_commit_statuses', return_value=statuses), \
             patch('scripts.lib.ci_gitcode_api._get_pr_comments',     return_value=[]):
            result = get_latest_failed_run(REPO, SHA, TOKEN, pr_number=42)
        assert result['target_url'] == X86

    def test_trigger_only_returns_none(self):
        # 只有 trigger URL，没有任何 build URL → 返回 None（不抓 trigger 日志）
        # trigger 日志里的 Finished: SUCCESS 会误导 agent 认为构建成功
        statuses = [{'state': 'failure', 'context': 'ci', 'target_url': TRIGGER}]
        with patch('scripts.lib.ci_gitcode_api.requests.get', return_value=_mock_pipeline_404()), \
             patch('scripts.lib.ci_gitcode_api._get_commit_statuses', return_value=statuses), \
             patch('scripts.lib.ci_gitcode_api._get_pr_comments',     return_value=[]):
            result = get_latest_failed_run(REPO, SHA, TOKEN, pr_number=42)
        assert result is None

    def test_comments_only_no_status(self):
        # commit status 空，全靠评论
        comments = [_comment(f'aarch64 build failed {AARCH64}')]
        with patch('scripts.lib.ci_gitcode_api.requests.get', return_value=_mock_pipeline_404()), \
             patch('scripts.lib.ci_gitcode_api._get_commit_statuses', return_value=[]), \
             patch('scripts.lib.ci_gitcode_api._get_pr_comments',     return_value=comments):
            result = get_latest_failed_run(REPO, SHA, TOKEN, pr_number=10)
        assert result['target_url'] == AARCH64

    def test_no_pr_number_skips_comments(self):
        # pr_number=0，跳过评论步骤，只用 commit status
        statuses = [{'state': 'failure', 'target_url': TRIGGER}]
        mock_get_comments = MagicMock()
        with patch('scripts.lib.ci_gitcode_api.requests.get', return_value=_mock_pipeline_404()), \
             patch('scripts.lib.ci_gitcode_api._get_commit_statuses', return_value=statuses), \
             patch('scripts.lib.ci_gitcode_api._get_pr_comments', mock_get_comments):
            get_latest_failed_run(REPO, SHA, TOKEN, pr_number=0)
        mock_get_comments.assert_not_called()

    def test_pipeline_exception_falls_through(self):
        # pipeline API 抛异常，应继续走 commit status + 评论
        statuses = [{'state': 'failed', 'target_url': X86}]
        with patch('scripts.lib.ci_gitcode_api.requests.get', side_effect=Exception('timeout')), \
             patch('scripts.lib.ci_gitcode_api._get_commit_statuses', return_value=statuses), \
             patch('scripts.lib.ci_gitcode_api._get_pr_comments',     return_value=[]):
            result = get_latest_failed_run(REPO, SHA, TOKEN, pr_number=5)
        assert result['target_url'] == X86

    def test_status_without_target_url_ignored(self):
        # state=failure 但无 target_url，不应加入候选
        statuses = [{'state': 'failure', 'context': 'ci', 'target_url': ''}]
        with patch('scripts.lib.ci_gitcode_api.requests.get', return_value=_mock_pipeline_404()), \
             patch('scripts.lib.ci_gitcode_api._get_commit_statuses', return_value=statuses), \
             patch('scripts.lib.ci_gitcode_api._get_pr_comments',     return_value=[]):
            result = get_latest_failed_run(REPO, SHA, TOKEN, pr_number=0)
        assert result is None

    def test_multiple_arch_builds_in_status_best_scored_wins(self):
        # 两条 status，一条 trigger 一条 x86，选 x86
        statuses = [
            {'state': 'failure', 'target_url': TRIGGER},
            {'state': 'failure', 'target_url': X86},
        ]
        with patch('scripts.lib.ci_gitcode_api.requests.get', return_value=_mock_pipeline_404()), \
             patch('scripts.lib.ci_gitcode_api._get_commit_statuses', return_value=statuses), \
             patch('scripts.lib.ci_gitcode_api._get_pr_comments',     return_value=[]):
            result = get_latest_failed_run(REPO, SHA, TOKEN, pr_number=0)
        assert result['target_url'] == X86


# ── get_pr_detail：无 head.sha 时用列表端点兜底 ────────────────────────────────

class TestGetPrDetailFallback:
    """`/pulls/{id}` 返回 200 却不含 `head.sha` 时，要改用列表端点兜底。

    2026-09-30 实测：该端点 06:15 连续 5 次返回 200 但无 `head`（03:39 同参数
    成功），而列表端点 `/pulls?state=open` 一直带着 `head.sha`——watch 侧用的就是
    它。缺这个字段不是"少个字段"，而是 manual-trigger 直接 RuntimeError、
    整条链路根本起不来。
    """

    @staticmethod
    def _resp(status, payload):
        r = MagicMock()
        r.status_code = status
        r.ok = status < 400
        r.json.return_value = payload
        return r

    def test_detail_with_head_is_used_directly(self):
        detail = {'number': 4723, 'title': 't', 'head': {'sha': 'abc12345'},
                  'base': {'ref': 'master'}}
        with patch('scripts.lib.ci_gitcode_api.requests.get',
                   return_value=self._resp(200, detail)) as g:
            assert get_pr_detail(REPO, 4723, TOKEN) == detail
        # 正常路径不该多打列表端点（watch 每轮都会调这里）
        assert g.call_count == 1

    def test_falls_back_to_list_when_head_missing(self):
        list_entry = {'number': 4723, 'title': 't',
                      'head': {'sha': 'd2831da2beef', 'ref': 'fix/4723'},
                      'base': {'ref': 'master'}}
        responses = [
            self._resp(200, {'number': 4723}),   # /pulls/4723 —— 残缺形态
            self._resp(200, [list_entry]),       # /pulls?state=open —— 兜底命中
        ]
        with patch('scripts.lib.ci_gitcode_api.requests.get', side_effect=responses):
            got = get_pr_detail(REPO, 4723, TOKEN)
        assert got['head']['sha'] == 'd2831da2beef'
        assert got['base']['ref'] == 'master'

    def test_returns_original_payload_when_fallback_also_fails(self):
        """兜底也捞不到时返回残缺响应本身，让上层报错带上真实字段名。"""
        responses = [
            self._resp(200, {'message': 'ok'}),  # 残缺
            self._resp(200, []),                 # open 里没有
            self._resp(404, []),                 # closed 拉不动
            self._resp(200, []),                 # merged 里也没有
        ]
        with patch('scripts.lib.ci_gitcode_api.requests.get', side_effect=responses):
            assert get_pr_detail(REPO, 4723, TOKEN) == {'message': 'ok'}
