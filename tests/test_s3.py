"""
3단계 격자 후보지 검증 (합성 데이터)

동서 직선 경로 하나(길이 2km)와 그 위로 1km 떨어진 짧은 경로 하나를 두고,
격자 중심 위치, 반경 필터, 연구 지역 자르기, 기존 대여소 플래그, 덮임 계산을 확인한다.

실행: python tests/test_s3.py
"""
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import shapely

TMP = Path(tempfile.mkdtemp(prefix="s3_test_"))
os.environ["BIKE_BASE_DIR"] = str(TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import s3_candidates as S3  # noqa: E402

X0, Y0 = 950_000.0, 1_950_000.0     # 격자 배수 위에 둬서 손계산이 쉽도록


def main():
    fails = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)

    main_route = shapely.LineString([(X0, Y0), (X0 + 2000, Y0)])
    side_route = shapely.LineString([(X0, Y0 + 1000), (X0 + 200, Y0 + 1000)])
    geoms = np.array([main_route, side_route], dtype=object)
    weights = np.array([5, 1])

    # --- 격자 중심: 200m 간격, 셀 중심은 100m 어긋난 자리 ---
    x, y = S3.grid_centers(geoms, 200, 500)
    check(np.allclose((x - X0 - 100) % 200, 0) and np.allclose((y - Y0 - 100) % 200, 0), "셀 중심이 격자 가운데가 아님")
    check(x.min() < X0 - 400 and x.max() > X0 + 2400 and y.max() > Y0 + 1400, "사각형 여유 500m가 반영되지 않음")

    # --- 반경 필터: 반경 150m면 주 경로 양옆 1줄씩(중심 y = ±100)만 남는다 ---
    c, acc, log = S3.make_candidates(geoms, weights, grid_m=200, radius_m=150, min_routes=1, margin_m=500)
    main_c = c[np.abs(c.y - Y0) < 500]
    check(set(np.round(main_c.y - Y0).astype(int)) == {-100, 100}, f"주 경로 주변 행 {sorted(set(main_c.y - Y0))}")
    # 경로 x 범위 [0, 2000] + 반경 안: 중심 x = -100 ~ 2100 (x=-100에서 경로 끝까지 거리 √(100²+100²)=141 <= 150)
    check(len(main_c) == 2 * 12, f"주 경로 주변 후보지 {len(main_c)} (기대 24)")
    check((main_c.n_routes == 1).all() and (main_c.n_trips == 5).all(), "반경 내 경로 수/가중치 오류")
    check(len(c) == len(main_c) + 2 * 3, f"전체 후보지 {len(c)} (짧은 경로 주변 6개 기대)")
    check(log["cover_route_any"] == 1.0 and log["cover_route_ends"] == 1.0, f"덮임 {log}")
    # 접근점 표: 주 경로 24행, 짧은 경로 6행. 거리는 100m(옆줄) 또는 141m(끝 대각선)
    per = acc.groupby("route_id").size().to_dict()
    check(per == {0: 24, 1: 6} and log["n_access_rows"] == 30, f"접근점 표 경로별 행 {per}")
    check(set(np.round(acc.dist_m).astype(int)) == {100, 141}, f"접근점 거리 {sorted(set(np.round(acc.dist_m)))}")
    check(log["cands_per_route"]["max"] == 24 and log["n_cands_unused"] == 0, f"접근점 통계 {log['cands_per_route']}")

    # --- 반경 100m < 격자/√2: 경로 끝(x=0)에서 가장 가까운 중심이 141m라 시종점 덮임이 깨진다 ---
    _, _, log100 = S3.make_candidates(geoms, weights, grid_m=200, radius_m=100, min_routes=1, margin_m=500)
    check(log100["cover_route_ends"] == 0.0 and log100["cover_route_any"] == 1.0, f"반경 100 덮임 {log100}")

    # --- 최소 경로 수 2: 두 경로가 1km 떨어져 있어 공통 후보지가 없음 ---
    c2, _, _ = S3.make_candidates(geoms, weights, grid_m=200, radius_m=150, min_routes=2, margin_m=500)
    check(len(c2) == 0, f"최소 경로 2인데 후보지 {len(c2)}")

    # --- 연구 지역 자르기: x < X0+1000만 지역 ---
    region = shapely.box(X0 - 1000, Y0 - 1000, X0 + 1000, Y0 + 2000)
    cr, _, logr = S3.make_candidates(geoms, weights, grid_m=200, radius_m=150, min_routes=1, margin_m=500, region=region)
    check((cr.x < X0 + 1000).all() and len(cr) == 2 * 6 + 6, f"지역 자르기 후 {len(cr)} (기대 18)")

    # --- 기존 대여소: 반경 필터와 무관하게 포함, 플래그 ---
    ex = pd.DataFrame({"station_id": ["S1", "S2"], "x": [X0 + 1000, X0 + 5000], "y": [Y0 + 50, Y0 + 5000]})
    ce, _, loge = S3.make_candidates(geoms, weights, grid_m=200, radius_m=150, min_routes=1, margin_m=500, existing=ex)
    e = ce[ce.is_existing].set_index("station_id")
    check(len(e) == 2 and e.n_routes["S1"] == 1 and e.n_routes["S2"] == 0, f"기존 대여소 {e}")
    check(ce.cand_id.is_unique and len(ce) == len(c) + 2, "cand_id 중복 또는 개수 오류")

    print(f"반경 150: 후보지 {log['n_candidates']}, 덮임 {log['cover_route_any']}/{log['cover_route_ends']}")
    print(f"반경 100: 후보지 {log100['n_candidates']}, 덮임 {log100['cover_route_any']}/{log100['cover_route_ends']}")
    print(f"지역 자르기: 셀 {logr['n_cells_bbox']} -> 지역 안 {logr['n_cells_in_region']} -> 후보지 {logr['n_candidates']}")
    if fails:
        print("\n실패 항목:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("\n모든 검증 통과")


if __name__ == "__main__":
    main()
