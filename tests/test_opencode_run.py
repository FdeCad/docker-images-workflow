#!/usr/bin/env python3
"""opencode_run 的行为测试（用假二进制，不需要真的 opencode）。

重点覆盖两个曾经出过事的地方：
  1. 超时必须向上抛出——旧的兜底 `except Exception` 会把它吞掉，
     函数隐式返回 None，调用方只看到「产物不存在」。
  2. stdout/stderr 必须实时转发——旧实现用 communicate()，要等进程退出
     才一次性返回，构建类任务几十分钟的日志全是空白。
"""

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, PROJECT_ROOT)

from scripts.lib.opencode_run import run_opencode  # noqa: E402

# 需要 bash + stdbuf（run_opencode 用它们组装命令行）
_NEEDS_POSIX = shutil.which('bash') is not None and shutil.which('stdbuf') is not None


class _StderrRecorder:
    """替换 sys.stderr，记录每行到达的时刻。"""

    def __init__(self):
        self.lines = []

    def write(self, s):
        if s.strip():
            self.lines.append((time.time(), s.rstrip('\n')))

    def flush(self):
        pass

    def text(self):
        return '\n'.join(line for _, line in self.lines)

    def time_of(self, needle):
        for ts, line in self.lines:
            if needle in line:
                return ts
        return None


@unittest.skipUnless(_NEEDS_POSIX, '需要 bash + stdbuf')
class TestRunOpencode(unittest.TestCase):

    def setUp(self):
        # run_opencode 把路径直接嵌进 bash 命令行，Windows 的反斜杠会被 bash 当转义符。
        # 统一转成正斜杠（Windows Python 与 Git Bash 都认）。
        self.tmp = tempfile.mkdtemp().replace('\\', '/')
        self.prompt = self.tmp + '/prompt.txt'
        with open(self.prompt, 'w', encoding='utf-8') as f:
            f.write('do something')
        self.output = self.tmp + '/out.md'
        self._old_stderr = sys.stderr
        self.rec = _StderrRecorder()
        sys.stderr = self.rec
        self._old_bin = os.environ.get('OPENCODE_BIN')

    def tearDown(self):
        sys.stderr = self._old_stderr
        if self._old_bin is None:
            os.environ.pop('OPENCODE_BIN', None)
        else:
            os.environ['OPENCODE_BIN'] = self._old_bin

    def _fake_bin(self, body: str) -> str:
        """写一个假 opencode 脚本，返回路径。

        OPENCODE_BIN 会被原样拼进 bash 命令，这里写成 `bash <path>`：
        既绕开 Windows 上的可执行位/shebang 问题，也不影响被测的转发与超时逻辑。
        """
        path = self.tmp + '/fake-opencode'
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write('#!/bin/bash\n' + body)
        os.environ['OPENCODE_BIN'] = f'bash {path}'
        return path

    def _run(self, timeout_ms=20000):
        return run_opencode(
            prompt_file=self.prompt,
            context={},
            instruction='test',
            work_dir=self.tmp,
            output_file=self.output,
            timeout_ms=timeout_ms,
            label='smoke',
        )

    def test_output_is_streamed_not_buffered(self):
        """第 1 行必须在进程结束之前就到达日志。"""
        # 先吐一行，睡 4 秒，再吐一行并产出文件
        self._fake_bin(
            'echo "EARLY-MARKER"\n'
            'sleep 4\n'
            'echo "LATE-MARKER"\n'
            f'echo done > "{self.output}"\n'
        )
        t0 = time.time()
        result = self._run(timeout_ms=20000)
        total = time.time() - t0

        self.assertEqual(result['exit_code'], 0)
        early = self.rec.time_of('EARLY-MARKER')
        late = self.rec.time_of('LATE-MARKER')
        self.assertIsNotNone(early, f'早到的输出没被转发:\n{self.rec.text()}')
        self.assertIsNotNone(late, f'晚到的输出没被转发:\n{self.rec.text()}')
        # 关键断言：早的那行明显早于进程整体结束
        self.assertLess(early - t0, total * 0.6,
                        f'输出被缓冲了：EARLY 在 {early - t0:.2f}s 才出现，'
                        f'进程总耗时 {total:.2f}s')

    def test_timeout_raises_instead_of_returning_none(self):
        """超时必须抛 TimeoutError，而不是被吞掉后返回 None。"""
        self._fake_bin('echo "WORKING"\nsleep 60\n')
        t0 = time.time()
        with self.assertRaises(TimeoutError):
            self._run(timeout_ms=3000)
        self.assertLess(time.time() - t0, 30, '超时没有及时生效')

    def test_timeout_still_raises_when_output_file_exists(self):
        """即使产物已经落盘，超时依然要如实上报（由调用方决定是否采纳）。"""
        self._fake_bin(f'echo done > "{self.output}"\nsleep 60\n')
        with self.assertRaises(TimeoutError):
            self._run(timeout_ms=3000)

    def test_missing_output_file_raises_runtime_error(self):
        """进程正常退出但没产出文件 → RuntimeError。"""
        self._fake_bin('echo "nothing written"\n')
        with self.assertRaises(RuntimeError) as ctx:
            self._run(timeout_ms=20000)
        self.assertIn('outputFile', str(ctx.exception))

    def test_full_output_is_written_to_log_file(self):
        """完整输出（含第 1 行）必须落进 log file，而不只是尾部若干行。"""
        self._fake_bin(
            'for i in $(seq 1 300); do echo "LINE-$i"; done\n'
            f'echo done > "{self.output}"\n'
        )
        self._run(timeout_ms=30000)
        logs = [p for p in os.listdir(self.tmp) if p.startswith('opencode-smoke-')]
        self.assertTrue(logs, '没有生成 log file')
        body = Path(os.path.join(self.tmp, logs[0])).read_text(encoding='utf-8')
        self.assertIn('LINE-1\n', body, 'log file 丢了首行')
        self.assertIn('LINE-300', body, 'log file 丢了末行')


if __name__ == '__main__':
    unittest.main()
