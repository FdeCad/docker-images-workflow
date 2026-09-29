# CI 失败分析报告

## 基本信息
- PR: #4723 — 【自动升级】milvus容器镜像升级至3.0.2版本.
- 失败类型: build-error
- 置信度: 高
- 知识库匹配: 新模式
- 新模式标题: MinIO下载源410
- 新模式症状关键词: curl: (22), 410 Gone, dl.min.io, minio, exit code: 22

## 根因分析

### 直接错误
```
#10 [stage-1 4/9] RUN curl -fSL -o minio https://dl.min.io/server/minio/release/linux-amd64/minio &&     chmod +x ./minio &&     mv ./minio /usr/bin/
#10 0.065   % Total    % Received % Xferd  Average Speed   Time    Time     Time  Current
#10 0.065                                  Dload  Upload   Total   Spent    Left  Speed
#10 0.065 \r  0   405    0     0    0     0      0 --:--:-- --:--:--     0\r  0   405
#10 0.582 curl: (22) The requested URL returned error: 410
#10 ERROR: process "/bin/sh -c curl -fSL -o minio https://dl.min.io/server/minio/release/linux-$TARGETARCH/minio &&     chmod +x ./minio &&     mv ./minio /usr/bin/" did not complete successfully: exit code: 22
------
Dockerfile:41
--------------------
  41 | >>> RUN curl -fSL -o minio https://dl.min.io/server/minio/release/linux-$TARGETARCH/minio && \
  42 | >>>     chmod +x ./minio && \
  43 | >>>     mv ./minio /usr/bin/
--------------------
ERROR: failed to solve: process ... did not complete successfully: exit code: 22
Finished: FAILURE
```

### 根因定位
- 失败位置: `Database/milvus/3.0.2/24.03-lts-sp4/Dockerfile:41`
- 失败原因: `curl -fSL` 从 `https://dl.min.io/server/minio/release/linux-amd64/minio` 下载 MinIO 二进制时，服务器返回 HTTP 410 Gone（curl 错误码 22），即该下载路径已不再提供制品，镜像构建在 stage-1 第 4 步失败。

### 与 PR 变更的关联
本 PR 新增了 `Database/milvus/3.0.2/24.03-lts-sp4/Dockerfile`，其中第 41-43 行的 MinIO 下载步骤引用了 `dl.min.io` 的旧发布路径。该失败完全由本 PR 新增 Dockerfile 触发：
- 日志中其他步骤（yum 安装依赖、Go 下载、etcd v3.5.0 下载与解压 `#9 DONE 1.3s`）均成功，唯一下载失败的是 MinIO。
- 410 属于"资源永久移除"，与网络抖动/超时不同，说明是 URL 本身失效而非 CI 基础设施问题。
- `#8` builder 阶段仍在下载 RPM，但因 MinIO 步骤失败被 `CANCELED`，这是级联结果而非根因。

注意：日志末尾为 `Finished: FAILURE`，属于真实构建失败，非"日志成功但状态失败"的场景，无需按 infra-error 处理。

## 修复方向

### 方向 1（置信度: 高）
`dl.min.io` 旧发布路径返回 410 Gone，需将 MinIO 下载源更换为当前仍提供制品的官方地址（MinIO 已调整其发布/分发域名与目录结构）。提交前必须实际访问验证目标 URL 可返回二进制而非 410/404。

### 方向 2（可选）
若上游官方已不再提供稳定的直接二进制下载路径，可改用多阶段构建从 `minio/minio` 官方镜像中 `COPY` 二进制（参考知识库模式16"RPM 包停止发布（换多阶段构建绕过）"的思路），避免依赖易失效的下载 URL。

## 需要进一步确认的点
1. MinIO 官方当前有效的 x86_64 / aarch64 二进制下载地址（需确认新域名/新路径，并确认对 `$TARGETARCH`（amd64/arm64）两个架构均可用）。
2. 目标 URL 是否可被 `curl -fSL` 直接下载（不带重定向失败），以确保替换后不再返回 410。
3. 该 Dockerfile 中其他外部下载源（Go、etcd v3.5.0）本次已成功，无需处理。

## 修复验证要求
本修复涉及替换外部下载 URL。code-fixer 在提交前，必须实际使用 `curl -fSL -o /dev/null -w "%{http_code}" <新URL>` 分别验证 `TARGETARCH=amd64` 与 `arm64` 两种架构对应的下载地址返回 200（而非 410/404），确认链接有效后再提交。禁止仅凭推测直接替换 URL。
