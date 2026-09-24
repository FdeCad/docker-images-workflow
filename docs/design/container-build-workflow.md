# 容器构建修复工作流 — 设计文档

**状态:** 已发布
**日期:** 2026-09-25

---

## 一、背景与动机

### 1.1 旧流程的问题

旧流程在 `code-fix` 阶段由 AI 直接**修改 Dockerfile 文本**，然后提交、等 CI 重新 build：

```
读 CI 日志 → AI 猜一个修复 → 改 Dockerfile 文本 → commit → CI 整 build → 失败 → 再猜……
```

缺陷：

| 问题 | 说明 |
|------|------|
| **修复未验证** | AI 只改文本，从不真正 build，改完是否正确全靠 CI 下一次 build 兜底 |
| **重试昂贵** | 每次失败都是一次完整的 CI build（多架构并行、拉依赖、编译），又慢又耗资源 |
| **漏修 bug** | 修一处引入另一处（如换了下载源但漏了校验和），要等下一轮 build 才暴露 |
| **收敛慢** | 一个 PR 可能重试 6 次仍不通过，最终人工介入 |

### 1.2 新流程的思路

把「改」和「验」合并到**同一处、同一个容器会话**里：在自托管 runner 上起一个容器，把 PR 的 Dockerfile 指令**逐条 `docker exec` 执行**，哪条失败就当场诊断、跑修复命令、重试该条。容器文件系统状态保留，成功的 `yum install` 不会被重跑——这就是「边跑边修」。

全部跑通后，把**实际跑通的那套命令序列反推回 Dockerfile**（只改失败相关的行 = 最小 diff），最后用 `docker build` 从零验证这份 Dockerfile 真能 build。

由于每一步修复都在容器里实测过，成功率远高于「盲改文本」。

### 1.3 双架构（amd64 + arm64）

目标是在 arm64 和 amd64 上都 build 通过。采用**串行 + 冻结 + 复验**策略：

1. **arm64 首修**：在 arm64 runner 上跑通，得到 arm 冻结版 Dockerfile。
2. **amd64 续修**：用 arm 冻结版在 amd64 runner 上 build；若有问题再修，但修复必须**架构中立**或用**架构守卫**（`if [ "$(uname -m)" = "x86_64" ]`），不得破坏 arm。
3. **arm64 复验**：最终 Dockerfile 再回 arm64 做一次确定性 `docker build`，确认 amd64 的修复没有破坏 arm。

---

## 二、整体流程

```
ci_failed PR（监控 stream-pr-events 不变）
        ↓
[ci-log-analysis]  AI 读日志诊断根因 → ci-analysis.md（作为 build agent 的「已知根因提示」）
        ↓ dispatch build-fix (arch=arm64)
[build-fix arm64]  自托管 ARM64 runner：起容器 → 逐条跑+修 → 反推 Dockerfile → docker build 验证
        ↓ 成功，冻结 Dockerfile → dispatch build-fix (arch=amd64)
[build-fix amd64]  自托管 AMD64 runner：用 arm 冻结版 build；失败则架构中立地修
        ↓ dispatch verify-arm
[verify-arm]       自托管 ARM64 runner：对最终 Dockerfile 做确定性 docker build
        ↓ 成功 → dispatch code-fix
[code-fix]         把最终 Dockerfile 作为最小 diff 落回 fix 分支 → commit → push → Fix PR
```

verify-arm 若失败：回 `build-fix (amd64)` 再修（`verify_count` 递增，最多 2 轮），仍失败则评论 PR 通知人工。

---

## 三、各阶段职责

| 阶段 | 触发 | 执行环境 | 输出 |
|------|------|---------|------|
| ci-log-analysis | dispatch | ubuntu-latest | `ci-fix-log/{pr}/ci-analysis.md`（提示） |
| build-fix (arm64/amd64) | dispatch | 自托管 runner（ARM64/AMD64） | `derived-dockerfile` + `dockerfile-target` + `build-log-{arch}.md` |
| verify-arm | dispatch | 自托管 ARM64 runner | `build-log-verify-arm64.md`，dispatch code-fix |
| code-fix | dispatch | ubuntu-latest | 最小 diff commit → push → Fix PR |

### ci-fix-log 分支新增文件

| 路径 | 内容 |
|------|------|
| `ci-fix-log/{pr}/derived-dockerfile` | 反推出的最终 Dockerfile 内容（arm 冻结 → amd64 更新） |
| `ci-fix-log/{pr}/dockerfile-target` | 被修复的 Dockerfile 在源仓库的相对路径 |
| `ci-fix-log/{pr}/build-log-{arch}.md` | 构建命令序列 + 修复说明（audit 用） |

---

## 四、build agent 工作方式

`.github/agents/dockerfile-builder.md` 定义的行为，运行在自托管 runner 上（Claude Code / OpenCode，`--dangerously-skip-permissions`，有 docker 权限）：

1. `docker pull <base_image>` → `docker run -d --name <container> <base_image> sleep infinity`
2. 按顺序重放 Dockerfile 指令：`RUN` → `docker exec`；`ENV`/`WORKDIR` → 同步容器环境；`COPY`/`ADD` → `docker cp`（若失败点不在此则保持原样）
3. 失败 → 读错误 → 跑修复命令 → 重跑原命令直到成功，记录「原命令 + 修复命令」
4. 跑通后把失败的 `RUN` 替换为「修复后实际跑通的等价命令」，其余行原样保留 → 写出最终 Dockerfile（最小 diff）
5. `docker build -f <dockerfile_path> .` 从零验证；build 失败说明反推有偏差（WORKDIR 顺序、环境变量丢失等），修正反推后重 build，直到成功
6. 写 `derived_file`（最终 Dockerfile 纯文本）和 `output_file`（构建日志）

关键点：**容器会话是快速迭代的草稿区，`docker build` 从零构建才是「真的能用」的裁判。**

---

## 五、自托管 Runner 接入

需要两台机器（arm64、amd64），各自：

1. 安装 GitHub self-hosted runner，打标签：
   - arm64 机器 → 标签 `self-hosted, ARM64`
   - amd64 机器 → 标签 `self-hosted, AMD64`
2. runner 用户加入 `docker` 组，能免 sudo 执行 `docker run` / `docker exec` / `docker build`
3. 能访问网络拉取基础镜像（如 openEuler 官方镜像）与目标仓库
4. Python 3.11（`actions/setup-python` 会自动安装）

工作流的 `build-fix` job 通过 `matrix.arch` 选择对应的 runner 标签。

---

## 六、失败与重试边界

- **arm64 build-fix 失败**（无法收敛）→ job 失败，`derived-dockerfile` 未产出，链终止，日志可查。
- **amd64 build-fix 失败** → 同上。
- **verify-arm 失败**（amd64 修复破坏了 arm）→ 回 amd64 再修（`verify_count` 递增，最多 2 轮）；仍失败 → 评论 PR 通知人工。
- **code-fix 拒绝提交**：当 `derived-dockerfile` 目标路径不在原始 PR 文件列表内时直接报错，绝不越界提交。

---

## 七、新增 / 修改文件

| 类型 | 文件 | 说明 |
|------|------|------|
| 新增 | `.github/agents/dockerfile-builder.md` | build agent 角色定义 |
| 新增 | `scripts/stages/build-fix.py` | build / verify 两模式的编排脚本 |
| 新增 | `scripts/lib/build_runner.py` | Dockerfile 解析/定位/diff + docker CLI 封装 |
| 新增 | `tests/test_build_runner.py` | 纯逻辑单测 |
| 修改 | `scripts/stages/code-fix.py` | 改为应用 derived Dockerfile（不再调用 AI 现改） |
| 修改 | `scripts/stages/ci-log-analysis.py` | dispatch 目标改为 build-fix arm64 |
| 修改 | `scripts/lib/ci_data.py` | 新增 derived-dockerfile / dockerfile-target / build-log 路径 |
| 修改 | `scripts/lib/stage_common.py` | 新增共享 `dispatch_phase` |
| 修改 | `.github/workflows/pr-ci-fix-trigger.yml` | 新增 build-fix / verify-arm job，精简 code-fix |
