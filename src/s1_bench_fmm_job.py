"""
FMM 일괄 매칭 (bike-fmm 컨테이너 안에서 실행한다. fmm과 표준 라이브러리만 쓴다)

입력 (호스트가 /work/fmm 에 준비)
  edges.shp     도로망 (id, source, target; EPSG:5179 미터 좌표)
  traces.csv    id;wkt  (LINESTRING, EPSG:5179)  -- id = "{trip_id}|{resample_s}"
  params.json   {"k", "radius", "gps_error", "ubodt_delta"}  -- config.py 값을 호스트가 넘긴다
출력
  ubodt.txt     최단경로 사전계산표 (있으면 재사용)
  result.csv    id;ok;wkt;runtime_s;error

실행 (호스트에서)
  docker run --rm -v <S1B_DIR>:/work bike-fmm python3 /work/s1_bench_fmm_job.py
"""
import csv
import json
import os
import sys
import time

import fmm

W = "/work/fmm"


def main():
    p = json.load(open(f"{W}/params.json"))
    network = fmm.Network(f"{W}/edges.shp", "id", "source", "target")
    graph = fmm.NetworkGraph(network)
    ubodt_path = f"{W}/ubodt.txt"
    if not os.path.exists(ubodt_path):
        t = time.time()
        fmm.UBODTGenAlgorithm(network, graph).generate_ubodt(ubodt_path, p["ubodt_delta"], binary=False, use_omp=True)
        print(f"UBODT 생성 {time.time() - t:.1f}초", flush=True)
    ubodt = fmm.UBODT.read_ubodt_csv(ubodt_path)
    model = fmm.FastMapMatch(network, graph, ubodt)
    cfg = fmm.FastMapMatchConfig(p["k"], p["radius"], p["gps_error"])

    csv.field_size_limit(sys.maxsize)
    with open(f"{W}/traces.csv") as fi, open(f"{W}/result.csv", "w", newline="") as fo:
        out = csv.writer(fo, delimiter=";")
        out.writerow(["id", "ok", "wkt", "runtime_s", "error"])
        for row in csv.DictReader(fi, delimiter=";"):
            t = time.perf_counter()
            try:
                r = model.match_wkt(row["wkt"], cfg)
                wkt = r.mgeom.export_wkt()
                ok = len(list(r.cpath)) > 0
                out.writerow([row["id"], int(ok), wkt if ok else "", round(time.perf_counter() - t, 4), ""])
            except Exception as e:  # 한 통행 실패가 전체를 멈추지 않도록
                out.writerow([row["id"], 0, "", round(time.perf_counter() - t, 4), str(e)[:200]])
    print("FMM 완료", flush=True)


if __name__ == "__main__":
    main()
