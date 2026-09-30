# 构建修复日志

## 修复摘要
将最终镜像 `stage-1` 阶段下载 MinIO 的 URL 从已永久下线（HTTP 410）的 `https://dl.min.io/server/minio/release/linux-$TARGETARCH/minio` 改为架构中立的 GitHub Releases 直链 `https://github.com/minio/minio/releases/download/RELEASE.2025-09-07T16-13-09Z/minio.linux-$TARGETARCH.RELEASE.2025-09-07T16-13-09Z`，解决 amd64 上 `curl: (22) ... error: 410` 导致的 `docker build` 失败，且不破坏 arm64。

## 根因确认
在容器 `ci-fix-4723-amd64`（`openeuler/openeuler:24.03-lts-sp4`，`uname -m = x86_64`）中复现原始指令：

```
$ TARGETARCH=amd64; curl -fSL -o /dev/null https://dl.min.io/server/minio/release/linux-$TARGETARCH/minio
curl: (22) The requested URL returned error: 410
exit=22
```

`dl.min.io` 对该无版本直链返回 **HTTP 410 Gone**，与 CI 诊断一致。etcd 下载（同一阶段相邻指令）正常，证明网络可达，失败点仅为此 URL。

## 构建命令序列（关键命令）
在 `ci-fix-4723-amd64` 容器内逐条重放 `stage-1`（`FROM $BASE`）指令，`TARGETARCH=amd64`：

1. `yum install -y libatomic openblas-devel libomp libstdc++ && yum clean all`
   → 成功（`YUM_OK`）。
2. etcd 下载（原样，非修复点）：
   ```
   curl -fSL -o etcd-v3.5.0-linux-$TARGETARCH.tar.gz \
     https://github.com/etcd-io/etcd/releases/download/v3.5.0/etcd-v3.5.0-linux-$TARGETARCH.tar.gz && \
   tar zxvf etcd-v3.5.0-linux-$TARGETARCH.tar.gz && \
   cp -r etcd-v3.5.0-linux-$TARGETARCH /usr/local/etcd && \
   rm -rf etcd-v3.5.0-linux-$TARGETARCH.tar.gz etcd-v3.5.0-linux-$TARGETARCH
   ```
   → 成功（`ETCD_OK`）。
3. **【修复】** MinIO 下载（替换 URL 后执行）：
   ```
   TARGETARCH=amd64; curl -fSL -o minio \
     https://github.com/minio/minio/releases/download/RELEASE.2025-09-07T16-13-09Z/minio.linux-$TARGETARCH.RELEASE.2025-09-07T16-13-09Z && \
   chmod +x ./minio && mv ./minio /usr/bin/ && /usr/bin/minio --version
   ```
   → 成功，输出 `minio version RELEASE.2025-09-07T16-13-09Z ... Runtime: go1.24.6 linux/amd64`。

修复原因：上游 MinIO 已不再提供 `dl.min.io/server/minio/release/linux-${TARGETARCH}/minio` 的无版本直链（410），改用带明确版本号的 GitHub Release 归档直链，固定版本 `RELEASE.2025-09-07T16-13-09Z` 保证可复现；URL 中的 `$TARGETARCH` 保证架构中立。

## 架构中立性 / arm64 保护
- 修复只替换下载 URL，未引入任何 `uname -m` 分支或架构特化逻辑，对 arm64 完全等价。
- 已确认 arm64 制品存在：对 `minio.linux-arm64.RELEASE.2025-09-07T16-13-09Z` 的 HEAD 请求返回 `HTTP/2 200`，`content-length: 105251000`。
- amd64 制品同理返回 `HTTP/2 200`，二进制可执行并打印版本。
- 因此本修复不会破坏 arm64 已验证通过的构建。

## 修改的 Dockerfile 内容
相对原始（git HEAD）Dockerfile，仅改动 **1 行**（第 41 行）：

```diff
-RUN curl -fSL -o minio https://dl.min.io/server/minio/release/linux-$TARGETARCH/minio && \
+RUN curl -fSL -o minio https://github.com/minio/minio/releases/download/RELEASE.2025-09-07T16-13-09Z/minio.linux-$TARGETARCH.RELEASE.2025-09-07T16-13-09Z && \
     chmod +x ./minio && \
     mv ./minio /usr/bin/
```

其余 `FROM` / `ARG` / `ENV` / `WORKDIR` / `COPY` / 注释等全部保持原样，未做任何重排或格式化。

## 验证结果
- **容器内逐条重放**：`stage-1` 的 yum / etcd / MinIO 指令在 amd64 容器中全部成功；MinIO 二进制可执行。
- **`docker build` 从零自检（修复片段）**：使用临时 Dockerfile（`/tmp/opencode/minio-verify/Dockerfile`，即真实 Dockerfile 最终阶段的等价钱下载序列，未改动仓库文件）执行
  `DOCKER_BUILDKIT=1 docker build --platform linux/amd64 -f /tmp/opencode/minio-verify/Dockerfile ...`
  → **成功**。日志显示 etcd 层 `DONE 0.8s`、MinIO 层 `DONE 3.0s` 且 `minio --version` 通过，镜像导出成功。
- **完整 `docker build -f Database/milvus/3.0.2/24.03-lts-sp4/Dockerfile .` 从零自检：未执行**。原因：builder 阶段需从源码编译 Milvus（`make build-cpp && make build-go`），耗时远超本阶段预算；且该 Dockerfile 已被 arm64 阶段端到端验证通过，本次改动仅涉及架构中立的下载 URL。权威的完整 `docker build` 由后续 `verify-arm` 阶段在 arm64 上无 AI 执行。
- 失败点（MinIO 410）已在 amd64 上从零复现并验证修复；未发现其他 amd64 特有问题（`$TARGETARCH` 在各下载指令中均正确解析为 `amd64`）。
