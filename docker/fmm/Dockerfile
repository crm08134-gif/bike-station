# FMM (Fast Map Matching, cyang-kth/fmm) 실행 환경
# 공식 docker/Dockerfile은 ubuntu:16.04 + python2 기반이라 ubuntu:22.04 + python3로 새로 작성했다.
# FMM의 python/CMakeLists.txt가 `python` 명령 + distutils로 설치 경로를 찾으므로
# python-is-python3, python3-distutils가 필요하다 (없으면 cmake 단계에서 DESTINATION 오류).
# 빌드: docker build -t bike-fmm docker/fmm
# 실행: docker run --rm -v <작업폴더>:/work bike-fmm python3 /work/<스크립트>.py
FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git ca-certificates \
        libboost-dev libboost-serialization-dev libssl-dev libbz2-dev libexpat1-dev \
        gdal-bin libgdal-dev swig python3-dev python3-pip \
        python-is-python3 python3-distutils \
    && rm -rf /var/lib/apt/lists/*
# 재현성을 위해 커밋을 고정한다 (master 최신 커밋, 2024-04-22)
ARG FMM_COMMIT=19ef34e1f57ff2f2484231aa0d01dfffea986ec1
RUN git clone https://github.com/cyang-kth/fmm.git /fmm && cd /fmm && git checkout ${FMM_COMMIT}
RUN mkdir -p /fmm/build && cd /fmm/build && cmake .. && make -j4 install
# make install이 파이썬 바인딩을 site-packages에 넣지 않는 경우를 대비해 경로를 잡아 둔다
ENV PYTHONPATH=/fmm/build/python
WORKDIR /work
