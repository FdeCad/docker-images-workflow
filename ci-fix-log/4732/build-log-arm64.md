# 构建修复日志

- PR: #4732 【自动升级】ceph容器镜像升级至21.3.0版本.
- Dockerfile: `Storage/ceph/21.3.0/24.03-lts-sp4/Dockerfile`
- 架构: arm64；基础镜像: `openeuler/openeuler:24.03-lts-sp4`
- 容器: `ci-fix-4732-arm64`
- 方法: 在活动容器里从 `dnf update` 起逐条重放 Dockerfile，失败即修、修完即重试，最后把跑通的命令序列反推为 Dockerfile。

## 修复摘要

该 Dockerfile 缺失 ceph 21.3.0 编译所需的依赖，并在 openEuler 环境下触发两个构建脚本兼容性问题。共修 4 处：补齐 `protobuf`/`grpc` 等构建依赖；把 dashboard 前端构建依赖的 `nodeenv` 约束到 1.9.1；为 openEuler 不提供 cmake 配置的 `grpc-devel` 补一个 `gRPCConfig.cmake`（消除 OpenTelemetry 与系统 abseil 的 inline namespace 冲突）；补装 `wheel`。最终容器内 `ninja` 与 `ninja install` 均 exit 0。

## 构建命令序列

以下命令在容器 `ci-fix-4732-arm64` 中按顺序实际执行。

1. 环境准备（对应 Dockerfile RUN #1）
   - `dnf update -y` → exit 0
   - `dnf install -y <原始依赖清单>` → exit 0
   - **修复命令** `dnf install -y protobuf-devel protobuf-compiler python3-protobuf jq` → exit 0
     - 原因：CI 首个失败点 `src/CMakeLists.txt:1029 find_package(Protobuf REQUIRED)` 报
       `Could NOT find Protobuf (missing: Protobuf_LIBRARIES Protobuf_INCLUDE_DIR)`。
       安装后 cmake 打印 `Found Protobuf: /usr/lib64/libprotobuf.so (found version "4.25.1")`。
     - `jq` 一并补装（方向2，避免后续脚本 command not found）。
   - **修复命令** `dnf install -y grpc-devel grpc-plugins` → exit 0
     - 原因：protobuf 修好后下一阻塞为 `src/CMakeLists.txt:1055 pkg_check_modules(GRPCPP REQUIRED grpc++)`，
       报 `Package 'grpc++', required by 'virtual:world', not found`。
       安装后 `pkg-config --modversion grpc++` = 1.60.0。

2. Python 依赖（对应 Dockerfile RUN: `pip install cython prettytable`）
   - `python3 -m pip install --upgrade pip`、`python3 -m pip install cython prettytable` → exit 0
   - **修复命令** 追加安装 `wheel`（`python3 -m pip install wheel`）。
     - 原因：`ninja install` 阶段编译 `src/pybind/rados` 时
       `error: invalid command 'bdist_wheel'` → `error: metadata-generation-failed`
       → `src/pybind/rados/cmake_install.cmake:78: Failed to build and install cython_rados python module`。
       安装 wheel 后 install 成功。

3. libnbd（对应 Dockerfile RUN）
   - `git clone https://gitlab.com/nbdkit/libnbd.git && autoreconf -fi && ./configure && make -j$(nproc) && make install` → exit 0

4. 约束 nodeenv（新增，对应新加的 RUN/ENV）
   - `echo "nodeenv==1.9.1" > /opt/pip-constraints.txt`
   - `ENV PIP_CONSTRAINT=/opt/pip-constraints.txt`
     - 原因：`ninja` 在 dashboard 前端目标失败：ceph 的
       `frontend/CMakeLists.txt:77` 执行 `chown -R ... node-env/src`，
       而 `node-env/bin/pip install nodeenv` 默认装到最新的 nodeenv 1.11.0，
       其 `Config.clean_src=True`，安装完会 `rmtree(src_dir)`，导致
       `chown: cannot access '.../node-env/src': No such file or directory`。
       经查 nodeenv 1.9.1 的 `--clean-src` 默认 False，会保留 `src`。
       用 `PIP_CONSTRAINT` 在不动 ceph 源码的前提下把版本钉到 1.9.1
       （容器内验证：安装 1.9.1 后 `node-env/src` 存在，chown 通过）。

5. 生成 gRPC cmake 配置（新增，对应新加的 RUN）
   - 在 `/usr/lib64/cmake/gRPC/gRPCConfig.cmake` 写一个最小配置，
     用 pkg-config 定义 `gRPC::grpc++`、`gRPC::grpc_cpp_plugin`、`gRPC::grpc++_reflection`。
     - 原因：`ninja` 在 `ceph-nvmeof-monitor-client` 编译失败：
       `reference to 'base_internal' is ambiguous` / 候选为
       `absl::otel_v1::base_internal`（OpenTelemetry 内置 absl 快照）与
       `absl::lts_20230802::base_internal`（系统 grpc 带的 abseil）。
       ceph 源码逻辑为：`find_package(gRPC CONFIG)` 成功才置 `_HAVE_ABSEIL=ON`，
       从而给 `ceph-nvmeof-monitor-client`/`ceph-mon` 加 `-DHAVE_ABSEIL`，
       让 OpenTelemetry 改用系统 absl（避免两个 inline namespace 冲突）。
       但 openEuler 的 `grpc-devel` 只提供 pkg-config、不提供 `gRPCConfig.cmake`，
       于是走 pkg-config 回退分支 `_HAVE_ABSEIL=OFF`，冲突发生。
       补上 config 后 cmake 打印 `Using gRPC 1.60.0`，`_HAVE_ABSEIL=ON`。
     - 迭代：首版 config 只链 `libgrpc++.so`，链接报
       `undefined reference to 'grpc_call_start_batch' ... libgrpc.so.37: DSO missing`;
       改为经 `PkgConfig::_grpcpp`（即 `grpc++.pc` 的完整 `Requires`，含 absl/grpc/protobuf）
       链接后，`ceph-nvmeof-monitor-client` 链接通过。
     - 另用真实 `docker build` 单独构建该 RUN，确认 Dockerfile 中 printf 展开出的文件与验证用的 shim 语义一致，并回灌容器重跑 cmake/ninja 通过。

6. do_cmake / 编译 / 安装（对应 Dockerfile RUN #3）
   - `git clone -b v21.3.0 --recursive --depth 1 https://github.com/ceph/ceph.git` → exit 0
   - `./do_cmake.sh -DCMAKE_BUILD_TYPE=Release -DWITH_TESTS=OFF` → exit 0
   - `ninja -j$(nproc)` → exit 0（日志 `/tmp/ninja3.log`，`ninja_exit=0`）
   - `ninja install` → exit 0（日志 `/tmp/ninja_install2.log`，`install_exit=0`）

## 修改的 Dockerfile 内容

相对原始 Dockerfile 的改动（其余行、结构、注释、顺序保持不变）：

1. 第 1 个 `dnf install` 清单末尾追加：
   ```
   protobuf protobuf-devel protobuf-compiler python3-protobuf \
   grpc grpc-devel grpc-plugins jq \
   ```
2. 新增（在 pip RUN 之前）：
   ```
   RUN echo "nodeenv==1.9.1" > /opt/pip-constraints.txt
   ENV PIP_CONSTRAINT=/opt/pip-constraints.txt
   ```
3. 新增生成 `/usr/lib64/cmake/gRPC/gRPCConfig.cmake` 的 RUN（用 printf 写文件，定义 gRPC 的 imported targets）。
4. pip 安装行由 `cython prettytable` 改为 `cython prettytable wheel`。
5. `do_cmake.sh`、`ninja`、`ninja install`、`ENV LD_LIBRARY_PATH`、`COPY`、`WORKDIR`、`ENTRYPOINT` 均未改动。

## 验证结果

- 容器内端到端重放：`dnf`、`pip`、libnbd、ceph clone、`do_cmake.sh`、`ninja`、`ninja install` 全部 exit 0；受保护的两条构建脚本（ceph 的 `frontend/CMakeLists.txt`、`NVMeofGwMonitorClient`）均通过。
- `docker build` 从零端到端自检：**未完成**。原因是 ceph 全量源码编译耗时很长（本机 16 核下 `ninja` 约 1.5 小时、`ninja install` 约 20 分钟），剩余预算不足以再跑一次完整从零构建。作为替代，已用真实 `docker build` 单独验证了新增的 `gRPCConfig.cmake` RUN（生成的 cmake 文件在容器内重新配置/链接通过），且其余每个 RUN 都已在活动容器里逐条实跑验证。
- 本轮非复验回修（`arm64_verify_failure` 为空），故针对的是 `ci_analysis` 指出的 protobuf 失败点及其后续尚未被 CI 验证的指令；后续所有新阻塞均已在容器中定位并修复。

## 备注（供确定性 verify-arm 阶段参考）

- 所有修复均为架构中立的包安装与构建 shim，不涉及 amd64/arm64 分支，不会破坏另一架构。
- 若 verify 环境使用 BuildKit 之外的老式 Docker，`printf` 写文件的方式不依赖 heredoc，兼容性良好。
