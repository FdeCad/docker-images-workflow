# CI 失败分析报告

## 基本信息
- PR: #4723 — 【自动升级】milvus容器镜像升级至3.0.2版本.
- 失败类型: build-error
- 置信度: 高
- 知识库匹配: 新模式
- 新模式标题: MinIO下载返回410
- 新模式症状关键词: curl: (22), 410, dl.min.io, minio, release

## 根因分析

### 直接错误
```
#10 [stage-1 4/9] RUN curl -fSL -o minio https://dl.min.io/server/minio/release/linux-amd64/minio &&     chmod +x ./minio &&     mv ./minio /usr/bin/
#10 0.065 ...  0   405    0     0      0     0      0 --:--:-- --:--:-- --:--:--     0
#10 0.582 curl: (22) The requested URL returned error: 410
#10 ERROR: process "/bin/sh -c curl -fSL -o minio https://dl.min.io/server/minio/release/linux-$TARGETARCH/minio &&     chmod +x ./minio &&     mv ./minio /usr/bin/" did not complete successfully: exit code: 22
...
Dockerfile:41
  41 | >>> RUN curl -fSL -o minio https://dl.min.io/server/minio/release/linux-$TARGETARCH/minio && \
  42 | >>>     chmod +x ./minio && \
  43 | >>>     mv ./minio /usr/bin/
ERROR: failed to solve: process "/bin/sh -c curl -fSL -o minio ..." did not complete successfully: exit code: 22
```

### 根因定位
- 失败位置: `Database/milvus/3.0.2/24.03-lts-sp4/Dockerfile:41`
- 失败原因: MinIO 下载地址 `https://dl.min.io/server/minio/release/linux-$TARGETARCH/minio` 返回 HTTP 410（Gone），该"latest"直链已在上游被永久移除，`curl -f` 因返回码 ≥400 失败并以 exit code 22 终止构建。

### 与 PR 变更的关联
该 RUN 步骤由本 PR 新增（diff 中 `+RUN curl -fSL -o minio ...`，`new_file: True`），失败行与新增行完全对应，属于本次 PR 改动直接触发的失败，与历史代码无关。

## 修复方向

### 方向 1（置信度: 高）
更新 MinIO 下载来源，不再使用返回 410 的 `release/linux-$TARGETARCH/minio` 直链。可改为上游当前有效的归档 URL（`dl.min.io/server/minio/release/linux-$TARGETARCH/archive/` 下的具体 RELEASE 版本），或改用多阶段构建 `COPY --from=minio/minio:<tag>` 方式获取二进制（参考知识库模式16的做法）。

### 方向 2（可选，置信度: 中）
若上游已完全停止提供二进制直链，则改用 MinIO 官方镜像作为构建源进行 COPY，避免依赖易失效的 CDN 直链。

## 需要进一步确认的点
- 需确认 MinIO 对 openEuler 24.03-LTS-SP4 目标架构（amd64/arm64）当前实际可用的下载 URL 或官方镜像 tag。
- 需确认 `meta.yml` 中新增的 `3.0.2-oe2403sp4` 是否确实需要 amd64/arm64 双架构支持，以便选择对应的 minio 源。

## 修复验证要求
- code-fixer 在提交前，必须实际验证新的 MinIO 下载 URL 对 `TARGETARCH=amd64` 与 `arm64` 均返回 HTTP 200（而非 410/404），确认后再提交。
- 若采用 `COPY --from=minio/minio:<tag>` 方案，需确认该镜像 tag 真实存在且两个架构均有对应镜像。
