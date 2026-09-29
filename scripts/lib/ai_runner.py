#!/usr/bin/env python3
"""
AI Agent 调用统一入口

根据 AI_RUNNER 环境变量选择后端：
  opencode             → opencode_run.run_opencode（默认）
  claude-code          → claude_code_run.run_claude_code（Anthropic API Key）
  claude-code-account  → claude_code_run.run_claude_code（Claude.ai 账号 OAuth）
"""

import os
from typing import Dict, Any, Optional

# 各后端可接受的凭证环境变量（任一非空即通过）
_CREDENTIAL_ENV = {
    'claude-code': ('ANTHROPIC_API_KEY',),
    'claude-code-account': ('CLAUDE_CREDENTIALS_JSON',),
    'opencode': ('OPENAI_API_KEY', 'DEEPSEEK_API_KEY', 'ANTHROPIC_API_KEY',
                 'OPENROUTER_API_KEY', 'GEMINI_API_KEY'),
}

_SECRET_HINT = {
    'claude-code': 'AI_API_KEY（Anthropic API Key）',
    'claude-code-account': 'CLAUDE_CREDENTIALS_JSON',
}


def check_credentials(runner: str) -> None:
    """前置校验：凭证为空时立即抛出可读错误。

    缺凭证时 opencode 仍会带着空 key 发出请求，服务端返回的通用错误被包装成
    `UnknownError: Unexpected server error`，与模型名错误、上游 503 等完全无法区分，
    排查成本极高。这里提前拦截。
    """
    names = _CREDENTIAL_ENV.get(runner)
    if not names or any(os.getenv(n, '').strip() for n in names):
        return
    hint = _SECRET_HINT.get(runner, 'AI_API_KEY')
    raise RuntimeError(
        f'AI_RUNNER={runner}，但凭证环境变量全为空（{" / ".join(names)}）。\n'
        f'请在仓库 Settings → Secrets and variables → Actions → Secrets 中配置 {hint}。\n'
        f'注意 Secrets 和 Variables 是两个独立页签，填错页签同样读不到。'
    )


def run_agent(
    prompt_file: str,
    context: Dict[str, Any],
    instruction: str,
    work_dir: str,
    output_file: str,
    log_dir: Optional[str] = None,
    timeout_ms: Optional[int] = None,
    label: str = 'agent',
    conventions_file: Optional[str] = None,
) -> Dict[str, str]:
    """统一 AI Agent 调用入口，透明切换 OpenCode / Claude Code。"""
    runner = os.getenv('AI_RUNNER', 'opencode')
    check_credentials(runner)

    kwargs: Dict[str, Any] = dict(
        prompt_file=prompt_file,
        context=context,
        instruction=instruction,
        work_dir=work_dir,
        output_file=output_file,
        log_dir=log_dir,
        label=label,
        conventions_file=conventions_file,
    )
    if timeout_ms is not None:
        kwargs['timeout_ms'] = timeout_ms

    if runner in ('claude-code', 'claude-code-account'):
        from scripts.lib.claude_code_run import run_claude_code
        return run_claude_code(**kwargs)
    else:
        from scripts.lib.opencode_run import run_opencode
        return run_opencode(**kwargs)
