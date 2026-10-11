# Wine for Arm Compiler 5's Windows tools, on x86 Debian (Rosetta on Apple silicon).
FROM debian:bookworm-slim
RUN dpkg --add-architecture i386 && apt-get update && \
    apt-get install -y --no-install-recommends wine wine32 wine64 ca-certificates && \
    rm -rf /var/lib/apt/lists/*
ENV WINEDEBUG=-all WINEPREFIX=/wine
RUN wineboot -i >/dev/null 2>&1 || true
WORKDIR /work
