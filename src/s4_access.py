"""
4단계: 접근점 표

최적화(MILP)의 입력이 되는 전처리. 대여소는 경로 위에 정확히 있을 필요가 없다 (사람들은 300m 정도는 걷는다).
후보지가 경로를 만나는 지점을 계산해 표로 만든다. 이 표의 행 수가 이 프로젝트의 실질적인 문제 규모다.

절차
  1. 대표경로 선 (route_lines.py, 3단계와 같은 선): link_seq 순서대로 링크를 이어 방향이 있는 LineString 하나로 만든다.
     '경로 시작점부터 몇 m 지점인가'(a)는 선이 순서대로 이어져 있어야 잴 수 있다.
     - 링크는 방향이 없이 저장돼 있으므로(u < v) 앞뒤 링크와 공유하는 노드를 보고 방향을 정한다.
     - 연속 링크가 노드를 공유하지 않는 곳(1단계에서 매칭이 끊겨 관측 궤적으로 이은 곳, 도로에 없는 노드 쌍)은
       직선으로 잇는다. 1단계는 그 구간의 모양을 저장하지 않기 때문이다. 이은 곳 수와 길이를 보고한다.
     - 선 끝 자르기 (ROUTE_TRIM_TO_ENDPOINTS, 2026-09-28 결정 A): 1단계는 경로 시작·끝의 반쪽 링크를 링크 전체로 넣으므로
       선이 실제 시종점보다 더 나가 있다. 선을 2단계 대표 시점·종점에서 잘라 a와 route_len을 실제 시종점 기준으로 잰다.
  2. 후보지 점으로 STRtree 공간 색인을 만들고, 경로마다 반경 S4_WALK_M 안의 후보지만 뽑는다.
     색인이 없으면 (경로 수 x 후보지 수)만큼 거리 계산이 돈다.
     predicate="dwithin"은 '경로를 반경만큼 부풀린 도형(buffer)과 겹치는 후보 -> 정확한 거리 확인'과 같은 결과를
     buffer 도형을 만들지 않고 낸다.
  3. 걸린 쌍마다
       walk_d = 후보지에서 경로 선까지 최단 직선거리 (보행 도로망 거리가 아님)
       a      = 경로 선 위에서 후보지와 가장 가까운 점의 누적거리 (경로 시작점 기준, shapely line_locate_point)

표 칼럼 (s4_access.parquet, 1행 = 경로 하나와 후보지 하나의 만남)
  route_id  : 대표경로 ID (s2_routes)
  cand_id   : 후보지 ID (s3_candidates)
  a         : 경로 시작점 기준 누적거리 (m)
  walk_d    : 후보지와 경로 사이 거리 (m)
  weight    : 경로 가중치 (그 대표경로로 묶인 통행 수)
  route_len : 경로 전체 길이 (m, 직선으로 이은 곳 포함)

주의
  - 경로가 한 후보지 반경 안을 두 번 지나가도(되돌아오는 경로, 고리) 행은 하나이고 a는 가장 가까운 지점 하나다.
    반경 원 안 조각 사이의 경로상 간격 분포(pass_gap_m)와 경로별 링크 반복 비율(repeat_ratio)로 규모를 본다.
  - 후보지가 경로 끝 바깥에 있으면 a가 0 또는 route_len이 된다 (끝점 행). 2026-09-28 결정: 그대로 두고 walk_d 분포만 확인.

입력  : data/interim/s2_routes.parquet      route_id, link_seq, weight, start_x, start_y, end_x, end_y
        data/interim/s1_links.parquet       link_id, geometry (WKB LineString, EPSG:5179)
        data/interim/s3_candidates.parquet  cand_id, x, y, is_existing
출력  : data/interim/s4_access.parquet       접근점 표 (위 칼럼)
        data/interim/s4_route_lines.parquet  route_id, route_len, n_breaks, break_m, repeat_ratio,
                                             trim_start_m, trim_end_m (선 끝에서 자른 길이), geometry(WKB)
                                             (5단계 평가와 민감도 분석에 재사용, route_id로 접근점 표와 조인)
        data/interim/s4_stats.json

실행
  python s4_access.py            # 캐시가 있으면 건너뜀
  python s4_access.py --force
"""
import argparse
import json
import time

import numpy as np
import pandas as pd
import shapely

import config as C
import route_lines as RL

OUT_ACCESS = C.DATA_INTERIM / "s4_access.parquet"
OUT_LINES = C.DATA_INTERIM / "s4_route_lines.parquet"
OUT_STATS = C.DATA_INTERIM / "s4_stats.json"
S3_ACCESS = C.DATA_INTERIM / "s3_access.parquet"

ACCESS_COLUMNS = ["route_id", "cand_id", "a", "walk_d", "weight", "route_len"]


# ===========================================================================
# 2~3. 접근점 표
# ===========================================================================
def access_table(lines, route_ids, weights, cands, walk_m=None):
    """
    lines   : 대표경로 LineString 배열 (route_ids, weights와 같은 순서)
    cands   : DataFrame [cand_id, x, y]
    반환    : DataFrame ACCESS_COLUMNS (route_id, a 순으로 정렬)
    """
    walk_m = C.S4_WALK_M if walk_m is None else walk_m
    lines = np.asarray(lines, dtype=object)
    pts = shapely.points(cands.x.to_numpy(), cands.y.to_numpy())
    # 후보지 점 색인. query(경로들)는 (경로 순번, 후보지 순번) 쌍을 돌려준다
    li, ci = shapely.STRtree(pts).query(lines, predicate="dwithin", distance=walk_m)
    df = pd.DataFrame({
        "route_id": np.asarray(route_ids)[li],
        "cand_id": cands.cand_id.to_numpy()[ci],
        "a": shapely.line_locate_point(lines[li], pts[ci]),
        "walk_d": shapely.distance(lines[li], pts[ci]),
        "weight": np.asarray(weights)[li],
        "route_len": shapely.length(lines)[li],
    })
    return df.sort_values(["route_id", "a", "cand_id"], ignore_index=True)


def pass_gaps(access, line_by_route, xy_by_cand, walk_m):
    """
    행마다 경로가 후보지 반경 원 안을 지나는 모양 (원과 선의 교집합).
    반환: (조각 수 배열, 조각 사이 경로상 최대 간격 m 배열; 조각이 1개면 0)

    조각이 2개 이상이어도 모두 '나갔다 다시 들어옴'은 아니다. 경로가 꺾이거나 스스로 교차하는 곳에서는
    한 번 지나간 구간이 여러 조각으로 잘려 나와 간격이 0에 가깝다. 그래서 조각 수 대신 간격 분포를 본다.
    간격이 길수록 경로가 원 밖으로 멀리 나갔다가 돌아온 것이고, 그때 a(가장 가까운 한 지점)는
    그 후보지를 경로의 어디서 쓸 수 있는지 다 나타내지 못한다.
    완전히 겹쳐 되돌아오는 구간은 교집합에서 한 조각으로 합쳐지므로 여기서는 잡히지 않는다 (repeat_ratio로 따로 봄).
    """
    lines = np.array([line_by_route[r] for r in access.route_id], dtype=object)
    xy = np.array([xy_by_cand[c] for c in access.cand_id])
    circles = shapely.buffer(shapely.points(xy[:, 0], xy[:, 1]), walk_m, quad_segs=16)
    parts = shapely.line_merge(shapely.intersection(lines, circles))
    n = shapely.get_num_geometries(parts)
    gap = np.zeros(len(access))
    for i in np.flatnonzero(n >= 2):
        # 조각마다 경로 위 구간 [시작 a, 끝 a], 정렬한 뒤 이웃 구간 사이 간격의 최댓값
        iv = sorted(sorted(shapely.line_locate_point(lines[i], shapely.points(shapely.get_coordinates(p)[[0, -1]])))
                    for p in shapely.get_parts(parts[i]))
        gap[i] = max(max(b[0] - a[1], 0.0) for a, b in zip(iv[:-1], iv[1:]))
    return n, gap


# ===========================================================================
# 보고용 통계
# ===========================================================================
def _dist(s):
    s = np.asarray(s, dtype=float)
    if len(s) == 0:
        return {}
    q = np.percentile(s, [0, 25, 50, 75, 90, 100])
    return {"mean": round(float(s.mean()), 2), "min": float(q[0]), "p25": float(q[1]), "p50": float(q[2]),
            "p75": float(q[3]), "p90": float(q[4]), "max": float(q[5])}


def access_stats(access, lines_df, routes, cands, top_n=None):
    """
    반드시 확인할 지표
      n_rows              : 전체 행 수 (MILP 규모)
      unreached_routes    : 후보지가 하나도 걸리지 않은 경로 (도달 불가, 별도 처리)
      cands_per_route     : 경로당 후보지 수 분포 (0개 경로 포함)
      routes_per_cand     : 후보지별 걸린 경로 수 분포 (0개 후보지 포함) + 구간별 개수 + 상위 top_n
    """
    top_n = top_n or C.S4_TOP_N_CANDS
    per_route = access.groupby("route_id").size().reindex(routes.route_id, fill_value=0)
    unreached = routes[per_route.to_numpy() == 0]
    g = access.groupby("cand_id")
    per_cand = g.size().reindex(cands.cand_id, fill_value=0)
    w_cand = g.weight.sum().reindex(cands.cand_id, fill_value=0)
    bins = [0, 1, 2, 5, 10, 20, 50, 100, np.inf]
    labels = ["0", "1", "2-4", "5-9", "10-19", "20-49", "50-99", "100+"]
    hist = pd.cut(per_cand, bins=[b - 0.5 for b in bins[:-1]] + [np.inf], labels=labels).value_counts().reindex(labels)
    top = cands.assign(n_routes=per_cand.to_numpy(), weight_sum=w_cand.to_numpy()) \
        .sort_values(["n_routes", "weight_sum", "cand_id"], ascending=[False, False, True]).head(top_n)
    return {
        "n_rows": int(len(access)),
        "n_routes": int(len(routes)),
        "n_candidates": int(len(cands)),
        "n_unreached_routes": int(len(unreached)),
        "unreached_weight": int(unreached.weight.sum()),
        "unreached_routes": [{"route_id": int(r.route_id), "weight": int(r.weight)} for r in unreached.itertuples()],
        "cands_per_route": _dist(per_route),
        "n_cands_no_route": int((per_cand == 0).sum()),
        "routes_per_cand": _dist(per_cand),
        "routes_per_cand_hist": {k: int(v) for k, v in hist.items()},
        "top_cands": [{"cand_id": int(r.cand_id), "n_routes": int(r.n_routes), "weight_sum": int(r.weight_sum),
                       "x": round(float(r.x), 1), "y": round(float(r.y), 1), "is_existing": bool(r.is_existing)}
                      for r in top.itertuples()],
        "walk_d": _dist(access.walk_d),
        "a_over_len": _dist(access.a / access.route_len),
        **end_rows(access),
    }


def end_rows(access, eps=None):
    """
    끝점 행: 후보지가 경로 끝 바깥에 있어 a가 0(시점) 또는 route_len(종점)으로 투영된 행.
    한 경로에서 여러 후보지가 같은 a를 갖게 된다. 끝점 행과 중간 행의 walk_d 분포를 나눠 본다.
    """
    eps = C.S4_END_EPS_M if eps is None else eps
    at_start = access.a <= eps
    at_end = (access.route_len - access.a <= eps) & ~at_start
    mid = ~(at_start | at_end)
    return {"n_rows_at_start": int(at_start.sum()), "n_rows_at_end": int(at_end.sum()),
            "walk_d_at_start": _dist(access.walk_d[at_start]), "walk_d_at_end": _dist(access.walk_d[at_end]),
            "walk_d_middle": _dist(access.walk_d[mid])}


def line_checks(lines_df, routes):
    """
    대표경로 선 점검
      n_routes_with_breaks, break_m : 연속 링크가 이어지지 않아 직선으로 이은 곳
      trim_m                        : 선 끝에서 자른 길이 합 (시작 + 끝), 경로별 분포와 선 길이 대비 비율
      start_offset_m                : 선 시작점과 2단계 대표 시점 사이 거리
                                      (자르지 않으면 반쪽 링크 때문에 0이 아님. 자르면 시점이 첫 링크에서 떨어진 거리)
      end_offset_m                  : 선 끝점과 2단계 대표 종점 사이 거리
      offset_over                   : 시작(끝) 편차가 S4_OFFSET_CHECK_M을 넘는 경로 목록
      n_reversed_vs_s2              : 선 시작점이 2단계 시점보다 종점에 더 가까운 경로 (0이어야 한다)
      n_routes_repeated_links       : 같은 링크를 두 번 이상 지나는 경로 (되돌아오기, 고리)
      repeat_ratio                  : 그 경로들의 링크 반복 비율 분포
    """
    r = routes.set_index("route_id").loc[lines_df.route_id]
    s = np.array([shapely.get_coordinates(g)[0] for g in lines_df.geom])
    e = np.array([shapely.get_coordinates(g)[-1] for g in lines_df.geom])
    d_start = np.hypot(s[:, 0] - r.start_x.to_numpy(), s[:, 1] - r.start_y.to_numpy())
    d_end = np.hypot(s[:, 0] - r.end_x.to_numpy(), s[:, 1] - r.end_y.to_numpy())
    e_end = np.hypot(e[:, 0] - r.end_x.to_numpy(), e[:, 1] - r.end_y.to_numpy())
    rep = lines_df.repeat_ratio.to_numpy() > 0
    lim = C.S4_OFFSET_CHECK_M
    over = [{"route_id": int(rid), "start_offset_m": round(float(a), 1), "end_offset_m": round(float(b), 1)}
            for rid, a, b in zip(lines_df.route_id, d_start, e_end) if a > lim or b > lim]
    trim = lines_df.trim_start_m + lines_df.trim_end_m
    return {
        "trim_m": _dist(trim),
        "trim_m_total": round(float(trim.sum()), 1),
        "trim_share_of_untrimmed_len": round(float(trim.sum() / (lines_df.route_len.sum() + trim.sum())), 4),
        "end_offset_m": _dist(e_end),
        "n_start_offset_over": int((d_start > lim).sum()),
        "n_end_offset_over": int((e_end > lim).sum()),
        "offset_over": over,
        "repeat_ratio": _dist(lines_df.repeat_ratio[rep]),
        "route_len": _dist(lines_df.route_len),
        "n_routes_with_breaks": int((lines_df.n_breaks > 0).sum()),
        "n_breaks": int(lines_df.n_breaks.sum()),
        "break_m_total": round(float(lines_df.break_m.sum()), 1),
        "break_share_of_len": round(float(lines_df.break_m.sum() / lines_df.route_len.sum()), 4),
        "break_m_per_break": _dist(lines_df.break_m[lines_df.n_breaks > 0] / lines_df.n_breaks[lines_df.n_breaks > 0]),
        "start_offset_m": _dist(d_start),
        "n_reversed_vs_s2": int((d_end < d_start).sum()),
        "n_routes_repeated_links": int(rep.sum()),
    }


def compare_s3(access, s3):
    """
    3단계 접근점 표와 쌍 비교. 두 단계가 같은 선(route_lines)을 쓰므로 쌍과 거리가 모두 같아야 한다.
    """
    k4 = access.set_index(["route_id", "cand_id"]).walk_d
    k3 = s3.set_index(["route_id", "cand_id"]).dist_m
    common = k4.index.intersection(k3.index)
    d = (k4.loc[common] - k3.loc[common]).to_numpy()
    return {"s3_rows": int(len(k3)), "only_s4": int(len(k4.index.difference(k3.index))),
            "only_s3": int(len(k3.index.difference(k4.index))),
            "n_s4_closer": int((d < -0.01).sum()), "max_s4_closer_m": round(float(-d.min()), 1) if len(d) else None,
            "n_s4_farther": int((d > 0.01).sum()), "max_s4_farther_m": round(float(d.max()), 1) if len(d) else None}


def print_report(log):
    s = log
    print("\n========== 4단계 접근점 표 ==========")
    print(f"설정                      도보 반경 {s['config']['S4_WALK_M']}m")
    print(f"입력                      대표경로 {s['n_routes']:,}, 후보지 {s['n_candidates']:,}")
    L = s["lines"]
    print(f"경로 선                   길이 {L['route_len']}")
    print(f"  끊겨 직선으로 이은 곳   경로 {L['n_routes_with_breaks']}개, {L['n_breaks']}곳, "
          f"{L['break_m_total']:,.0f}m (전체 길이의 {L['break_share_of_len']:.2%})")
    print(f"  선 끝 자르기            {'켬' if s['config']['ROUTE_TRIM_TO_ENDPOINTS'] else '끔'}: 합계 {L['trim_m_total']:,.0f}m "
          f"(자르기 전 길이의 {L['trim_share_of_untrimmed_len']:.2%}), 경로별 {L['trim_m']}")
    print(f"  시작점 - 2단계 시점     {L['start_offset_m']}  (방향 반대 {L['n_reversed_vs_s2']}개)")
    print(f"  끝점 - 2단계 종점       {L['end_offset_m']}")
    print(f"  편차 {s['config']['S4_OFFSET_CHECK_M']}m 초과 경로      시작 {L['n_start_offset_over']}개, 끝 {L['n_end_offset_over']}개"
          f" (목록: s4_stats.json lines.offset_over)")
    print(f"  링크 반복 경로          {L['n_routes_repeated_links']}개, 반복 비율 {L['repeat_ratio']}")
    print(f"전체 행 수                {s['n_rows']:>8,}")
    print(f"도달 불가 경로            {s['n_unreached_routes']}개 (가중치 {s['unreached_weight']})")
    print(f"경로당 후보지 수          {s['cands_per_route']}")
    print(f"후보지별 걸린 경로 수     {s['routes_per_cand']}")
    print(f"  구간별 후보지 수        {s['routes_per_cand_hist']}")
    print(f"  경로 0개 후보지         {s['n_cands_no_route']:,}")
    print(f"걸린 경로 수 상위 {len(s['top_cands'])} 후보지")
    print("  순위  cand_id  경로수  가중치합          x           y")
    for i, t in enumerate(s["top_cands"], 1):
        print(f"  {i:>4}  {t['cand_id']:>7}  {t['n_routes']:>6}  {t['weight_sum']:>8}  {t['x']:>10.1f}  {t['y']:>10.1f}"
              + ("  (기존)" if t["is_existing"] else ""))
    print(f"walk_d                    {s['walk_d']}")
    print(f"a / route_len             {s['a_over_len']}")
    print(f"끝점 행                   시점 {s['n_rows_at_start']:,}, 종점 {s['n_rows_at_end']:,}")
    print(f"  walk_d 시점 행          {s['walk_d_at_start']}")
    print(f"  walk_d 종점 행          {s['walk_d_at_end']}")
    print(f"  walk_d 중간 행          {s['walk_d_middle']}")
    print(f"반경 원과 선 교집합이 2조각 이상인 행 {s['n_rows_multi_piece']:,}")
    print(f"  조각 사이 경로상 최대 간격 m  {s['pass_gap_m']}")
    if "vs_s3" in s:
        print(f"3단계 표와 비교           {s['vs_s3']}")
    print("=====================================\n")


def main(force=False):
    C.setup_environment()
    force = force or C.FORCE_RECOMPUTE
    if OUT_ACCESS.exists() and OUT_LINES.exists() and OUT_STATS.exists() and not force:
        print(f"[s4] 기존 산출물이 있어 건너뜁니다 (--force 로 재계산). {OUT_STATS}")
        print_report(json.loads(OUT_STATS.read_text(encoding="utf-8")))
        return
    t0 = time.time()
    cands = pd.read_parquet(C.DATA_INTERIM / "s3_candidates.parquet", columns=["cand_id", "x", "y", "is_existing"])
    routes, lines_df = RL.load_route_lines()
    access = access_table(lines_df.geom.to_numpy(), lines_df.route_id.to_numpy(), routes.weight.to_numpy(), cands)

    log = {"config": {"S4_WALK_M": C.S4_WALK_M, "S4_OFFSET_CHECK_M": C.S4_OFFSET_CHECK_M,
                      "ROUTE_TRIM_TO_ENDPOINTS": C.ROUTE_TRIM_TO_ENDPOINTS}}
    log.update(access_stats(access, lines_df, routes, cands))
    log["lines"] = line_checks(lines_df, routes)
    n_piece, gap = pass_gaps(access, dict(zip(lines_df.route_id, lines_df.geom)),
                             dict(zip(cands.cand_id, zip(cands.x, cands.y))), C.S4_WALK_M)
    log["n_rows_multi_piece"] = int((n_piece >= 2).sum())
    log["pass_gap_m"] = _dist(gap[n_piece >= 2])
    if S3_ACCESS.exists():
        log["vs_s3"] = compare_s3(access, pd.read_parquet(S3_ACCESS))
    log["elapsed_s"] = round(time.time() - t0, 1)

    access[ACCESS_COLUMNS].to_parquet(OUT_ACCESS, index=False)
    lines_df.assign(geometry=shapely.to_wkb(lines_df.geom.to_numpy())).drop(columns="geom").to_parquet(OUT_LINES, index=False)
    OUT_STATS.write_text(json.dumps(log, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print_report(log)
    print(f"[s4] 저장 완료 ({log['elapsed_s']}초): {OUT_ACCESS.name} ({len(access):,}행), {OUT_LINES.name}, {OUT_STATS.name}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="캐시 무시하고 재계산")
    main(force=ap.parse_args().force)
