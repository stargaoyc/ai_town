FROM pgvector/pgvector:pg17

# 从官方 release 下载预编译扩展（v1.7.0，仅 x86_64），避免依赖本地未追踪的源码包；
# 校验和来自官方 SHA256SUMS，解压后对全部文件复核
ADD https://github.com/fboulnois/pg_uuidv7/releases/download/v1.7.0/pg_uuidv7.tar.gz /tmp/pg_uuidv7.tar.gz
ADD https://github.com/fboulnois/pg_uuidv7/releases/download/v1.7.0/SHA256SUMS /tmp/SHA256SUMS

RUN set -eux; \
    cd /tmp; \
    tar -xzf pg_uuidv7.tar.gz; \
    sha256sum -c SHA256SUMS --ignore-missing; \
    PG_MAJOR=$(pg_config --version | sed 's/^.* \([0-9]\{1,\}\).*$/\1/'); \
    cp "$PG_MAJOR/pg_uuidv7.so" "$(pg_config --pkglibdir)"; \
    cp pg_uuidv7--1.7.sql pg_uuidv7.control "$(pg_config --sharedir)/extension"; \
    rm -rf /tmp/pg_uuidv7.tar.gz /tmp/SHA256SUMS "/tmp/$PG_MAJOR"