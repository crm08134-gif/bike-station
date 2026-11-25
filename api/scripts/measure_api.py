"""
API 응답 시간 측정 (표준 라이브러리만 사용).

엔드포인트마다 워밍업 WARMUP회 뒤 N회 호출해 클라이언트 측 왕복 시간(ms)의 중앙값·p95·최대와 응답 크기를 출력한다.
서버 측 시간은 앱 로그(api.timing)에 요청마다 남는다.

실행 (API가 떠 있어야 한다)
  python api/scripts/measure_api.py --base http://localhost:8080 --n 50
"""
import argparse
import statistics
import time
import urllib.request
from urllib.parse import quote

ABS = "absolute-new-w300-journey_gap15m"


def endpoints(cand_id):
    return [
        ("시나리오 목록", "/api/scenarios"),
        ("커버리지 곡선", f"/api/coverage-curve?scenario={ABS}-p100"),
        ("대여소 p=100 전체", f"/api/stations?scenario={ABS}-p100"),
        ("대여소 p=1000 전체 (506개)", f"/api/stations?scenario={ABS}-p1000"),
        ("대여소 p=1000 bbox (서울 중심부)", f"/api/stations?scenario={ABS}-p1000&bbox=126.95,37.48,127.05,37.58"),
        (f"대여소 {cand_id} 반경 300m", f"/api/stations/{cand_id}/coverage"),
        ("구별 커버율 (송파구, p=100)", f"/api/districts/{quote('송파구')}/stats?scenario={ABS}-p100"),
        ("통행 선 (p=100)", f"/api/routes?scenario={ABS}-p100"),
        ("구 경계", "/api/districts"),
    ]


def call(url):
    t = time.perf_counter()
    with urllib.request.urlopen(url) as r:
        body = r.read()
        status = r.status
    return (time.perf_counter() - t) * 1000, status, len(body)


def main(base, n, warmup, cand_id):
    print(f"{'엔드포인트':<34} {'중앙값':>8} {'p95':>8} {'최대':>8} {'크기':>9}")
    for name, path in endpoints(cand_id):
        url = base + path
        for _ in range(warmup):
            call(url)
        ms, sizes = [], set()
        for _ in range(n):
            t, status, size = call(url)
            if status != 200:
                raise RuntimeError(f"{url} -> {status}")
            ms.append(t)
            sizes.add(size)
        ms.sort()
        p95 = ms[min(len(ms) - 1, int(round(0.95 * (len(ms) - 1))))]
        print(f"{name:<34} {statistics.median(ms):>6.1f}ms {p95:>6.1f}ms {ms[-1]:>6.1f}ms {max(sizes):>7,}B")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8080")
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--warmup", type=int, default=5)
    ap.add_argument("--cand", type=int, default=1, help="반경 조회할 후보지 id")
    a = ap.parse_args()
    main(a.base, a.n, a.warmup, a.cand)
