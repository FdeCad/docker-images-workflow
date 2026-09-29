#!/usr/bin/env python3
"""
opencode 调用统一封装 - Python 版本
参考 portal-workflow 实现

关键工程要点:
  1. 用 bash + 真实管道喂 prompt: cat $promptFile | opencode run - ...
  2. stdbuf -oL -eL: 强制行缓冲，workflow 日志实时可见
  3. stdout/stderr 直通 workflow log
  4. 进程组 SIGKILL: 确保子进程被清理
  5. prompt 落盘支持 replay
"""

import os
import sys
import json
import time
import signal
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any

DEFAULT_MODEL = os.getenv('AI_MODEL', 'deepseek/deepseek-v4-pro')
DEFAULT_AGENT = 'build'
DEFAULT_EXTRA_RAW = '--dangerously-skip-permissions'
DEFAULT_TIMEOUT = int(os.getenv('AI_TIMEOUT_MS', '1800000'))


def log(msg: str):
    """日志输出"""
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"[{ts}] opencode {msg}", file=sys.stderr)


def opencode_log_tail(max_lines: int = 40) -> str:
    """读取 opencode 自身日志（~/.local/share/opencode/log）的末尾若干行。

    CLI 只把一个通用错误包装成 UnknownError 吐到 stderr，真实原因（凭证缺失、
    模型未解析、上游报错）只写在这里。失败时随异常一起带出，否则无从定位。
    """
    try:
        base = os.getenv('XDG_DATA_HOME') or os.path.join(
            os.path.expanduser('~'), '.local', 'share')
        log_dir = os.path.join(base, 'opencode', 'log')
        files = [os.path.join(log_dir, f) for f in os.listdir(log_dir)]
        files = [f for f in files if os.path.isfile(f)]
        if not files:
            return f'（{log_dir} 下没有日志文件）'
        newest = max(files, key=os.path.getmtime)
        with open(newest, 'r', encoding='utf-8', errors='replace') as fh:
            lines = fh.read().splitlines()
        return f'{newest}（末 {min(max_lines, len(lines))} 行）:\n' + \
               '\n'.join(lines[-max_lines:])
    except Exception as e:  # 日志缺失不应掩盖原始错误
        return f'（读取 opencode 日志失败: {e}）'


def run_opencode(
    prompt_file: str,
    context: Dict[str, Any],
    instruction: str,
    work_dir: str,
    output_file: str,
    log_dir: Optional[str] = None,
    timeout_ms: int = DEFAULT_TIMEOUT,
    model: str = DEFAULT_MODEL,
    agent: str = DEFAULT_AGENT,
    extra_args_raw: str = DEFAULT_EXTRA_RAW,
    label: str = 'opencode',
    conventions_file: Optional[str] = None,
) -> Dict[str, str]:
    """
    跑一次 opencode
    
    Args:
        prompt_file: 主 prompt 文件路径
        context: 上下文对象，JSON 序列化注入
        instruction: 任务指令
        work_dir: 工作目录 (opencode CWD)
        output_file: agent 必须写入的文件绝对路径
        log_dir: prompt dump + log 目录，默认 = work_dir
        timeout_ms: 超时毫秒，默认 30 分钟
        model: 模型名称
        agent: agent 名称
        extra_args_raw: 额外参数
        label: 日志短标签
    
    Returns:
        包含 output_file, log_file, prompt_file 的字典
    
    Raises:
        Exception: 当 opencode 执行失败时
    """
    if not prompt_file:
        raise ValueError('prompt_file required')
    if not work_dir:
        raise ValueError('work_dir required')
    if not output_file:
        raise ValueError('output_file required')
    
    # 创建目录
    Path(work_dir).mkdir(parents=True, exist_ok=True)
    artifact_dir = log_dir or work_dir
    if artifact_dir != work_dir:
        Path(artifact_dir).mkdir(parents=True, exist_ok=True)
    
    ts = datetime.now().strftime('%Y-%m-%dT%H-%M-%S')
    log_file = os.path.join(artifact_dir, f'opencode-{label}-{ts}.log')
    
    # 读取 prompt 模板
    with open(prompt_file, 'r', encoding='utf-8') as f:
        prompt_body = f.read()
    
    context_json = json.dumps(context or {}, indent=2, ensure_ascii=False)
    
    # 读取项目规范（如果有）
    conventions_text = ''
    if conventions_file and os.path.exists(conventions_file):
        with open(conventions_file, 'r', encoding='utf-8') as f:
            conventions_text = f.read()
        log(f'📋 Loaded conventions from {conventions_file} ({len(conventions_text)} chars)')
    
    # 拼接完整 prompt
    prompt_parts = [
        prompt_body.strip(),
        '',
        '## 上下文 (Context)',
        '```json',
        context_json,
        '```',
    ]
    
    # 如果有项目规范，注入到 prompt 中
    if conventions_text:
        prompt_parts.extend([
            '',
            '## 项目规范与约束 (Project Conventions)',
            '以下是当前项目的规范要求，请在分析和设计时严格遵守：',
            '',
            conventions_text,
        ])
    
    prompt_parts.extend([
        '',
        '## 任务指令',
        instruction or '',
        '',
        '## 输出要求',
        f'请把最终结果写入文件: `{output_file}`',
        '(写入完成后即可结束，无需在 stdout 重复输出全文)',
    ])
    
    full_prompt = '\n'.join(prompt_parts)
    
    # 提前删旧文件
    if os.path.exists(output_file):
        os.remove(output_file)
    
    # prompt 落盘支持 replay
    prompt_dump_file = os.path.join(artifact_dir, f'opencode-prompt-{label}-{ts}.txt')
    with open(prompt_dump_file, 'w', encoding='utf-8') as f:
        f.write(full_prompt)
    
    opencode_bin = os.getenv('OPENCODE_BIN', 'opencode')
    extra_args = extra_args_raw.split() if extra_args_raw else []
    opencode_args = ['run', '-', '--model', model, '--agent', agent] + extra_args
    
    # 组装 bash 命令
    # stdbuf 行缓冲 → workflow 日志实时可见
    # 真实 shell pipe 喂 prompt → 避免 hang
    bash_cmd = f'stdbuf -oL -eL {opencode_bin} {" ".join(opencode_args)} < "{prompt_dump_file}"'
    
    log(f'▶ [{label}] starting (timeout={timeout_ms // 1000}s)')
    log(f'    bin:     {opencode_bin}')
    log(f'    args:    {json.dumps(opencode_args)}')
    log(f'    cwd:     {work_dir}')
    log(f'    model:   {model}')
    log(f'    prompt:  {len(full_prompt)} chars → {prompt_dump_file}')
    log(f'    log:     {log_file}')
    log(f'    output:  {output_file}')
    api_key = os.environ.get('OPENAI_API_KEY', '')
    base_url = os.environ.get('OPENAI_BASE_URL', '')
    log(f'    api:     key={("***" + api_key[-4:]) if len(api_key) > 4 else "NOT SET"}, base_url={base_url or "default"}')
    log(f'    replay:  cd {work_dir} && cat {prompt_dump_file} | {opencode_bin} {" ".join(opencode_args)}')
    
    t0 = time.time()
    
    # 写 log file header
    try:
        header = (
            f'==== opencode-run [{label}] @ {datetime.now().isoformat()} ====\n'
            f'bin={opencode_bin}\nargs={json.dumps(opencode_args)}\ncwd={work_dir}\n'
            f'prompt_file={prompt_dump_file} ({len(full_prompt)} chars)\n'
            f'output_file={output_file}\n'
            f'replay: cd {work_dir} && cat {prompt_dump_file} | {opencode_bin} {" ".join(opencode_args)}\n'
            f'---- (stdout/stderr 走 inherit，完整输出见 workflow log) ----\n'
        )
        with open(log_file, 'w', encoding='utf-8') as f:
            f.write(header)
    except Exception as e:
        log(f'    ⚠ log file header 写入失败(不阻断): {e}')
    
    # 启动子进程
    try:
        process = subprocess.Popen(
            ['bash', '-c', bash_cmd],
            cwd=work_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=os.environ.copy(),
            start_new_session=True,  # 创建新进程组
        )
    except Exception as e:
        raise RuntimeError(f'opencode spawn failed: {e}')
    
    log(f'    pid:     {process.pid}')
    
    # 超时处理
    timed_out = False
    
    def timeout_handler():
        nonlocal timed_out
        timed_out = True
        duration = time.time() - t0
        log(f'  ⏱ [{label}] TIMEOUT {duration:.1f}s, SIGKILL 整个进程组(pid=-{process.pid})')
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except Exception as e:
            log(f'     kill -PG 失败({e}), fallback kill 当前 pid')
            try:
                process.kill()
            except:
                pass
    
    # 使用 Timer 实现超时
    from threading import Timer
    timer = Timer(timeout_ms / 1000, timeout_handler)
    timer.daemon = True
    timer.start()
    
    try:
        # 等待进程完成，同时读取输出
        stdout, stderr = process.communicate()
        timer.cancel()
        
        duration = time.time() - t0
        
        # 输出 opencode 的 stdout/stderr 到 workflow log
        stderr_text = stderr.decode('utf-8', errors='replace').strip()
        stdout_text = stdout.decode('utf-8', errors='replace').strip()
        
        if stderr_text:
            for line in stderr_text.split('\n'):
                log(f'  [stderr] {line}')
        if stdout_text:
            # 只输出最后几行避免刷屏
            stdout_lines = stdout_text.split('\n')
            if len(stdout_lines) > 20:
                log(f'  [stdout] ... ({len(stdout_lines)} lines total, showing last 20)')
                for line in stdout_lines[-20:]:
                    log(f'  [stdout] {line}')
            else:
                for line in stdout_lines:
                    log(f'  [stdout] {line}')
        
        if timed_out:
            raise TimeoutError(
                f'opencode [{label}] TIMEOUT after {duration:.1f}s '
                f'(limit {timeout_ms / 1000:.0f}s). '
                f'replay: cd {work_dir} && cat {prompt_dump_file} | {opencode_bin} {" ".join(opencode_args)}'
            )
        
        if process.returncode != 0:
            # 检查 outputFile 是否存在
            if not os.path.exists(output_file):
                raise RuntimeError(
                    f'opencode [{label}] exit {process.returncode} '
                    f'但未生成 outputFile {output_file}。\n'
                    f'stderr: {stderr_text[:2000]}\n'
                    f'{opencode_log_tail()}\n'
                    f'replay: cd {work_dir} && cat {prompt_dump_file} | {opencode_bin} {" ".join(opencode_args)}'
                )
            
            # 非零退出但文件存在，记录警告
            log(f'  ⚠ [{label}] exit={process.returncode} in {duration:.1f}s (outputFile 存在)')
        else:
            log(f'  ✅ [{label}] exit=0 in {duration:.1f}s')
        
        # 最终检查 outputFile
        if not os.path.exists(output_file):
            raise RuntimeError(
                f'opencode [{label}] exit {process.returncode} in {duration:.1f}s, '
                f'但未找到 outputFile {output_file}。\n'
                f'{opencode_log_tail()}\n'
                f'replay: cd {work_dir} && cat {prompt_dump_file} | {opencode_bin} {" ".join(opencode_args)}'
            )
        
        return {
            'output_file': output_file,
            'log_file': log_file,
            'prompt_file': prompt_dump_file,
            'exit_code': process.returncode,
        }
        
    except KeyboardInterrupt:
        timer.cancel()
        log(f'  ⚠ [{label}] 被用户中断')
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except:
            pass
        raise
    except Exception as e:
        timer.cancel()
        if not timed_out:
            raise


if __name__ == '__main__':
    # 测试代码
    import argparse
    
    parser = argparse.ArgumentParser(description='Run opencode with prompt')
    parser.add_argument('--prompt', required=True, help='Prompt file path')
    parser.add_argument('--context', default='{}', help='Context JSON')
    parser.add_argument('--instruction', required=True, help='Instruction')
    parser.add_argument('--work-dir', required=True, help='Working directory')
    parser.add_argument('--output', required=True, help='Output file path')
    parser.add_argument('--label', default='test', help='Label')
    
    args = parser.parse_args()
    
    result = run_opencode(
        prompt_file=args.prompt,
        context=json.loads(args.context),
        instruction=args.instruction,
        work_dir=args.work_dir,
        output_file=args.output,
        label=args.label,
    )
    
    print(f'✅ Success: {result}')
