# CI 失败分析报告

## 基本信息
- PR: #4723 — 【自动升级】milvus容器镜像升级至3.0.2版本.
- 失败类型: `build-error`（下载源 HTTP 410 Gone 导致 Docker 构建失败）
- 置信度: 高
- 知识库匹配: 新模式
- 新模式标题: MinIO 下载链接失效
- 新模式症状关键词: curl: (22), HTTP 410, dl.min.io, minio, release/linux, exit code: 22

## 根因分析

### 直接错误
```
#10 [stage-1 4/9] RUN curl -fSL -o minio https://dl.min.io/server/minio/release/linux-amd64/minio &&     chmod +x ./minio &&     mv ./minio /usr/bin/
#10 0.065   % Total    % Received % Xferd  Average Speed   Time    Time     Time  Current
#10 0.065                                  Dload  Upload   Total   Spent    Left  Speed
#10 0.065   0   405    0     0    0      0 --:--:-- --:--:--     0
#10 0.582 curl: (22) The requested URL returned error: 410
#10 ERROR: process "/bin/sh -c curl -fSL -o minio https://dl.min.io/server/minio/release/linux-$TARGETARCH/minio &&     chmod +x ./minio &&     mv ./minio /usr/bin/" did not complete successfully: exit code: 22
```

```
Dockerfile:41
--------------------
  41 | >>> RUN curl -fSL -o minio https://dl.min.io/server/minio/release/linux-$TARGETARCH/minio && \
  42 | >>>     chmod +x ./minio && \
  43 | >>>     mv ./minio /usr/bin/
--------------------
ERROR: failed to solve: process "..." did not complete successfully: exit code: 22
```

### 根因定位
- 失败位置: `Database/milvus/3.0.2/24.03-lts-sp4/Dockerfile:41-43`
- 失败原因: 下载 MinIO 服务端二进制的 URL `https://dl.min.io/server/minio/release/linux-${TARGETARCH}/minio` 返回 **HTTP 410 Gone**（资源已永久移除），MinIO 上游已停止在该路径分发二进制，`curl -f` 遇 410 返回退出码 22，构建中断。

### 与 PR 变更的关联
本 PR 新增了 `Database/milvus/3.0.2/24.03-lts-sp4/Dockerfile`，其中第 41-43 行首次引入了从 `dl.min.io` 下载 minio 的命令。该下载步骤是本次失败的直接触发点，属于 PR 新增内容导致，与历史镜像无关。PR 中的下载 URL 沿用了失效的 minio 分发路径。

## 修复方向

### 方向 1（置信度: 高）
将 minio 二进制的获取方式改为**当前仍可用的官方分发渠道**。MinIO 已变更分发策略，旧的 `dl.min.io/server/minio/release/...` 路径返回 410。可考虑：
- 改用 MinIO 官方新的下载地址（需在修复前实际验证 URL 可访问、并可返回二进制而非 HTML/错误页）；
- 或参考模式16（RPM 停止发布→多阶段构建），从官方 MinIO 容器镜像中 `COPY` 二进制文件（例如 `minio/minio` 镜像的 `/usr/bin/minio`），避免直接依赖已失效的直链。

### 方向 2（可选）
若必须固定版本，可在 `ARG` 中显式锁定一个仍然发布制品的 MinIO 版本，并确认该版本对应下载路径有效；仅更换域名/镜像站而不改变已被上游废弃的 v3 API 分发路径，很可能仍 410。

## 需要进一步确认的点
1. 修复前必须验证所选 MinIO 下载 URL 在 CI 构建环境中确实可达且返回的是二进制文件（410 属永久移除，不是临时网络问题）。
2. 需确认是否要求固定 MinIO 版本（影响 URL 是否能长期稳定）。
3. `meta.yml` 中新增的 `3.0.2-oe2403sp4` 条目未限制架构，但本次失败为下载源问题而非架构问题；若后续在 aarch64 上出现同类 minio 下载失败，应同步核实 arm64 版本 URL 是否同样失效。

## 修复验证要求
本修复方向涉及替换外部下载源（非正则 patch），code-fixer 在提交前必须：
1. 实际访问/下载所选 minio 获取方式的 URL（或所引用的官方镜像），确认返回 200 且内容为可执行二进制，而非 410/404/HTML。
2. 若采用多阶段 `COPY --from=minio/minio` 方案，需确认该镜像存在且 `/usr/bin/minio` 路径正确，并注意 `TARGETARCH` 架构兼容（amd64 与 arm64 均需覆盖）。
