
#0 building with "default" instance using docker driver

#1 [internal] load build definition from Dockerfile
#1 transferring dockerfile: 2.04kB done
#1 DONE 0.0s

#2 [internal] load metadata for docker.io/openeuler/openeuler:24.03-lts-sp4
#2 DONE 0.0s

#3 [internal] load .dockerignore
#3 transferring context: 2B done
#3 DONE 0.0s

#4 [stage-1 1/9] FROM docker.io/openeuler/openeuler:24.03-lts-sp4
#4 DONE 0.0s

#5 [stage-1 2/9] RUN yum install -y libatomic openblas-devel libomp libstdc++ &&     yum clean all
#5 CACHED

#6 [stage-1 4/9] RUN curl -fSL -o minio https://github.com/minio/minio/releases/download/RELEASE.2025-09-07T16-13-09Z/minio.linux-arm64.RELEASE.2025-09-07T16-13-09Z &&     chmod +x ./minio &&     mv ./minio /usr/bin/
#6 CACHED

#7 [stage-1 3/9] RUN curl -fSL -o etcd-v3.5.0-linux-arm64.tar.gz https://github.com/etcd-io/etcd/releases/download/v3.5.0/etcd-v3.5.0-linux-arm64.tar.gz &&     tar zxvf etcd-v3.5.0-linux-arm64.tar.gz &&     cp -r etcd-v3.5.0-linux-arm64 /usr/local/etcd &&     rm -rf etcd-v3.5.0-linux-arm64.tar.gz etcd-v3.5.0-linux-arm64
#7 CACHED

#8 [stage-1 5/9] WORKDIR /milvus
#8 CACHED

#9 [builder 2/4] RUN yum install -y         sudo vim wget gcc g++ cmake make git which         gfortran zip unzip libatomic texinfo numa* ninja* libstdc* pkg-config libuuid-devel         python3-pip openblas-devel libaio perl-IPC-Cmd libasan libomp hdf5 hdf5-devel &&     yum clean all &&     wget -O go.tar.gz https://golang.google.cn/dl/go1.24.2.linux-arm64.tar.gz &&     tar -xvf go.tar.gz -C /usr/local &&     rm -rf go.tar.gz
#9 CACHED

#10 [builder 3/4] RUN curl https://sh.rustup.rs -sSf | sh -s -- --default-toolchain=1.73 -y &&     pip install conan==1.61.0
#10 CACHED

#11 [builder 4/4] RUN git clone -b v3.0.2 https://github.com/milvus-io/milvus.git &&     cd milvus/ &&     ./scripts/install_deps.sh &&     CXXFLAGS="-I/usr/include/openblas" make build-cpp &&     make build-go
#11 0.382 Cloning into 'milvus'...
#11 35.18 Note: switching to '3c4448a2aee506ccd14851e742aba85f1c83188a'.
#11 35.18 
#11 35.18 You are in 'detached HEAD' state. You can look around, make experimental
#11 35.18 changes and commit them, and you can discard any commits you make in this
#11 35.18 state without impacting any branches by switching back to a branch.
#11 35.18 
#11 35.18 If you want to create a new branch to retain commits you create, you may
#11 35.18 do so (now or later) by using -c with the switch command. Example:
#11 35.18 
#11 35.18   git switch -c <new-branch-name>
#11 35.18 
#11 35.18 Or undo this operation with:
#11 35.18 
#11 35.18   git switch -
#11 35.18 
#11 35.18 Turn off this advice by setting config variable advice.detachedHead to false
#11 35.18 
#11 36.04 [0;32m[INFO][0m Milvus Development Dependencies Installer
#11 36.04 [0;32m[INFO][0m ==========================================
#11 36.07 go: downloading go1.26.6 (linux/arm64)
#11 46.17 [0;32m[INFO][0m Go version: 1.26
#11 46.17 [0;31m[ERROR][0m Unsupported Linux distribution: openEuler
#11 46.17 [0;32m[INFO][0m Supported distributions: Ubuntu, Rocky Linux, Amazon Linux, CentOS
#11 ERROR: process "/bin/sh -c git clone -b v${VERSION} https://github.com/milvus-io/milvus.git &&     cd milvus/ &&     ./scripts/install_deps.sh &&     CXXFLAGS=\"-I/usr/include/openblas\" make build-cpp &&     make build-go" did not complete successfully: exit code: 1
------
 > [builder 4/4] RUN git clone -b v3.0.2 https://github.com/milvus-io/milvus.git &&     cd milvus/ &&     ./scripts/install_deps.sh &&     CXXFLAGS="-I/usr/include/openblas" make build-cpp &&     make build-go:
35.18   git switch -
35.18 
35.18 Turn off this advice by setting config variable advice.detachedHead to false
35.18 
36.04 [0;32m[INFO][0m Milvus Development Dependencies Installer
36.04 [0;32m[INFO][0m ==========================================
36.07 go: downloading go1.26.6 (linux/arm64)
46.17 [0;32m[INFO][0m Go version: 1.26
46.17 [0;31m[ERROR][0m Unsupported Linux distribution: openEuler
46.17 [0;32m[INFO][0m Supported distributions: Ubuntu, Rocky Linux, Amazon Linux, CentOS
------
Dockerfile:22
--------------------
  21 |     
  22 | >>> RUN git clone -b v${VERSION} https://github.com/milvus-io/milvus.git && \
  23 | >>>     cd milvus/ && \
  24 | >>>     ./scripts/install_deps.sh && \
  25 | >>>     CXXFLAGS="-I/usr/include/openblas" make build-cpp && \
  26 | >>>     make build-go
  27 |     
--------------------
ERROR: failed to solve: process "/bin/sh -c git clone -b v${VERSION} https://github.com/milvus-io/milvus.git &&     cd milvus/ &&     ./scripts/install_deps.sh &&     CXXFLAGS=\"-I/usr/include/openblas\" make build-cpp &&     make build-go" did not complete successfully: exit code: 1
