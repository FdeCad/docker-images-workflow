# 构建修复日志

## 修复摘要

最终镜像 `stage-1` 阶段从 `https://dl.min.io/server/minio/release/linux-$TARGETARCH/minio` 下载 MinIO 二进制时，上游返回 **HTTP 410 Gone**（`curl -f` → exit 22），导致 `Database/milvus/3.0.2/24.03-lts-sp4/Dockerfile:41` 该 RUN 层构建失败。将下载源改为 MinIO 官方 GitHub Release 的**带版本固定直链**（`RELEASE.2025-09-07T16-13-09Z`），仍使用 `$TARGETARCH`，架构中立、amd64/arm64 资源均存在。仅改动这一条 RUN 的 URL，其余 Dockerfile 内容原样保留。

## 构建命令序列

以下命令在 `openeuler/openeuler:24.03-lts-sp4` 起的一次性容器 `ci-fix-4723-amd64` 中逐条实测。

1. 起容器（复现环境）：
   `docker run -d --name ci-fix-4723-amd64 openeuler/openeuler:24.03-lts-sp4 sleep infinity`

2. **复现失败**（原始命令，amd64 展开后）：
   `docker exec -w / ci-fix-4723-amd64 sh -c 'curl -fSL -o minio https://dl.min.io/server/minio/release/linux-amd64/minio && chmod +x ./minio && mv ./minio /usr/bin/'`
   → `curl: (22) The requested URL returned error: 410`，`exit 22`。**确认 CI 根因**：该 URL 已被上游永久移除，与网络无关。

3. **验证修复 URL 的可用性**（修复命令）：
   `curl -sIL https://github.com/minio/minio/releases/download/RELEASE.2025-09-07T16-13-09Z/minio.linux-amd64.RELEASE.2025-09-07T16-13-09Z` → HTTP 200
   `curl -sIL https://github.com/minio/minio/releases/download/RELEASE.2025-09-07T16-13-09Z/minio.linux-arm64.RELEASE.2025-09-07T16-13-09Z` → HTTP 200（确认 arm64 资源同样存在，修复不破坏 arm64）

4. **在容器中执行修复后的指令**（替换 Dockerfile:41 的 URL）：
   `docker exec -w / ci-fix-4723-amd64 sh -c 'curl -fSL -o minio https://github.com/minio/minio/releases/download/RELEASE.2025-09-07T16-13-09Z/minio.linux-amd64.RELEASE.2025-09-07T16-13-09Z && chmod +x ./minio && mv ./minio /usr/bin/'`
   → `exit 0`；`minio --version` 输出 `minio version RELEASE.2025-09-07T16-13-09Z ... linux/amd64`。**修复生效**。

5. **回归验证 stage-1 其余指令**（确认无其它失败点）：
   - `yum install -y libatomic openblas-devel libomp libstdc++ && yum clean all` → `exit 0`
   - etcd 下载/解包：`curl -fSL -o etcd-v3.5.0-linux-amd64.tar.gz https://github.com/etcd-io/etcd/releases/download/v3.5.0/etcd-v3.5.0-linux-amd64.tar.gz && tar zxvf ... && cp -r ... /usr/local/etcd && rm -rf ...` → `exit 0`，`/usr/local/etcd/etcd --version` 正常。

6. **`docker build` 局部自检**（对改动指令做 BuildKit 真实构建，验证 `$TARGETARCH` 解析）：
   临时 Dockerfile（仅含该 RUN）执行 `docker build`，BuildKit 解析为 `minio.linux-amd64.RELEASE.2025-09-07T16-13-09Z`，下载 105M 成功、`minio --version` 通过、层导出成功 → **DONE**。

## 修改的 Dockerfile 内容

仅 1 行（第 41 行）发生变更：

- 原：
  ```
  RUN curl -fSL -o minio https://dl.min.io/server/minio/release/linux-$TARGETARCH/minio && \
  ```
- 改为：
  ```
  RUN curl -fSL -o minio https://github.com/minio/minio/releases/download/RELEASE.2025-09-07T16-13-09Z/minio.linux-$TARGETARCH.RELEASE.2025-09-07T16-13-09Z && \
  ```

其余 `FROM/ARG/ENV/WORKDIR/COPY` 及注释、builder 阶段全部原样保留。修复使用 `$TARGETARCH` 动态选择 `amd64`/`arm64` 资产，**架构中立**，不破坏 arm64。

## 验证结果

- 失败指令已在容器中复现（HTTP 410 / exit 22）并修复（`exit 0`，MinIO 版本正确）。
- stage-1 全部 RUN（yum 安装、etcd、minio）在容器中逐条执行均成功。
- 针对改动指令的 `docker build`（BuildKit）局部构建成功。
- **未完成完整 `docker build` 从零自检**：完整构建需先跑 builder 阶段（rustup + conan + milvus C++/Go 源码编译），耗时远超本次预算；builder 阶段未改动，故未重跑。改动层已通过上述定向验证。
- 产物已写入 `derived_file`。
