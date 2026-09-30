# CI 失败分析报告

## 基本信息
- PR: #4732 — 【自动升级】ceph容器镜像升级至21.3.0版本.
- 失败类型: `build-error`
- 置信度: 高
- 知识库匹配: 模式10（缺少构建依赖）
- 新模式标题: (不适用)
- 新模式症状关键词: (不适用)

## 根因分析

### 直接错误
```
#12 304.9 -- Found libnbd: /usr/local/include (Required is at least version "1.0")
#12 314.7 -- Could NOT find Curses (missing: CURSES_LIBRARY CURSES_INCLUDE_PATH)
#12 314.7 -- Found nl: /usr/lib64/libnl-3.so
#12 314.7 -- Checking for module 'libcap-ng'
#12 314.7 --   Found libcap-ng, version 0.8.3
#12 314.7 CMake Error at /usr/share/cmake/Modules/FindPackageHandleStandardArgs.cmake:233 (message):
#12 314.7   Could NOT find Protobuf (missing: Protobuf_LIBRARIES Protobuf_INCLUDE_DIR)
#12 314.7 Call Stack (most recent call first):
#12 314.7   /usr/share/cmake/Modules/FindPackageHandleStandardArgs.cmake:603 (_FPHSA_FAILURE_MESSAGE)
#12 314.7   /usr/share/cmake/Modules/FindProtobuf.cmake:772 (FIND_PACKAGE_HANDLE_STANDARD_ARGS)
#12 314.7   src/CMakeLists.txt:1029 (find_package)
#12 314.8 -- Configuring incomplete, errors occurred!
#12 314.8 + exit 1
#12 ERROR: process "/bin/sh -c git clone -b v${VERSION} ... && ./do_cmake.sh -DCMAKE_BUILD_TYPE=Release -DWITH_TESTS=OFF ... did not complete successfully: exit code: 1
```

### 根因定位
- 失败位置: `Storage/ceph/21.3.0/24.03-lts-sp4/Dockerfile:40`（`./do_cmake.sh` 的 cmake 配置阶段），实际缺失判定发生在 ceph 源码 `src/CMakeLists.txt:1029` 的 `find_package(Protobuf)`
- 失败原因: Dockerfile 第一阶段 `dnf install` 的依赖清单中**没有安装 protobuf 相关开发包**，导致 cmake 在配置阶段找不到 Protobuf（`Protobuf_LIBRARIES`、`Protobuf_INCLUDE_DIR` 均缺失），`do_cmake.sh` 返回 exit 1，构建中断。尾部的 `Finished: FAILURE` 表明该失败是非零退出（非误报成功）。

### 与 PR 变更的关联
强关联。该 PR 新增了 `Storage/ceph/21.3.0/24.03-lts-sp4/Dockerfile`，其 `dnf install -y ...` 列表中包含大量构建依赖（如 `thrift-devel`、`lua-devel`、`libcap-ng-devel` 等），但遗漏了 ceph 21.3.0 编译必需的 protobuf 开发包，因而构建在 cmake 配置阶段直接失败。日志中的 `-- Found thrift ...`、`-- Found libcap-ng ...` 等恰好说明其余依赖已装、唯独 protobuf 缺失。

## 修复方向

### 方向 1（置信度: 高）
在 Dockerfile 第一阶段的 `dnf install -y` 依赖清单中补充 protobuf 相关开发/编译包（openEuler 中为 `protobuf-devel`、`protobuf-compiler`，以及 ceph mgr/Python 侧可能需要的 `python3-protobuf`），使 cmake 的 `find_package(Protobuf)` 能定位到头文件与库。

### 方向 2（置信度: 中）
日志在 cmake 报错前出现 `#12 314.3 bash: line 1: jq: command not found`，`jq` 亦未在依赖清单中。虽非本次致命错误，但 ceph 构建/运行脚本可能依赖它，建议一并补装 `jq`，避免后续阶段出现新的失败。

## 需要进一步确认的点
- ceph 21.3.0 在 openEuler 24.03-LTS-SP4 上对 protobuf 的最低版本要求，以及该仓库中 `protobuf-devel`/`protobuf-compiler`/`python3-protobuf` 的实际可用版本是否满足（若版本过低仍可能失败）。
- 是否存在其它被 `find_package` 静默跳过、后续 ninja 编译阶段才暴露的缺失依赖（当前日志仅推进到 cmake 配置阶段，未进入实际编译）。

## 修复验证要求
本修复不涉及对第三方/上游源文件的正则 patch，无需执行正则匹配验证；建议 code-fixer 在提交后确认 cmake 配置阶段能打印出 `Found Protobuf: ...` 且 `do_cmake.sh` 不再以 exit 1 结束。
