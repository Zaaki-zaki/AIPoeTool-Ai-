# Silnik obliczeń AIPoeTool: headless Path of Building (LuaJIT) z serwerem JSON na stdin/stdout.
#   docker build -t aipoetool-pob pob
#   docker run -i --rm aipoetool-pob      (to samo robi app.calc.PobEngine)
FROM ubuntu:24.04 AS build
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates build-essential zlib1g-dev && rm -rf /var/lib/apt/lists/*
# LuaJIT w wersji używanej przez PoB (ten sam commit co w ich Dockerfile)
RUN git clone https://github.com/LuaJIT/LuaJIT.git /opt/LuaJIT \
 && cd /opt/LuaJIT && git checkout 2460b3ff93a1c955de3d62cfc825de7d68dc272e && make -j4 && make install PREFIX=/opt/luajit
# moduł lua-utf8 wymagany przez PoB
RUN git clone --depth 1 https://github.com/starwing/luautf8.git /opt/luautf8 \
 && gcc -O2 -shared -fPIC -I/opt/luajit/include/luajit-2.1 /opt/luautf8/lutf8lib.c -o /opt/lua-utf8.so
# PoB w przetestowanej wersji (ta sama co w scripts/update_gamedata.ps1)
ARG POB_COMMIT=16de4b82d57f1c0de6eb40f37143c32d4da36a02
RUN git init /opt/pob && cd /opt/pob \
 && git remote add origin https://github.com/PathOfBuildingCommunity/PathOfBuilding.git \
 && git fetch --depth 1 origin ${POB_COMMIT} && git checkout FETCH_HEAD \
 && rm -rf .git

FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends zlib1g && rm -rf /var/lib/apt/lists/*
COPY --from=build /opt/luajit /opt/luajit
COPY --from=build /opt/lua-utf8.so /opt/lua-utf8.so
COPY --from=build /opt/pob /opt/pob
COPY server.lua zlib_ffi.lua /opt/pobserver/
ENV LUA_CPATH="/opt/?.so;;" \
    LUA_PATH="/opt/pob/runtime/lua/?.lua;/opt/pob/runtime/lua/?/init.lua;;" \
    POB_ZLIB_LUA=/opt/pobserver/zlib_ffi.lua
WORKDIR /opt/pob/src
ENTRYPOINT ["/opt/luajit/bin/luajit", "/opt/pobserver/server.lua"]
