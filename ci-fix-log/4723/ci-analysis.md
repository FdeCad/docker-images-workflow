# CI 失败分析报告

## 基本信息
- PR: #4723 — 【自动升级】milvus容器镜像升级至3.0.2版本.
- 失败类型: `build-error`
- 置信度: 高
- 知识库匹配: 新模式
- 新模式标题: MinIO下载源410
- 新模式症状关键词: curl: (22), 410, dl.min.io, minio, exit code: 22

## 根因分析

### 直接错误
```
#10 [stage-1 4/9] RUN curl -fSL -o minio https://dl.min.io/server/minio/release/linux-amd64/minio &&     chmod +x ./minio &&     mv ./minio /usr/bin/
#10 0.065   % Total    % Received % Xferd  Average Speed   Time    Time     Time  Current
#10 0.065                                  Dload  Upload   Total   Spent    Left  Speed
#10 0.065   0     0    0     0     0      0      0 --:--:--  --:--:-- --:--:--     0   0   405    0     0    0      0      0 --:--:-- --:--:-- --:--:--     0
#10 0.582 curl: (22) The requested URL returned error: 410
#10 ERROR: process "/bin/sh -c curl -fSL -o minio https://dl.min.io/server/minio/release/linux-$TARGETARCH/minio &&     chmod +x ./minio &&     mv ./minio /usr/bin/" did not complete successfully: exit code: 22
```

### 根因定位
- 失败位置: `Database/milvus/3.0.2/24.03-lts-sp4/Dockerfile:41`
- 失败原因: 下载 MinIO 服务端二进制的 URL `https://dl.min.io/server/minio/release/linux-${TARGETARCH}/minio` 返回 HTTP 410（Gone），上游已停止在该路径分发 MIT 许可的 MinIO 二进制（MinIO 官方调整了发行策略/仓库布局），导致 `curl -fSL` 以 exit code 22 失败。

### 与 PR 变更的关联
本 PR 新增了 `Database/milvus/3.0.2/24.03-lts-sp4/Dockerfile`（新文件），其中第 38-40 行（解析后对应 Dockerfile:41 的 Docker 步骤）硬编码了上述 MinIO 下载地址。该行由本 PR 首次引入，因此此次失败**由 PR 改动直接触发**。其余更新（README.md、image-info.yml、meta.yml）与失败无关。

## 修复方向

### 方向 1（置信度: 高）
更换 MinIO 二进制的下载来源，改为当前仍可用的官方/镜像分发型式（例如 MinIO 官方新发布的下载端点，或社区镜像站/软件包源），并确保所选 URL 对 `amd64` 与 `arm64` 两种架构均可用。注意原有 `${TARGETARCH}` 变量格式（`amd64`/`arm64`）需与新下载源的架构命名保持一致。

### 方向 2（可选）
若找不到稳定的直接下载源，可考虑移除对 MinIO 同步下载的强依赖：改为在多阶段构建中从官方 MinIO 容器镜像 `COPY` 出 `/usr/bin/minio`（与知识库模式16“RPM 停止发布→改用多阶段 COPY”思路一致），从而摆脱 dl.min.io 路径变更的影响。

## 需要进一步确认的点
- 需确认 MinIO 当前实际可用的二进制下载地址（原 `dl.min.io/server/minio/release/...` 已返回 410），以及该地址在 `amd64`/`arm64` 下是否均提供制品、架构标识是否仍为 `amd64`/`arm64`。
- 需确认所采用的替代方案在 openEuler 24.03-LTS-SP4 运行时阶段是否满足 Milvus 对 MinIO 的版本兼容要求。
- 日志中 `#8`（builder 阶段 yum 安装）仍在进行时 `#10` 已失败并被 CANCELED，属正常的并行构建取消，非根因，无需处理。

## 修复验证要求
无正则 patch 外部源文件场景。code-fixer 在提交前须实际访问或拉取所选 MinIO 下载地址，验证其返回 HTTP 200 且为可执行二进制（非 HTML/错误页），并确认 `${TARGETARCH}` 展开后的架构路径有效，避免再次出现 404/410。
