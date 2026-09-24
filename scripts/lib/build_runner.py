#!/usr/bin/env python3
"""
build 阶段工具库：Dockerfile 解析 / 定位 / diff，容器与构建封装

纯逻辑（可单测）：
  - parse_dockerfile:   解析 Dockerfile 为指令序列（支持续行、跳过注释）
  - base_image:         提取 FROM 基础镜像
  - find_dockerfile_target: 从 PR 文件列表中定位唯一 Dockerfile
  - render_diff:        生成 unified diff（展示 / 校验用）
  - container_name:     计算确定性容器名（清理用）

Docker CLI 封装（依赖宿主机 docker，供 stage 脚本调用）：
  - docker_rm / docker_build
"""

import os
import subprocess
from dataclasses import dataclass
from typing import List, Optional

DOCKER_BUILD_TIMEOUT = int(os.environ.get('BUILD_DOCKER_TIMEOUT', '7200'))


@dataclass
class Instruction:
    keyword: str  # FROM / RUN / ENV / ARG / WORKDIR / COPY / ADD / ...
    args: str     # 参数（不含关键字）
    raw: str      # 原始逻辑行（含续行）


def parse_dockerfile(content: str) -> List[Instruction]:
    """把 Dockerfile 文本解析为逻辑指令序列。

    支持反斜杠续行；跳过空行与整行注释。不处理 `# escape=` / heredoc 等高级语法，
    对本工作流（定位 FROM、枚举 RUN）足够。
    """
    instructions: List[Instruction] = []
    buf: List[str] = []

    def flush() -> None:
        joined = '\n'.join(buf)
        stripped = joined.lstrip()
        if stripped and not stripped.startswith('#'):
            parts = stripped.split(None, 1)
            instructions.append(Instruction(
                keyword=parts[0].upper(),
                args=parts[1].lstrip() if len(parts) > 1 else '',
                raw=joined,
            ))

    for line in content.splitlines():
        if not buf and (not line.strip() or line.lstrip().startswith('#')):
            continue
        buf.append(line)
        if line.rstrip().endswith('\\'):
            continue
        flush()
        buf = []
    if buf:
        flush()
    return instructions


def base_image(content: str) -> Optional[str]:
    """提取 FROM 的基础镜像（镜像名:tag），跳过 --platform 等 flag。"""
    for inst in parse_dockerfile(content):
        if inst.keyword == 'FROM':
            for token in inst.args.split():
                if not token.startswith('-'):
                    return token
            return inst.args.strip() or None
    return None


def find_dockerfile_target(pr_files: List[str]) -> Optional[str]:
    """从 PR 文件列表中定位待修复的 Dockerfile；唯一命中才返回，否则 None。"""
    candidates = [
        f for f in pr_files
        if f.split('/')[-1].lower() == 'dockerfile'
        or f.split('/')[-1].lower().endswith('.dockerfile')
    ]
    return candidates[0] if len(candidates) == 1 else None


def render_diff(original: str, final: str, path: str = 'Dockerfile') -> str:
    """生成 unified diff，用于展示与校验。"""
    import difflib
    return ''.join(difflib.unified_diff(
        original.splitlines(keepends=True),
        final.splitlines(keepends=True),
        fromfile=f'a/{path}',
        tofile=f'b/{path}',
    ))


def container_name(pr_number: int, arch: str) -> str:
    return f'ci-fix-{pr_number}-{arch}'


def docker_rm(name: str) -> None:
    """尽力清理容器，忽略失败（容器可能已被 agent 自行移除）。"""
    try:
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True,
                       text=True, timeout=120)
    except Exception:
        pass


def docker_build(dockerfile_path: str, context: str,
                 timeout: int = DOCKER_BUILD_TIMEOUT) -> subprocess.CompletedProcess:
    """对指定 Dockerfile 执行 docker build（context 为仓库根目录，-f 指向 Dockerfile 相对路径）。

    返回 CompletedProcess，不在此处抛超时异常；调用方自行判断 returncode。
    """
    return subprocess.run(
        ['docker', 'build', '-f', dockerfile_path, '.'],
        cwd=context, capture_output=True, text=True, timeout=timeout,
    )
