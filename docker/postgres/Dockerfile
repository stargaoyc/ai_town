FROM pgvector/pgvector:pg17

# 将本地下载的源码包复制进镜像
COPY pg_uuidv7-main.tar.gz /tmp/pg_uuidv7.tar.gz

RUN set -eux; \
    echo "deb http://mirrors.tuna.tsinghua.edu.cn/debian/ bookworm main contrib non-free non-free-firmware" > /etc/apt/sources.list; \
    echo "deb http://mirrors.tuna.tsinghua.edu.cn/debian/ bookworm-updates main contrib non-free non-free-firmware" >> /etc/apt/sources.list; \
    echo "deb http://mirrors.tuna.tsinghua.edu.cn/debian/ bookworm-backports main contrib non-free non-free-firmware" >> /etc/apt/sources.list; \
    echo "deb http://mirrors.tuna.tsinghua.edu.cn/debian-security bookworm-security main contrib non-free non-free-firmware" >> /etc/apt/sources.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates; \
    \
    sed -i 's|http://apt.postgresql.org/pub/repos/apt|https://apt.postgresql.org/pub/repos/apt|g' /etc/apt/sources.list.d/pgdg.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends build-essential postgresql-server-dev-17; \
    \
    mkdir /tmp/pg_uuidv7; \
    tar -xzf /tmp/pg_uuidv7.tar.gz -C /tmp/pg_uuidv7 --strip-components=1; \
    rm /tmp/pg_uuidv7.tar.gz; \
    cd /tmp/pg_uuidv7 && make && make install; \
    \
    cd / && rm -rf /tmp/pg_uuidv7; \
    apt-get purge -y --auto-remove ca-certificates build-essential postgresql-server-dev-17; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*