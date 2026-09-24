#!/usr/bin/env python3
"""
GitHub API — PR 监控 + CI 日志获取
"""

import io
import os
import zipfile
import requests
from typing import Dict, List, Any, Optional

MAX_LOG_CHARS = 50_000
MAX_DIFF_CHARS = 30_000


def _headers(token: str) -> Dict[str, str]:
    return {
        'Authorization': f'token {token}',
        'Accept': 'application/vnd.github.v3+json',
    }


# ── PR 查询 ──

def fetch_prs_with_label(repo: str, label: str, token: str, max_prs: int = 50) -> List[Dict]:
    url = f"https://api.github.com/repos/{repo}/pulls"
    params = {'state': 'open', 'per_page': min(max_prs, 100), 'sort': 'updated', 'direction': 'desc'}
    resp = requests.get(url, headers=_headers(token), params=params, timeout=30)
    resp.raise_for_status()
    return [pr for pr in resp.json() if label in [l['name'] for l in pr.get('labels', [])]]


def find_open_ci_successful_fix_pr(repo: str, pr_number: int, token: str) -> Optional[Dict]:
    """查找标题含 '(fix #<pr_number>)' 且为 open 状态、带 ci_successful 标签的 PR。"""
    url = f"https://api.github.com/repos/{repo}/pulls"
    pattern = f'(fix #{pr_number})'
    try:
        resp = requests.get(url, headers=_headers(token),
                            params={'state': 'open', 'per_page': 50}, timeout=30)
        if not resp.ok:
            return None
        for pr in resp.json():
            title = pr.get('title', '')
            labels = [l.get('name') for l in pr.get('labels', [])]
            if pattern in title and 'ci_successful' in labels:
                return pr
    except Exception:
        pass
    return None


def find_any_pr_by_head_branch(repo: str, head_branch: str, token: str) -> Optional[Dict]:
    owner = repo.split('/')[0]
    url = f"https://api.github.com/repos/{repo}/pulls"
    params = {'state': 'all', 'head': f'{owner}:{head_branch}', 'per_page': 10}
    resp = requests.get(url, headers=_headers(token), params=params, timeout=30)
    resp.raise_for_status()
    prs = resp.json()
    return prs[0] if prs else None


def get_pr_diff(repo: str, pr_number: int, token: str) -> str:
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}"
    headers = _headers(token)
    headers['Accept'] = 'application/vnd.github.v3.diff'
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.text[:MAX_DIFF_CHARS]


def get_pr_file_names(repo: str, pr_number: int, token: str) -> List[str]:
    """返回 PR 涉及的文件路径列表（从 API 获取，不依赖本地 git history）。"""
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}/files"
    resp = requests.get(url, headers=_headers(token), params={'per_page': 100}, timeout=30)
    resp.raise_for_status()
    return [f['filename'] for f in resp.json() if f.get('filename')]


def get_pr_detail(repo: str, pr_number: int, token: str) -> Optional[Dict]:
    """按编号获取 PR 详情（含 head.sha / title / base.ref）。不存在返回 None。"""
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}"
    resp = requests.get(url, headers=_headers(token), timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def get_branch_commit_count(repo: str, branch: str, base_branch: str, token: str) -> int:
    url = f"https://api.github.com/repos/{repo}/compare/{base_branch}...{branch}"
    resp = requests.get(url, headers=_headers(token), timeout=30)
    if resp.status_code == 404:
        return 0
    resp.raise_for_status()
    return resp.json().get('ahead_by', 0)


# ── CI 日志获取 ──

def get_latest_failed_run(repo: str, head_sha: str, token: str,
                          pr_number: int = 0) -> Optional[Dict]:
    url = f"https://api.github.com/repos/{repo}/actions/runs"
    # 先查 failure 状态
    resp = requests.get(url, headers=_headers(token),
                        params={'head_sha': head_sha, 'per_page': 20}, timeout=30)
    resp.raise_for_status()
    runs = [r for r in resp.json().get('workflow_runs', []) if r.get('conclusion') == 'failure']
    if not runs:
        return None
    return sorted(runs, key=lambda r: r.get('updated_at', ''), reverse=True)[0]


def get_failed_job_logs(repo: str, run_id: int, token: str, target_url: str = '') -> str:
    jobs_url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs"
    resp = requests.get(jobs_url, headers=_headers(token), timeout=30)
    resp.raise_for_status()
    jobs = resp.json().get('jobs', [])

    failed_jobs = [j for j in jobs if j.get('conclusion') == 'failure'] or jobs[:3]
    parts: List[str] = []

    for job in failed_jobs[:3]:
        job_id = job['id']
        job_name = job.get('name', str(job_id))
        log_url = f"https://api.github.com/repos/{repo}/actions/jobs/{job_id}/logs"

        try:
            log_resp = requests.get(log_url, headers=_headers(token),
                                    timeout=60, allow_redirects=True)
        except Exception as e:
            parts.append(f"### Job: {job_name}\n[log fetch error: {e}]")
            continue

        content = _extract_log_content(log_resp, job_name)
        if content:
            parts.append(f"### Job: {job_name}\n```\n{content}\n```")

    result = '\n\n'.join(parts)
    return result[:MAX_LOG_CHARS]


def _extract_log_content(resp: requests.Response, job_name: str) -> str:
    if resp.status_code not in (200, 302):
        return f"[HTTP {resp.status_code}]"

    content_type = resp.headers.get('Content-Type', '')

    if 'zip' in content_type or resp.content[:2] == b'PK':
        try:
            with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
                texts = []
                for name in z.namelist()[:5]:
                    with z.open(name) as f:
                        texts.append(f.read().decode('utf-8', errors='replace')[:10000])
                return '\n'.join(texts)
        except Exception as e:
            return f"[zip parse error: {e}]"

    raw = resp.text
    lines = raw.split('\n')
    error_lines = [l for l in lines if any(
        k in l.lower() for k in ['error', 'fail', 'exception', 'traceback', 'fatal', 'panic']
    )]
    tail = lines[-300:]
    combined = error_lines[:150] + (['---'] if error_lines else []) + tail
    return '\n'.join(combined)[:20000]


# ── PR 操作 ──

def close_pull_request(repo: str, pr_number: int, token: str):
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}"
    resp = requests.patch(url, headers=_headers(token), json={'state': 'closed'}, timeout=30)
    resp.raise_for_status()


def add_pr_comment(repo: str, pr_number: int, body: str, token: str):
    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
    resp = requests.post(url, headers=_headers(token), json={'body': body}, timeout=30)
    resp.raise_for_status()
