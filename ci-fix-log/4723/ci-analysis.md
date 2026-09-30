# CI 失败分析报告

## 基本信息
- PR: #4723 — 【自动升级】milvus容器镜像升级至3.0.2版本.
- 失败类型: build-error
- 置信度: 高
- 知识库匹配: 新模式
- 新模式标题: MinIO下载源410
- 新模式症状关键词: 410, curl: (22), dl.min.io, minio, The requested URL returned error

## 根因分析

### 直接错误
```
#10 [stage-1 4/9] RUN curl -fSL -o minio https://dl.min.io/server/minio/release/linux-amd64/minio &&     chmod +x ./minio &&     mv ./minio /usr/bin/
#10 0.065   % Total    % Received % Xferd  Average Speed   Time    Time     Time  Current
#10 0.065  0   405    0     0    0      0      0 --:--:-- --:--:-- --:--:--     0
#10 0.582 curl: (22) The requested URL returned error: 410
#10 ERROR: process "/bin/sh -c curl -fSL -o minio https://dl.min.io/server/minio/release/linux-$TARGETARCH/minio &&     chmod +x ./minio &&     mv ./minio /usr/bin/" did not complete successfully: exit code: 22
------
Dockerfile:41
  41 | >>> RUN curl -fSL -o minio https://dl.min.io/server/minio/release/linux-$TARGETARCH/minio && \
  42 | >>>     chmod +x ./minio && \
  43 | >>>     mv ./minio /usr/bin/
ERROR: failed to solve: process ... did not complete successfully: exit code: 22
```

### 根因定位
- 失败位置: `Database/milvus/3.0.2/24.03-lts-sp4/Dockerfile:41`
- 失败原因: 最终镜像 `stage-1` 阶段通过 `curl -fSL` 从 `https://dl.min.io/server/minio/release/linux-amd64/minio` 下载 MinIO 二进制，服务器返回 **HTTP 410 Gone**（资源已被永久移除，`curl -f` 对 4xx 直接以 exit code 22 失败），导致该 RUN 层构建失败、整个 `docker build` 失败。

### 与 PR 变更的关联
- 该 Dockerfile 为本 PR **新增文件**（`Database/milvus/3.0.2/24.03-lts-sp4/Dockerfile`，53 行全部新增），第 41 行的 MinIO 下载指令即来自此 PR 新增内容，与失败**直接相关**。
- 同阶段紧邻的 etcd 下载（`#9`）在本次运行中 `DONE 1.3s`，说明构建网络本身可达；失败点**仅**为 `dl.min.io` 这一特定 URL 返回 410，不是网络不通。
- 该 URL 使用 `$TARGETARCH` 变量，aarch64 架构同样会命中同一失效路径，属系统性问题而非偶发。

## 修复方向

### 方向 1（置信度: 高）
MinIO 已对 `dl.min.io/server/minio/release/linux-${TARGETARCH}/minio` 这一"最新版直链"返回 410（上游改为不再提供无版本号的直链分发）。需将下载源更换为当前可用的 MinIO 分发形式，例如：
- 使用带版本/时间戳的归档直链（`.../archive/minio.<RELEASE>` 形式），并将版本号参数化固定，保证可复现；或
- 改为多阶段构建，从官方 MinIO 镜像中直接 `COPY` 二进制（参考历史模式16 的做法，规避下载源变动）。

### 方向 2（可选）
若上游确实仍提供该二进制，检查是否遗漏了 MinIO 要求的版本路径/参数（410 表示资源被移除，通常需改用带具体版本号的 archive 地址）。建议确认 Milvus 官方 Dockerfile 中对应版本实际使用的 MinIO 下载方式后对齐。

## 需要进一步确认的点
1. Milvus 3.0.2 对 MinIO 是否有版本要求（其 `docker-compose`/依赖声明中固定的 MinIO RELEASE 号），以确定应固定的具体版本。
2. 目标 MinIO 版本在 `https://dl.min.io/server/minio/release/linux-${TARGETARCH}/archive/` 下是否存在对应制品（需 x86-64 与 aarch64 两个架构都确认）。
3. 若改用官方镜像 `COPY` 方案，需确认 MinIO 官方镜像内二进制路径（通常为 `/usr/bin/minio` 或 `/minio`）。
4. 由于是**新增镜像**，还需确认 `Database/milvus/meta.yml`、`README.md`、`doc/image-info.yml` 的一致性（本 PR 已同步更新，非本次失败原因，仅提示）。

## 修复验证要求（仅当修复涉及正则 patch 外部源文件时填写）
本次不涉及正则 patch 外部源文件，无需填写。
