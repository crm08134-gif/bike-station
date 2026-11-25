"""
4단계 접근점 표 검증 (합성 데이터)

링크 (저장 방향 u -> v, 단위 m, 원점 X0, Y0 기준)
  L0 (0,0)->(100,0)   L1 (200,0)->(100,0) [거꾸로 저장]   L2 (200,0)->(200,100)
  L3 (300,100)->(300,200) [L2와 이어지지 않음: (200,100)~(300,100) 100m를 직선으로 이음]
  L4 (5000,5000)->(5100,5000) [멀리 떨어진 경로]
경로  R0 = [L0, L1, L2, L3] 시점 (0,0)   -> 선 (0,0)(100,0)(200,0)(200,100)(300,100)(300,200), 길이 500
      R1 = [L1] 시점 (200,0)             -> 선 (200,0)(100,0)  (거꾸로 저장된 링크를 시점 쪽에서 시작)
      R2 = [L4]                           -> 반경 안 후보지 없음 (도달 불가)
후보지 (도보 반경 60m)
  c0 (50,50)   R0: a=50,  d=50
  c1 (240,50)  R0: a=250, d=40   (이은 직선까지는 50m라 더 먼 쪽)
  c2 (300,50)  R0: a=400, d=50   (이은 직선 끝 = L3 시작점)
  c3 (1000,1000) 어떤 경로에도 안 걸림
  c4 (150,-30) R0: a=150, d=30 / R1: a=50, d=30

실행: python tests/test_s4.py
"""
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import shapely

TMP = Path(tempfile.mkdtemp(prefix="s4_test_"))
os.environ["BIKE_BASE_DIR"] = str(TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import config as C  # noqa: E402
import route_lines as RL  # noqa: E402
import s4_access as S4  # noqa: E402

X0, Y0 = 950_000.0, 1_950_000.0
WALK = 60


def P(x, y):
    return (X0 + x, Y0 + y)


def main():
    fails = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)

    link_xy = [[P(0, 0), P(100, 0)], [P(200, 0), P(100, 0)], [P(200, 0), P(200, 100)],
               [P(300, 100), P(300, 200)], [P(5000, 5000), P(5100, 5000)]]
    links = pd.DataFrame({"link_id": np.arange(5, dtype=np.int64),
                          "geometry": shapely.to_wkb(shapely.linestrings(link_xy))})
    ends = RL.link_ends(links)

    # --- 선 만들기: 방향, 끊긴 곳 ---
    line, nb, bm = RL.ordered_line([0, 1, 2, 3], ends, np.array(P(0, 0)))
    exp = np.array([P(0, 0), P(100, 0), P(200, 0), P(200, 100), P(300, 100), P(300, 200)])
    check(np.allclose(shapely.get_coordinates(line), exp), f"R0 선 좌표 {shapely.get_coordinates(line) - [X0, Y0]}")
    check(nb == 1 and abs(bm - 100) < 1e-6 and abs(line.length - 500) < 1e-6, f"R0 끊김 {nb}, {bm}, 길이 {line.length}")
    l1, nb1, _ = RL.ordered_line([1], ends, np.array(P(195, 5)))
    check(np.allclose(shapely.get_coordinates(l1), [P(200, 0), P(100, 0)]) and nb1 == 0, "R1: 시점 쪽 노드에서 시작 안 함")
    # 첫 링크 방향은 시점보다 다음 링크와의 공유 노드가 우선 (시점을 반대쪽에 줘도)
    l2, _, _ = RL.ordered_line([1, 0], ends, np.array(P(0, 0)))
    check(np.allclose(shapely.get_coordinates(l2), [P(200, 0), P(100, 0), P(0, 0)]), "첫 링크 방향이 공유 노드를 무시")
    # 같은 링크를 되돌아옴 (U턴): L0 -> L0
    l3, nb3, _ = RL.ordered_line([0, 0], ends, np.array(P(0, 0)))
    check(np.allclose(shapely.get_coordinates(l3), [P(0, 0), P(100, 0), P(0, 0)]) and nb3 == 0, "U턴 선 오류")

    # --- 선 끝 자르기: 시점은 첫 링크에, 종점은 마지막 링크에만 투영 ---
    t, cs, ce = RL.trim_line(line, P(30, 0), P(300, 150))
    check(abs(cs - 30) < 1e-6 and abs(ce - 50) < 1e-6 and abs(t.length - 420) < 1e-6
          and np.allclose(shapely.get_coordinates(t)[[0, -1]], [P(30, 0), P(300, 150)]), f"자르기 {cs}, {ce}, {t.length}")
    # 출발지로 되돌아오는 경로 (L0 왕복): 종점을 선 전체에 투영하면 선 앞부분으로 가서 선이 사라진다
    t3, cs3, ce3 = RL.trim_line(l3, P(30, 0), P(20, 0))
    check(abs(cs3 - 30) < 1e-6 and abs(ce3 - 20) < 1e-6 and abs(t3.length - 150) < 1e-6, f"왕복 자르기 {cs3}, {ce3}, {t3.length}")
    # 시점이 첫 링크 밖(링크 연장선 뒤쪽)이면 투영이 링크 끝에 걸려 자르지 않는다
    _, cs4, _ = RL.trim_line(line, P(-50, 0), P(300, 200))
    check(cs4 == 0, f"링크 밖 시점인데 {cs4}m 자름")

    # --- 접근점 표 ---
    routes = pd.DataFrame({"route_id": np.array([0, 1, 2], dtype=np.int64),
                           "link_seq": [[0, 1, 2, 3], [1], [4]], "weight": [5, 2, 1],
                           "start_x": [X0, X0 + 200, X0 + 5000], "start_y": [Y0, Y0, Y0 + 5000],
                           "end_x": [X0 + 300, X0 + 100, X0 + 5100], "end_y": [Y0 + 200, Y0, Y0 + 5000]})
    cxy = [(50, 50), (240, 50), (300, 50), (1000, 1000), (150, -30)]
    cands = pd.DataFrame({"cand_id": np.arange(5, dtype=np.int64), "x": [X0 + x for x, _ in cxy],
                          "y": [Y0 + y for _, y in cxy], "is_existing": False})
    ld = RL.build_route_lines(routes, ends)
    acc = S4.access_table(ld.geom.to_numpy(), ld.route_id.to_numpy(), routes.weight.to_numpy(), cands, WALK)
    got = {(r.route_id, r.cand_id): (round(r.a, 3), round(r.walk_d, 3)) for r in acc.itertuples()}
    exp_rows = {(0, 0): (50, 50), (0, 4): (150, 30), (0, 1): (250, 40), (0, 2): (400, 50), (1, 4): (50, 30)}
    check(got == exp_rows, f"접근점 표 {got}")
    check(list(acc.columns) == S4.ACCESS_COLUMNS, f"칼럼 {list(acc.columns)}")
    check((acc[acc.route_id == 0].route_len == 500).all() and (acc[acc.route_id == 0].weight == 5).all(), "weight/route_len")
    check(list(acc[acc.route_id == 0].cand_id) == [0, 4, 1, 2], "경로 안 a 순 정렬이 아님")

    st = S4.access_stats(acc, ld, routes, cands, top_n=2)
    check(st["n_rows"] == 5 and st["n_unreached_routes"] == 1 and st["unreached_routes"][0]["route_id"] == 2,
          f"행 수/도달 불가 {st['n_rows']}, {st['unreached_routes']}")
    check(st["cands_per_route"]["max"] == 4 and st["cands_per_route"]["p50"] == 1.0, f"경로당 {st['cands_per_route']}")
    check(st["n_cands_no_route"] == 1 and st["routes_per_cand_hist"]["0"] == 1 and st["routes_per_cand_hist"]["2-4"] == 1,
          f"후보지별 {st['routes_per_cand_hist']}")
    check(st["top_cands"][0]["cand_id"] == 4 and st["top_cands"][0]["weight_sum"] == 7, f"상위 {st['top_cands']}")

    lc = S4.line_checks(ld, routes)
    check(lc["n_reversed_vs_s2"] == 0 and lc["n_breaks"] == 1 and lc["n_routes_repeated_links"] == 0, f"선 점검 {lc}")
    check(lc["n_start_offset_over"] == 0 and lc["n_end_offset_over"] == 0 and lc["offset_over"] == [], f"편차 {lc}")
    check((ld.repeat_ratio == 0).all() and abs(RL.repeat_ratio([0, 0, 1]) - 1 / 3) < 1e-12, "반복 비율")

    # --- 끝점 행: a = 0, a = route_len, 중간 ---
    er = S4.end_rows(pd.DataFrame({"a": [0.0, 100.0, 40.0], "route_len": [100.0, 100.0, 100.0],
                                   "walk_d": [10.0, 20.0, 30.0]}))
    check(er["n_rows_at_start"] == 1 and er["n_rows_at_end"] == 1 and er["walk_d_at_end"]["max"] == 20.0
          and er["walk_d_middle"]["max"] == 30.0, f"끝점 행 {er}")

    # --- 반경 안을 두 번 지나는 경로 (U자): 두 조각 사이 경로상 간격 = 220 - 2 x √(30² - 10²) ---
    u = shapely.LineString([P(0, 0), P(0, 100), P(20, 100), P(20, 0)])
    a1 = pd.DataFrame({"route_id": [9, 9], "cand_id": [9, 8]})
    n_piece, gap = S4.pass_gaps(a1, {9: u}, {9: P(10, 0), 8: P(-15, 50)}, 30)
    check(n_piece.tolist() == [2, 1] and abs(gap[0] - (220 - 2 * np.sqrt(800))) < 0.5 and gap[1] == 0,
          f"U자 경로 조각 {n_piece.tolist()}, 간격 {gap.tolist()}")

    # --- main(): 파일 입출력 ---
    C.setup_environment()
    routes.to_parquet(C.DATA_INTERIM / "s2_routes.parquet", index=False)
    links.to_parquet(C.S1_LINKS_PATH, index=False)
    cands.to_parquet(C.DATA_INTERIM / "s3_candidates.parquet", index=False)
    C.S4_WALK_M = WALK
    S4.main(force=True)
    out = pd.read_parquet(S4.OUT_ACCESS)
    check(len(out) == 5 and list(out.columns) == S4.ACCESS_COLUMNS, "main 산출물 오류")
    check("repeat_ratio" in pd.read_parquet(S4.OUT_LINES).columns, "경로 선 표에 repeat_ratio 없음")

    # --- 자르기가 표에 반영되는지: R0 시점을 (80,0)으로 옮기면 c0(50,50)은 a=0 끝점 행이 되고 거리가 멀어진다 ---
    r2 = routes.copy()
    r2.loc[0, "start_x"] = X0 + 80
    ld2 = RL.build_route_lines(r2, ends)
    acc2 = S4.access_table(ld2.geom.to_numpy(), ld2.route_id.to_numpy(), r2.weight.to_numpy(), cands, WALK)
    row = acc2[(acc2.route_id == 0) & (acc2.cand_id == 0)]
    check(ld2.trim_start_m[0] == 80 and ld2.route_len[0] == 420 and len(row) == 1 and row.a.iloc[0] == 0
          and abs(row.walk_d.iloc[0] - np.hypot(30, 50)) < 1e-6,
          f"자르기 후 R0: 자른 길이 {ld2.trim_start_m[0]}, 길이 {ld2.route_len[0]}, c0 행 {row.to_dict('records')}")
    c4 = acc2[(acc2.route_id == 0) & (acc2.cand_id == 4)]
    check(np.allclose(c4[["a", "walk_d"]].to_numpy(), [[70, 30]]), f"자르기 후 c4 {c4.to_dict('records')}")
    ld3 = RL.build_route_lines(r2, ends, trim=False)
    check(ld3.route_len[0] == 500 and ld3.trim_start_m[0] == 0, "trim=False인데 잘림")

    if fails:
        print("\n실패 항목:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("\n모든 검증 통과")


if __name__ == "__main__":
    main()
