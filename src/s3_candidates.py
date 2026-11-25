"""
3단계: 격자 후보지 생성

대여소를 놓을 수 있는 후보 지점을 만든다.
교차점만 쓰면 공원 입구나 아파트 출입구 같은 좋은 자리가 빠지므로 격자 셀 중심을 쓴다.

절차
  1. 모든 대표경로를 감싸는 사각형을 구하고 S3_BBOX_MARGIN_M 여유를 둔다.
  2. S3_GRID_M 간격 격자를 만들고 각 셀 중심점을 후보지로 삼는다.
     규모는 후보지 수가 아니라 접근점 표 행 수로 판단한다 (S3_ACCESS_ROWS_WARN).
  3. 반경 S3_RADIUS_M 안에 지나가는 대표경로가 S3_MIN_ROUTES개 미만인 셀은 뺀다.
     이 필터만으로 허허벌판, 강, 산이 걸러진다. POI나 설치 가능성 필터는 넣지 않는다.
  +  연구 지역(서울) 밖 셀은 뺀다 (S3_CLIP_TO_STUDY_REGION, 대여소 후보지는 서울 안에만).
  +  기존 대여소 위치를 확보했다면 is_existing=True로 목록에 넣는다 (반경 필터와 무관).

입력  : data/interim/s2_routes.parquet (route_id, link_seq, weight, 시종점)
        data/interim/s1_links.parquet  (link_id, geometry)  -> 대표경로 선 (route_lines.py, 4단계와 같은 선)
출력  : data/interim/s3_candidates.parquet  cand_id, x, y (EPSG:5179), n_routes(반경 내 대표경로 수),
                                            n_trips(반경 내 경로 가중치 합), is_existing
        data/interim/s3_access.parquet      접근점 표: 경로마다 반경 안 후보지 (route_id, cand_id, dist_m)
                                            최적화(MILP) 규모를 정하는 건 후보지 수가 아니라 이 표의 행 수다.
        data/interim/s3_stats.json

실행
  python s3_candidates.py            # 캐시가 있으면 건너뜀
  python s3_candidates.py --force
"""
import argparse
import json

import numpy as np
import pandas as pd
import shapely

import config as C

OUT_CANDS = C.DATA_INTERIM / "s3_candidates.parquet"
OUT_ACCESS = C.DATA_INTERIM / "s3_access.parquet"
OUT_STATS = C.DATA_INTERIM / "s3_stats.json"


# ===========================================================================
# 입력
# ===========================================================================
def load_route_geoms():
    """
    대표경로 선, 가중치, route_id. 선은 4단계와 같은 것을 쓴다 (route_lines.py: 링크를 순서대로 잇고,
    ROUTE_TRIM_TO_ENDPOINTS면 2단계 시종점에서 자른 선). 전에는 링크를 순서 없이 모은 MultiLineString을 썼는데,
    4단계가 선 끝을 자르자 돌출 구간으로만 걸렸던 후보지 56개가 어떤 경로에도 안 걸리게 돼 맞췄다 (2026-09-28).
    """
    import route_lines as RL
    routes, lines = RL.load_route_lines()
    return lines.geom.to_numpy(), routes.weight.to_numpy(), routes.route_id.to_numpy()


def study_region_polygon():
    """연구 지역(STUDY_REGION) 시군구 경계를 합친 다각형 (EPSG:5179)."""
    import region_compare as RC
    import region_density as R
    sgg = R.load_sgg()
    return shapely.union_all(sgg[RC.in_region(sgg, C.STUDY_REGION)].geometry.to_numpy())


def load_existing_stations(path):
    """기존 대여소 CSV (station_id, lon, lat) -> [station_id, x, y] (EPSG:5179)"""
    from pyproj import Transformer
    st = pd.read_csv(path)
    x, y = Transformer.from_crs(C.CRS_SRC, C.CRS_WORK, always_xy=True).transform(st.lon.to_numpy(), st.lat.to_numpy())
    return pd.DataFrame({"station_id": st.station_id.astype(str), "x": x, "y": y})


# ===========================================================================
# 후보지 만들기
# ===========================================================================
def grid_centers(geoms, grid_m, margin_m):
    """경로 전체를 감싸는 사각형 + 여유 안에 grid_m 간격 셀 중심점 (x, y 배열)."""
    x0, y0, x1, y1 = shapely.total_bounds(geoms)
    x0, y0, x1, y1 = x0 - margin_m, y0 - margin_m, x1 + margin_m, y1 + margin_m
    # 격자 원점을 grid_m 배수에 맞춘다: 같은 설정이면 입력이 조금 바뀌어도 셀 위치가 같다
    xs = np.arange(np.floor(x0 / grid_m) * grid_m, x1, grid_m) + grid_m / 2
    ys = np.arange(np.floor(y0 / grid_m) * grid_m, y1, grid_m) + grid_m / 2
    gx, gy = np.meshgrid(xs, ys)
    return gx.ravel(), gy.ravel()


def routes_within(x, y, geoms, weights, radius_m):
    """
    점마다 반경 radius_m 안을 지나가는 경로 수와 가중치 합.
    STRtree 공간 색인으로 가까운 경로만 거리 계산한다 (모든 쌍 비교 없음).
    """
    tree = shapely.STRtree(geoms)
    pi, gi = tree.query(shapely.points(x, y), predicate="dwithin", distance=radius_m)
    n = np.bincount(pi, minlength=len(x))
    w = np.bincount(pi, weights=np.asarray(weights, dtype=float)[gi], minlength=len(x))
    return n, w


def make_candidates(geoms, weights, grid_m=None, radius_m=None, min_routes=None, margin_m=None,
                    region=None, existing=None, route_ids=None):
    """
    절차 1~3. 테스트와 설정 비교에서 값을 바꿔 부를 수 있게 인자로 받는다 (None이면 config 값).
    region  : 이 다각형 안의 셀만 남긴다 (None이면 자르지 않음)
    existing: 기존 대여소 [station_id, x, y] (None이면 없음)
    반환: (후보지 DataFrame, 접근점 표 DataFrame, 로그 dict)
    """
    grid_m = grid_m or C.S3_GRID_M
    radius_m = radius_m or C.S3_RADIUS_M
    min_routes = C.S3_MIN_ROUTES if min_routes is None else min_routes
    margin_m = C.S3_BBOX_MARGIN_M if margin_m is None else margin_m

    x, y = grid_centers(geoms, grid_m, margin_m)
    log = {"n_cells_bbox": int(len(x))}
    if region is not None:
        inside = shapely.contains_xy(region, x, y)
        x, y = x[inside], y[inside]
        log["n_cells_in_region"] = int(len(x))
    n, w = routes_within(x, y, geoms, weights, radius_m)
    keep = n >= min_routes
    cands = pd.DataFrame({"x": x[keep], "y": y[keep], "n_routes": n[keep], "n_trips": w[keep].round().astype(np.int64),
                          "is_existing": False, "station_id": None})
    log["n_grid_candidates"] = int(keep.sum())

    if existing is not None and len(existing):
        ne, we = routes_within(existing.x.to_numpy(), existing.y.to_numpy(), geoms, weights, radius_m)
        ex = existing.assign(n_routes=ne, n_trips=we.round().astype(np.int64), is_existing=True)
        cands = pd.concat([cands, ex[cands.columns]], ignore_index=True)
        log["n_existing"] = int(len(ex))
    cands.insert(0, "cand_id", np.arange(len(cands), dtype=np.int64))
    log["n_candidates"] = int(len(cands))
    log.update(coverage(cands[~cands.is_existing], geoms, weights, radius_m))
    log["n_routes_dist"] = {k: int(v) for k, v in
                            zip(["min", "p25", "p50", "p75", "max"],
                                np.percentile(cands.n_routes, [0, 25, 50, 75, 100]) if len(cands) else [0] * 5)}
    route_ids = np.arange(len(geoms)) if route_ids is None else np.asarray(route_ids)
    access = access_table(cands, geoms, radius_m, route_ids)
    log.update(access_stats(access, route_ids, len(cands)))
    log["advice"] = advice(log["n_access_rows"], log["n_cands_unused"], grid_m)
    return cands, access, log


def access_table(cands, geoms, radius_m, route_ids=None):
    """
    접근점 표: 경로마다 반경 radius_m 안에 있는 후보지 (경로 선까지의 최단거리 포함).
    반환: DataFrame [route_id, cand_id, dist_m]  (route_ids가 없으면 geoms 순번을 route_id로)
    """
    pts = shapely.points(cands.x.to_numpy(), cands.y.to_numpy())
    gi, ci = shapely.STRtree(pts).query(geoms, predicate="dwithin", distance=radius_m)
    rid = np.arange(len(geoms)) if route_ids is None else np.asarray(route_ids)
    return pd.DataFrame({"route_id": rid[gi], "cand_id": cands.cand_id.to_numpy()[ci],
                         "dist_m": shapely.distance(geoms[gi], pts[ci])}).sort_values(["route_id", "dist_m"],
                                                                                        ignore_index=True)


def access_stats(access, route_ids, n_cands):
    """접근점 표 규모: 행 수, 경로당 후보지 수 분포(0개인 경로 포함), 어떤 경로에도 안 걸리는 후보지 수."""
    per_route = access.groupby("route_id").size().reindex(route_ids, fill_value=0)
    return {"n_access_rows": int(len(access)),
            "cands_per_route": {"mean": round(float(per_route.mean()), 1), "p50": float(per_route.median()),
                                "p90": float(per_route.quantile(0.9)), "max": int(per_route.max()),
                                "min": int(per_route.min())},
            "n_cands_unused": int(n_cands - access.cand_id.nunique())}


def coverage(cands, geoms, weights, radius_m):
    """
    후보지가 경로를 얼마나 덮는지 (설정 비교용 진단값).
      route_any : 경로 어디든 반경 안에 후보지가 하나라도 있는 경로 가중치 비율
      route_ends: 경로 시점과 종점 모두 반경 안에 후보지가 있는 경로 가중치 비율
                  (목적함수가 경로 시종점을 기준선으로 쓰므로 이쪽이 더 중요)
    """
    w = np.asarray(weights, dtype=float)
    if len(cands) == 0:
        return {"cover_route_any": 0.0, "cover_route_ends": 0.0}
    pts = shapely.points(cands.x.to_numpy(), cands.y.to_numpy())
    tree = shapely.STRtree(pts)
    gi, _ = tree.query(geoms, predicate="dwithin", distance=radius_m)
    any_hit = np.zeros(len(geoms), bool)
    any_hit[gi] = True
    # 시종점: 경로 모양의 첫 점과 마지막 점 (MultiLineString이면 첫 조각의 시작, 마지막 조각의 끝)
    starts = shapely.points([shapely.get_coordinates(g)[0] for g in geoms])
    ends = shapely.points([shapely.get_coordinates(g)[-1] for g in geoms])
    s_hit = np.zeros(len(geoms), bool)
    e_hit = np.zeros(len(geoms), bool)
    s_hit[tree.query(starts, predicate="dwithin", distance=radius_m)[0]] = True
    e_hit[tree.query(ends, predicate="dwithin", distance=radius_m)[0]] = True
    return {"cover_route_any": round(float(w[any_hit].sum() / w.sum()), 4),
            "cover_route_ends": round(float(w[s_hit & e_hit].sum() / w.sum()), 4)}


def advice(n_rows, n_unused, grid_m):
    """접근점 표 행 수로 규모를 판단한다 (후보지 수가 아니라 MILP 크기를 정하는 값)."""
    if n_rows <= C.S3_ACCESS_ROWS_WARN:
        return f"접근점 표 {n_rows:,}행 <= {C.S3_ACCESS_ROWS_WARN:,}: 그대로 진행"
    if n_unused > 0:
        return f"접근점 표가 큼: 어떤 경로에도 안 걸리는 후보지 {n_unused:,}개부터 제거"
    return f"접근점 표가 큼: 격자를 키운다 (현재 {grid_m}m)"


def print_report(log, cfg):
    print("\n========== 3단계 격자 후보지 ==========")
    print(f"설정                     격자 {cfg['S3_GRID_M']}m, 반경 {cfg['S3_RADIUS_M']}m, "
          f"최소 경로 {cfg['S3_MIN_ROUTES']}, 여유 {cfg['S3_BBOX_MARGIN_M']}m")
    print(f"사각형 안 셀             {log['n_cells_bbox']:>8,}")
    if "n_cells_in_region" in log:
        print(f"연구 지역 안 셀          {log['n_cells_in_region']:>8,}")
    print(f"반경 필터 후 격자 후보지 {log['n_grid_candidates']:>8,}")
    if "n_existing" in log:
        print(f"기존 대여소 (플래그)     {log['n_existing']:>8,}")
    print(f"최종 후보지              {log['n_candidates']:>8,}")
    print(f"후보지당 반경 내 경로 수 {log['n_routes_dist']}")
    print(f"경로 덮임 (가중치 기준)  어디든 {log['cover_route_any']:.1%}, 시종점 모두 {log['cover_route_ends']:.1%}")
    print(f"접근점 표 행 수          {log['n_access_rows']:>8,}  (경로당 후보지 {log['cands_per_route']})")
    print(f"어떤 경로에도 안 걸리는 후보지 {log['n_cands_unused']:,}")
    print(f"안내                     {log['advice']}")
    print("=======================================\n")


def main(force=False):
    C.setup_environment()
    force = force or C.FORCE_RECOMPUTE
    cfg_keys = ["S3_GRID_M", "S3_RADIUS_M", "S3_MIN_ROUTES", "S3_BBOX_MARGIN_M", "S3_CLIP_TO_STUDY_REGION",
                "S3_EXISTING_STATIONS_PATH", "ROUTE_TRIM_TO_ENDPOINTS"]
    cfg = {k: getattr(C, k) for k in cfg_keys}
    if OUT_CANDS.exists() and OUT_ACCESS.exists() and OUT_STATS.exists() and not force:
        print(f"[s3] 기존 산출물이 있어 건너뜁니다 (--force 로 재계산). {OUT_STATS}")
        log = json.loads(OUT_STATS.read_text(encoding="utf-8"))
        print_report(log, log["config"])
        return
    geoms, weights, route_ids = load_route_geoms()
    region = study_region_polygon() if C.S3_CLIP_TO_STUDY_REGION else None
    existing = load_existing_stations(C.S3_EXISTING_STATIONS_PATH) if C.S3_EXISTING_STATIONS_PATH else None
    cands, access, log = make_candidates(geoms, weights, region=region, existing=existing, route_ids=route_ids)
    log["n_routes_input"] = int(len(geoms))
    log["config"] = cfg
    cands.to_parquet(OUT_CANDS, index=False)
    access.to_parquet(OUT_ACCESS, index=False)
    OUT_STATS.write_text(json.dumps(log, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print_report(log, cfg)
    print(f"[s3] 저장 완료: {OUT_CANDS.name} ({len(cands):,}개), {OUT_STATS.name}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="캐시 무시하고 재계산")
    main(force=ap.parse_args().force)
