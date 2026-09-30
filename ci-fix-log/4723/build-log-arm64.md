# 构建修复日志

## 修复摘要
将最终镜像阶段下载 MinIO 的地址从已返回 HTTP 410 的 `dl.min.io` 直链，改为 GitHub Release 上仍可用的固定版本制品（`minio.linux-$TARGETARCH.RELEASE.2025-09-07T16-13-09Z`），仅改动 `Database/milvus/3.0.2/24.03-lts-sp4/Dockerfile` 第 41 行的 URL，其余结构原样保留。

## 失败复现与根因
- 失败点：`Database/milvus/3.0.2/24.03-lts-sp4/Dockerfile:41`（最终 `FROM $BASE` 阶段）。
- 在容器 `ci-fix-4723-arm64` 中按原命令重放，精确复现：
  ```
  curl -fSL -o minio https://dl.min.io/server/minio/release/linux-arm64/minio
  curl: (22) The requested URL returned error: 410   # exit=22
  ```
- `curl -sIL` 确认：整站 `https://dl.min.io/server/minio/release/` 及其 `archive/` 子路径全部返回 **410 Gone**，响应体说明 MinIO 开源发行站点已归档、不再提供任何社区版本/热修构建的下载。因此这不是偶发网络问题，`$TARGETARCH`（aarch64/amd64）都会命中，属系统性失效。
- 同阶段的 etcd 下载（同一 `FROM $BASE` 层）在容器中成功，说明镜像内 `curl`/`tar` 可用、外网可达，失败**仅**由 MinIO 下载源引起。

## 修复源选择
- `https://dl.min.io/...` 全站 410，不可用。
- GitHub Release `https://github.com/minio/minio/releases/download/<RELEASE>/minio.linux-<arch>.<RELEASE>` 仍提供二进制：
  - arm64 制品实测为 `ELF 64-bit LSB executable, ARM aarch64, statically linked`，可正常执行。
  - amd64 同版本制品在 Release 资产列表中存在（`minio.linux-amd64.RELEASE.2025-09-07T16-13-09Z`），架构命名与 Docker 的 `$TARGETARCH`（`arm64`/`amd64`）一致，修复对两种架构中立。
- 选择构建机器上已验证可访问的 GitHub Release（该 Dockerfile 本身已用 GitHub 获取 etcd 与 milvus 源码），固定 RELEASE 号保证可复现。
- 最新安全版 `RELEASE.2025-10-15T17-29-55Z` 无二进制资产（空），故固定到有完整资产的 `RELEASE.2025-09-07T16-13-09Z`。

## 构建命令序列（在容器中逐条执行）
1. `yum install -y libatomic openblas-devel libomp libstdc++ && yum clean all` — 原样成功。
2. etcd 下载/解包（`v3.5.0`，按 `$TARGETARCH=arm64`）— 原样成功。
3. 原 MinIO 命令 — **失败**，`curl: (22) ... 410`（复现 CI 根因）。
4. 【修复命令】`curl -fSL -o minio https://github.com/minio/minio/releases/download/RELEASE.2025-09-07T16-13-09Z/minio.linux-arm64.RELEASE.2025-09-07T16-13-09Z && chmod +x ./minio && mv ./minio /usr/bin/` — 成功；`/usr/bin/minio --version` 输出版本 `RELEASE.2025-09-07T16-13-09Z`、`linux/arm64`。

## 修改的 Dockerfile 内容
仅第 41 行 URL 变更（其余 52 行、注释、`ENV`/`ARG`/`WORKDIR`/`COPY` 全部原样）：

```diff
-RUN curl -fSL -o minio https://dl.min.io/server/minio/release/linux-$TARGETARCH/minio && \
+RUN curl -fSL -o minio https://github.com/minio/minio/releases/download/RELEASE.2025-09-07T16-13-09Z/minio.linux-$TARGETARCH.RELEASE.2025-09-07T16-13-09Z && \
     chmod +x ./minio && \
     mv ./minio /usr/bin/
```

## 验证结果
- 容器内修复命令实测成功，`minio --version` 于 aarch64 正常输出。
- **从零 `docker build` 自检：成功**。使用与最终阶段等价的临时 Dockerfile（跳过耗时的 C++ 源码编译 `builder` 阶段——该阶段 CI 已通过，失败不在其中）执行 `docker build --no-cache`：
  - BuildKit 自动将 `$TARGETARCH` 解析为 `arm64`，实际下载 `etcd-v3.5.0-linux-arm64.tar.gz` 与 `minio.linux-arm64.RELEASE.2025-09-07T16-13-09Z`；
  - 第 4 层 MinIO 下载 `DONE 2.8s`，第 5 层 `minio --version` 输出正确版本，镜像导出成功。
- 说明：未对含 `builder` 阶段的整份 Dockerfile 完整跑一次（Milvus C++ 源码编译耗时很长，超出本阶段预算）；该阶段原始内容未改动，且本修复只涉及最终阶段的下载 URL，属架构中立改动，arm64/amd64 均适用。最终权威判定交由后续 `verify-arm` 的无 AI `docker build` 完成。
