"""
8단계: 결과 확정 및 내보내기

7단계 검증이 끝난 최종 결과를 테이블별 GeoParquet(EPSG:5179)로 내보낸다. 이 테이블 구조(SCHEMA)가 그대로 PostGIS 스키마가 된다.
9단계 DDL은 SCHEMA와 칼럼 이름·순서·타입이 같아야 하고, 적재 스크립트는 이 파일들을 그대로 넣는다.
계산은 여기서 끝낸다: 웹이 할 일은 조회와 집계뿐이므로, 웹에서 필요한 비율·구 코드 같은 값도 여기서 칼럼으로 만든다.

테이블 (geom은 EPSG:5179)
  candidates          cand_id, sgg_code, n_routes, n_trips, is_existing, station_id, boot_freq, geom(Point)
  scenarios           scenario_id, cond_id, mode, build_type, walk_m, trip_unit, p, objective, upper_bound,
                      n_selected, n_routes_benefit, mip_gap, status
  scenario_selection  scenario_id, cand_id, used                (선택된 후보지만)
  routes              route_id, weight, route_len_m, obs_ratio, repeat_ratio, straight_ratio, n_breaks, geom(LineString)
  route_benefit       scenario_id, route_id, benefit_m, benefit, start_cand, end_cand   (혜택 > 0인 경로만, 없는 행 = 0)
  existing_stations   station_id, cand_id, sgg_code, geom(Point)
  coverage_curve      cond_id, mode, build_type, walk_m, trip_unit, p, objective, upper_bound
  districts           sgg_code, sgg_name, area_km2, n_candidates, n_routes, route_km, geom(MultiPolygon)

시나리오 = 조건(cond_id) + p
  - cond_id = "{mode}-{build_type}-w{walk_m}-{trip_unit}", scenario_id = "{cond_id}-p{p}". 같은 조건이면 다시 돌려도 같은 id다.
  - 시나리오는 칼럼으로 펼치지 않고 긴 형태로 둔다 (scenario_selection, route_benefit). 시나리오가 늘어도 구조가 같다.
  - 민감도 분석 결과는 scenarios에 조건별 행으로 들어간다. 6단계 산출물에는 mode와 p만 있으므로 나머지 조건은
    config 기본값(S8_BUILD_TYPE_DEFAULT, S4_WALK_M, S8_TRIP_UNIT_DEFAULT)으로 채운다.
    7단계 민감도 결과가 s6 형식(s6_selected, s6_routes, s6_stats의 runs)에 build_type, walk_m, trip_unit 칼럼을 담아 오면 그 값을 쓴다.
  - 기존 대여소 기준선 점수는 build_type = "existing"인 시나리오 행으로 둔다 (p = 기존 대여소 수).
    그러면 기존 대여소 비교가 같은 테이블들로 된다 (선택 목록, 경로별 혜택, 목적값).

칼럼 정의
  routes.obs_ratio      관측 시간 비율 = 경로에 묶인 여정들의 (관측 시간 합 / 전체 시간 합). 나머지는 조각 사이 공백(최단경로로 추정)
  routes.repeat_ratio   (링크 수 - 서로 다른 링크 수) / 링크 수 (4단계)
  routes.straight_ratio 끊겨 직선으로 이은 길이 / 경로 길이 (4단계 break_m / route_len)
  route_benefit.benefit 변형 적용 혜택 (absolute: m, ratio: benefit_m / 경로 길이). 목적값 = sum(routes.weight * benefit)
  candidates.sgg_code   후보지가 속한 구 (웹이 공간 조인 없이 구별로 집계할 수 있게)
  districts.route_km    구 안을 지나는 대표경로 선 길이 합 (가중치 미적용)

7단계 입력 계약 (있으면 쓰고, 없으면 해당 칼럼은 NULL, 테이블은 6단계 값만)
  data/interim/s7_bootstrap.parquet  cand_id, boot_freq (0~1, 부트스트랩 반복 중 선택된 비율)
  data/interim/s7_coverage.parquet   mode, p, objective [, upper_bound, build_type, walk_m, trip_unit]  (p = 1~30 커버리지 곡선)

검증 (하나라도 틀리면 저장하지 않는다)
  기본키 중복 없음, 외래키가 가리키는 행이 있음, 시나리오별 선택 수 = n_selected,
  시나리오별 sum(weight * benefit) = objective, 커버리지 곡선과 시나리오가 겹치는 p에서 목적값 같음, 도형 유효·EPSG:5179

입력  : data/interim/s3_candidates.parquet, s2_routes.parquet, s2_trip_route.parquet, s0_journeys.parquet,
        s4_route_lines.parquet, s6_selected.parquet, s6_routes.parquet, s6_stats.json, (s7_*.parquet)
        data/raw/boundary/ (ADMIN_BOUNDARY_PATH, 구 경계)
출력  : data/output/export/{테이블}.parquet  (GeoParquet, 도형 없는 테이블은 일반 parquet)
        data/output/export/manifest.json     테이블별 행 수·칼럼 타입, 빠진 7단계 입력, 사용한 설정값

실행
  python s8_export.py            # 캐시가 있으면 건너뜀
  python s8_export.py --force
"""
import argparse
import json
import time

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

import config as C

IN_CANDS = C.DATA_INTERIM / "s3_candidates.parquet"
IN_S2_ROUTES = C.DATA_INTERIM / "s2_routes.parquet"
IN_TRIP_ROUTE = C.DATA_INTERIM / "s2_trip_route.parquet"
IN_JOURNEYS = C.DATA_INTERIM / "s0_journeys.parquet"
IN_LINES = C.DATA_INTERIM / "s4_route_lines.parquet"
IN_SELECTED = C.DATA_INTERIM / "s6_selected.parquet"
IN_ROUTES = C.DATA_INTERIM / "s6_routes.parquet"
IN_S6_STATS = C.DATA_INTERIM / "s6_stats.json"
IN_BOOT = C.DATA_INTERIM / "s7_bootstrap.parquet"
IN_COVERAGE = C.DATA_INTERIM / "s7_coverage.parquet"
OUT_DIR = C.S8_OUT_DIR
OUT_MANIFEST = OUT_DIR / "manifest.json"

# 테이블 계약: 칼럼 이름·순서·타입. geom은 도형 칼럼 (EPSG:5179). 9단계 DDL이 이것과 같아야 한다.
SCHEMA = {
    "candidates": {"cand_id": "int64", "sgg_code": "string", "n_routes": "int64", "n_trips": "int64",
                   "is_existing": "bool", "station_id": "string", "boot_freq": "float64", "geom": "Point"},
    "scenarios": {"scenario_id": "string", "cond_id": "string", "mode": "string", "build_type": "string",
                  "walk_m": "int64", "trip_unit": "string", "p": "int64", "objective": "float64",
                  "upper_bound": "float64", "n_selected": "int64", "n_routes_benefit": "int64",
                  "mip_gap": "float64", "status": "string"},
    "scenario_selection": {"scenario_id": "string", "cand_id": "int64", "used": "bool"},
    "routes": {"route_id": "int64", "weight": "int64", "route_len_m": "float64", "obs_ratio": "float64",
               "repeat_ratio": "float64", "straight_ratio": "float64", "n_breaks": "int64", "geom": "LineString"},
    "route_benefit": {"scenario_id": "string", "route_id": "int64", "benefit_m": "float64", "benefit": "float64",
                      "start_cand": "int64", "end_cand": "int64"},
    "existing_stations": {"station_id": "string", "cand_id": "int64", "sgg_code": "string", "geom": "Point"},
    "coverage_curve": {"cond_id": "string", "mode": "string", "build_type": "string", "walk_m": "int64",
                       "trip_unit": "string", "p": "int64", "objective": "float64", "upper_bound": "float64"},
    "districts": {"sgg_code": "string", "sgg_name": "string", "area_km2": "float64", "n_candidates": "int64",
                  "n_routes": "int64", "route_km": "float64", "geom": "MultiPolygon"},
}
PRIMARY_KEYS = {
    "candidates": ["cand_id"], "scenarios": ["scenario_id"], "scenario_selection": ["scenario_id", "cand_id"],
    "routes": ["route_id"], "route_benefit": ["scenario_id", "route_id"], "existing_stations": ["station_id"],
    "coverage_curve": ["cond_id", "p"], "districts": ["sgg_code"],
}
# (테이블, 칼럼) -> (참조 테이블, 칼럼). NULL은 검사하지 않는다.
FOREIGN_KEYS = [
    ("scenario_selection", "scenario_id", "scenarios", "scenario_id"),
    ("scenario_selection", "cand_id", "candidates", "cand_id"),
    ("route_benefit", "scenario_id", "scenarios", "scenario_id"),
    ("route_benefit", "route_id", "routes", "route_id"),
    ("route_benefit", "start_cand", "candidates", "cand_id"),
    ("route_benefit", "end_cand", "candidates", "cand_id"),
    ("existing_stations", "cand_id", "candidates", "cand_id"),
    ("existing_stations", "sgg_code", "districts", "sgg_code"),
    ("candidates", "sgg_code", "districts", "sgg_code"),
]
COND_COLS = ["mode", "build_type", "walk_m", "trip_unit"]


# ---------------------------------------------------------------------------
# 시나리오 조건
# ---------------------------------------------------------------------------
def with_conditions(df):
    """조건 칼럼이 없으면 config 기본값으로 채우고 cond_id를 붙인다 (6단계 산출물에는 mode만 있다)."""
    df = df.copy()
    defaults = {"build_type": C.S8_BUILD_TYPE_DEFAULT, "walk_m": int(C.S4_WALK_M), "trip_unit": C.S8_TRIP_UNIT_DEFAULT}
    for col, val in defaults.items():
        if col not in df.columns:
            df[col] = val
        else:
            df[col] = df[col].fillna(val)
    df["walk_m"] = df["walk_m"].astype("int64")
    df["cond_id"] = (df["mode"].astype(str) + "-" + df["build_type"].astype(str) + "-w" + df["walk_m"].astype(str)
                     + "-" + df["trip_unit"].astype(str))
    return df


def with_scenario_id(df):
    df = with_conditions(df)
    df["scenario_id"] = df["cond_id"] + "-p" + df["p"].astype("int64").astype(str)
    return df


# ---------------------------------------------------------------------------
# 테이블 만들기
# ---------------------------------------------------------------------------
def build_districts(sgg, lines, cands):
    """
    연구 지역 구 경계 + 구별 통계.
    sgg: region_density.load_sgg() 결과 중 연구 지역 [sgg, sgg_name, area_km2, geometry] (EPSG:5179)
    lines: 대표경로 선 (shapely 배열), cands: sgg_code가 붙은 후보지
    """
    geoms = shapely.force_2d(sgg.geometry.to_numpy())
    geoms = np.array([g if g.geom_type == "MultiPolygon" else shapely.MultiPolygon([g]) for g in geoms], dtype=object)
    n_routes, route_km = [], []
    for g in geoms:
        inter = shapely.intersection(lines, g)
        length = shapely.length(inter)
        n_routes.append(int((length > 0).sum()))
        route_km.append(float(length.sum() / 1000))
    n_cands = cands.sgg_code.value_counts()
    d = pd.DataFrame({
        "sgg_code": sgg.sgg.astype(str).to_numpy(), "sgg_name": sgg.sgg_name.astype(str).to_numpy(),
        "area_km2": sgg.area_km2.to_numpy(dtype=float), "n_routes": n_routes, "route_km": route_km})
    d["n_candidates"] = d.sgg_code.map(n_cands).fillna(0).astype("int64")
    return gpd.GeoDataFrame(d, geometry=geoms, crs=C.CRS_WORK).rename_geometry("geom")


def assign_district(points, sgg):
    """점 -> 속한 구 코드. 경계 위 점은 코드가 작은 구로, 어느 구에도 없으면 None."""
    tree = shapely.STRtree(sgg.geometry.to_numpy())
    pi, gi = tree.query(points, predicate="intersects")
    codes = sgg.sgg.astype(str).to_numpy()
    hit = pd.DataFrame({"pi": pi, "code": codes[gi]}).sort_values(["pi", "code"]).drop_duplicates("pi")
    out = np.full(len(points), None, dtype=object)
    out[hit.pi.to_numpy()] = hit.code.to_numpy()
    return out


def build_candidates(cands, sgg, boot):
    pts = shapely.points(cands.x.to_numpy(), cands.y.to_numpy())
    df = pd.DataFrame({
        "cand_id": cands.cand_id.astype("int64").to_numpy(),
        "sgg_code": assign_district(pts, sgg),
        "n_routes": cands.n_routes.astype("int64").to_numpy(),
        "n_trips": cands.n_trips.astype("int64").to_numpy(),
        "is_existing": cands.is_existing.fillna(False).astype(bool).to_numpy() if "is_existing" in cands else False,
        "station_id": cands.station_id.to_numpy() if "station_id" in cands else None,
    })
    if boot is not None:
        df = df.merge(boot[["cand_id", "boot_freq"]], on="cand_id", how="left")
    else:
        df["boot_freq"] = np.nan
    return gpd.GeoDataFrame(df, geometry=pts, crs=C.CRS_WORK).rename_geometry("geom")


def build_existing(cand_gdf):
    ex = cand_gdf[cand_gdf.is_existing]
    return gpd.GeoDataFrame({"station_id": ex.station_id.to_numpy(), "cand_id": ex.cand_id.to_numpy(),
                             "sgg_code": ex.sgg_code.to_numpy()},
                            geometry=ex.geom.to_numpy(), crs=C.CRS_WORK).rename_geometry("geom")


def build_routes(lines, s2_routes, trip_route, journeys):
    """대표경로 선 + 품질 지표. obs_ratio는 경로에 묶인 여정들의 관측 시간 합 / 전체 시간 합."""
    tj = trip_route.merge(journeys[["journey_id", "obs_s", "span_s"]], left_on="trip_id", right_on="journey_id",
                          how="left", validate="one_to_one")
    if tj.span_s.isna().any():
        raise ValueError(f"[s8] 여정 정보가 없는 통행 {int(tj.span_s.isna().sum())}개 (s2_trip_route vs s0_journeys)")
    obs = tj.groupby("route_id")[["obs_s", "span_s"]].sum()
    obs_ratio = (obs.obs_s / obs.span_s).clip(upper=1.0)
    r = lines.merge(s2_routes[["route_id", "weight"]], on="route_id", how="left", validate="one_to_one")
    geom = shapely.from_wkb(r.geometry.to_numpy())
    df = pd.DataFrame({
        "route_id": r.route_id.astype("int64").to_numpy(),
        "weight": r.weight.astype("int64").to_numpy(),
        "route_len_m": r.route_len.to_numpy(dtype=float),
        "obs_ratio": r.route_id.map(obs_ratio).to_numpy(dtype=float),
        "repeat_ratio": r.repeat_ratio.to_numpy(dtype=float),
        "straight_ratio": np.where(r.route_len > 0, r.break_m / r.route_len.where(r.route_len > 0, 1), 0.0),
        "n_breaks": r.n_breaks.astype("int64").to_numpy(),
    })
    return gpd.GeoDataFrame(df, geometry=geom, crs=C.CRS_WORK).rename_geometry("geom")


def build_scenarios(stats, selected, s6_routes):
    """6단계(와 7단계가 같은 형식으로 더한) 실행 결과 -> scenarios, scenario_selection, route_benefit."""
    runs = pd.DataFrame([r for r in stats["runs"] if r.get("obj_eval") is not None])
    runs = with_scenario_id(runs)
    ub = stats.get("upper_bound", {})
    if "upper_bound" not in runs.columns:
        runs["upper_bound"] = np.nan
    runs["upper_bound"] = runs["upper_bound"].fillna(runs["mode"].map(ub))
    scen = pd.DataFrame({
        "scenario_id": runs.scenario_id, "cond_id": runs.cond_id, "mode": runs["mode"],
        "build_type": runs.build_type, "walk_m": runs.walk_m, "trip_unit": runs.trip_unit,
        "p": runs.p.astype("int64"), "objective": runs.obj_eval.astype(float),
        "upper_bound": runs.upper_bound.astype(float), "n_selected": runs.n_selected.astype("int64"),
        "n_routes_benefit": runs.n_routes_benefit.astype("int64"), "mip_gap": runs.mip_gap.astype(float),
        "status": runs.status.astype(str)})

    sel = with_scenario_id(selected)
    sel = pd.DataFrame({"scenario_id": sel.scenario_id, "cand_id": sel.cand_id.astype("int64"),
                        "used": sel.used.astype(bool)})

    rb = with_scenario_id(s6_routes)
    rb = rb[rb.benefit_m > 0]
    rb = pd.DataFrame({"scenario_id": rb.scenario_id, "route_id": rb.route_id.astype("int64"),
                       "benefit_m": rb.benefit_m.astype(float), "benefit": rb.benefit.astype(float),
                       "start_cand": rb.start_cand.astype("int64"), "end_cand": rb.end_cand.astype("int64")})
    return scen, sel, rb


def build_coverage(scen, coverage):
    """커버리지 곡선 = 7단계 곡선(p = 1~30) + 시나리오 행의 (조건, p, 목적값). 같은 (조건, p)는 7단계 값을 쓴다."""
    parts = []
    if coverage is not None:
        cv = with_conditions(coverage)
        if "upper_bound" not in cv.columns:
            cv["upper_bound"] = np.nan
        parts.append(cv)
    parts.append(scen)
    cols = list(SCHEMA["coverage_curve"])
    cv = pd.concat([p[cols] for p in parts], ignore_index=True).drop_duplicates(["cond_id", "p"], keep="first")
    # 7단계 곡선에 상한이 없으면 같은 조건 시나리오의 상한을 쓴다
    ub = scen.drop_duplicates("cond_id").set_index("cond_id").upper_bound
    cv["upper_bound"] = cv.upper_bound.fillna(cv.cond_id.map(ub))
    cv["p"] = cv.p.astype("int64")
    return cv.sort_values(["cond_id", "p"]).reset_index(drop=True)


def conform(df, name):
    """SCHEMA 순서·타입으로 맞춘다. 도형 칼럼은 그대로."""
    out = df[list(SCHEMA[name])].copy()
    for col, typ in SCHEMA[name].items():
        if col == "geom":
            continue
        if typ == "string":
            out[col] = out[col].astype("string")
        elif typ == "int64":
            out[col] = out[col].astype("int64")
        elif typ == "float64":
            out[col] = out[col].astype("float64")
        elif typ == "bool":
            out[col] = out[col].astype(bool)
    return out.reset_index(drop=True)


def build_tables(inp):
    """
    inp: dict — cands, s2_routes, trip_route, journeys, lines, selected, s6_routes, stats, sgg, boot(None 가능), coverage(None 가능)
    반환: dict 테이블 이름 -> DataFrame/GeoDataFrame (SCHEMA 순서·타입)
    """
    sgg = inp["sgg"]
    cand = build_candidates(inp["cands"], sgg, inp["boot"])
    routes = build_routes(inp["lines"], inp["s2_routes"], inp["trip_route"], inp["journeys"])
    scen, sel, rb = build_scenarios(inp["stats"], inp["selected"], inp["s6_routes"])
    t = {
        "candidates": cand,
        "scenarios": scen,
        "scenario_selection": sel,
        "routes": routes,
        "route_benefit": rb,
        "existing_stations": build_existing(cand),
        "coverage_curve": build_coverage(scen, inp["coverage"]),
        "districts": build_districts(sgg, routes.geom.to_numpy(), cand),
    }
    return {name: conform(df, name) for name, df in t.items()}


# ---------------------------------------------------------------------------
# 검증
# ---------------------------------------------------------------------------
def validate(t, rel_tol=None):
    """계약 검사. 틀린 것을 전부 모아 AssertionError로 던진다. 통과하면 검사 요약을 반환."""
    rel_tol = C.S8_CHECK_REL_TOL if rel_tol is None else rel_tol
    errs = []
    for name, cols in SCHEMA.items():
        if list(t[name].columns) != list(cols):
            errs.append(f"{name}: 칼럼 {list(t[name].columns)} != 계약 {list(cols)}")
        dup = t[name].duplicated(PRIMARY_KEYS[name]).sum()
        if dup:
            errs.append(f"{name}: 기본키 {PRIMARY_KEYS[name]} 중복 {dup}행")
        for col in PRIMARY_KEYS[name]:
            if t[name][col].isna().any():
                errs.append(f"{name}.{col}: 기본키에 NULL")
        if "geom" in cols:
            g = t[name]
            if g.crs is None or g.crs.to_epsg() != 5179:
                errs.append(f"{name}: 좌표계 {g.crs} (EPSG:5179여야 함)")
            geom = g.geom.to_numpy()
            if len(geom):
                if (~shapely.is_valid(geom)).any():
                    errs.append(f"{name}: 유효하지 않은 도형 {int((~shapely.is_valid(geom)).sum())}개")
                bad_type = set(shapely.get_type_id(geom)) - {shapely.GeometryType[cols['geom'].upper()].value}
                if bad_type:
                    errs.append(f"{name}: 도형 타입이 {cols['geom']}가 아님")
                if shapely.is_empty(geom).any():
                    errs.append(f"{name}: 빈 도형 {int(shapely.is_empty(geom).sum())}개")
    for tab, col, ref, rcol in FOREIGN_KEYS:
        v = t[tab][col].dropna()
        miss = ~v.isin(t[ref][rcol])
        if miss.any():
            errs.append(f"{tab}.{col} -> {ref}.{rcol}: 없는 값 {int(miss.sum())}개 (예: {v[miss].iloc[0]})")
    n_null_sgg = int(t["candidates"].sgg_code.isna().sum())
    if n_null_sgg:
        errs.append(f"candidates.sgg_code: 어느 구에도 속하지 않는 후보지 {n_null_sgg}개")

    scen = t["scenarios"].set_index("scenario_id")
    n_sel = t["scenario_selection"].groupby("scenario_id").size().reindex(scen.index, fill_value=0)
    bad = n_sel != scen.n_selected
    if bad.any():
        errs.append(f"선택 수 != n_selected: {list(scen.index[bad])[:5]}")
    rb = t["route_benefit"].merge(t["routes"][["route_id", "weight"]], on="route_id", how="left")
    obj = (rb.benefit * rb.weight).groupby(rb.scenario_id).sum().reindex(scen.index, fill_value=0.0)
    rel = (obj - scen.objective).abs() / scen.objective.abs().clip(lower=1e-12)
    rel[scen.objective == 0] = (obj - scen.objective).abs()[scen.objective == 0]
    if (rel > rel_tol).any():
        errs.append(f"sum(weight * benefit) != objective: 최대 상대차 {rel.max():.3e} ({rel.idxmax()})")
    nrb = t["route_benefit"].groupby("scenario_id").size().reindex(scen.index, fill_value=0)
    if (nrb != scen.n_routes_benefit).any():
        errs.append(f"혜택 경로 수 != n_routes_benefit: {list(scen.index[nrb != scen.n_routes_benefit])[:5]}")
    cv = t["coverage_curve"].merge(t["scenarios"][["cond_id", "p", "objective"]], on=["cond_id", "p"],
                                   suffixes=("", "_scen"))
    cv_rel = (cv.objective - cv.objective_scen).abs() / cv.objective_scen.abs().clip(lower=1e-12)
    if (cv_rel > rel_tol).any():
        errs.append(f"커버리지 곡선 목적값 != 시나리오 목적값: {int((cv_rel > rel_tol).sum())}개")
    if errs:
        raise AssertionError("[s8] 내보내기 검증 실패\n  " + "\n  ".join(errs))
    return {"objective_max_rel_diff": float(rel.max()) if len(rel) else 0.0,
            "coverage_checked_points": int(len(cv))}


# ---------------------------------------------------------------------------
# 입출력
# ---------------------------------------------------------------------------
def load_study_sgg():
    """연구 지역(STUDY_REGION) 구 경계 [sgg, sgg_name, area_km2, geometry] (EPSG:5179)."""
    import region_compare as RC
    import region_density as R
    sgg = R.load_sgg()
    return sgg[RC.in_region(sgg, C.STUDY_REGION)].sort_values("sgg").reset_index(drop=True)


def load_inputs():
    stats = json.loads(IN_S6_STATS.read_text(encoding="utf-8"))
    return {
        "cands": pd.read_parquet(IN_CANDS),
        "s2_routes": pd.read_parquet(IN_S2_ROUTES, columns=["route_id", "weight"]),
        "trip_route": pd.read_parquet(IN_TRIP_ROUTE),
        "journeys": pd.read_parquet(IN_JOURNEYS, columns=["journey_id", "obs_s", "span_s"]),
        "lines": pd.read_parquet(IN_LINES),
        "selected": pd.read_parquet(IN_SELECTED),
        "s6_routes": pd.read_parquet(IN_ROUTES),
        "stats": stats,
        "sgg": load_study_sgg(),
        "boot": pd.read_parquet(IN_BOOT) if IN_BOOT.exists() else None,
        "coverage": pd.read_parquet(IN_COVERAGE) if IN_COVERAGE.exists() else None,
    }


def pending_items(t, inp):
    """7단계 몫인데 아직 채워지지 않은 것 (manifest와 보고용)."""
    items = []
    if inp["boot"] is None:
        items.append("candidates.boot_freq: s7_bootstrap.parquet 없음 -> 전부 NULL")
    if inp["coverage"] is None:
        ps = sorted(t["coverage_curve"].p.unique().tolist())
        items.append(f"coverage_curve: s7_coverage.parquet 없음 -> 6단계 p {ps}만")
    if t["existing_stations"].empty:
        items.append("existing_stations: 기존 대여소 데이터 없음 (BIKE_EXISTING_STATIONS) -> 빈 테이블, "
                     "기준선(build_type=existing)·증설(expand) 시나리오 없음")
    conds = t["scenarios"].cond_id.unique().tolist()
    if t["scenarios"][["walk_m", "trip_unit", "build_type"]].drop_duplicates().shape[0] == 1:
        items.append(f"민감도: 도보 반경·통행 단위·설치 유형 조건이 1개뿐 ({len(conds)}개 조건 = 변형 수)")
    return items


def write_tables(t, meta):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, df in t.items():
        path = OUT_DIR / f"{name}.parquet"
        if isinstance(df, gpd.GeoDataFrame):
            df.to_parquet(path, index=False)            # GeoParquet (geo 메타데이터에 EPSG:5179)
        else:
            df.to_parquet(path, index=False)
    OUT_MANIFEST.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def print_report(meta):
    print(f"[s8] 내보내기 ({meta['out_dir']})")
    for name, info in meta["tables"].items():
        print(f"  {name:<20} {info['rows']:>7,}행")
    if meta["pending"]:
        print("[s8] 7단계 입력이 없어 비어 있는 것:")
        for s in meta["pending"]:
            print(f"  - {s}")


def run(save=True):
    t0 = time.perf_counter()
    inp = load_inputs()
    t = build_tables(inp)
    checks = validate(t)
    meta = {
        "created": pd.Timestamp.now(tz=C.TIMEZONE).isoformat(),
        "out_dir": str(OUT_DIR), "crs": C.CRS_WORK,
        "tables": {name: {"rows": int(len(df)),
                          "columns": {c: SCHEMA[name][c] for c in df.columns},
                          "primary_key": PRIMARY_KEYS[name]} for name, df in t.items()},
        "foreign_keys": [f"{a}.{b} -> {c}.{d}" for a, b, c, d in FOREIGN_KEYS],
        "checks": checks,
        "pending": pending_items(t, inp),
        "sources": {p.name: pd.Timestamp(p.stat().st_mtime, unit="s", tz="UTC").isoformat()
                    for p in (IN_CANDS, IN_S2_ROUTES, IN_TRIP_ROUTE, IN_JOURNEYS, IN_LINES, IN_SELECTED,
                              IN_ROUTES, IN_S6_STATS, IN_BOOT, IN_COVERAGE, C.ADMIN_BOUNDARY_PATH) if p.exists()},
        "config": {"S8_BUILD_TYPE_DEFAULT": C.S8_BUILD_TYPE_DEFAULT, "S4_WALK_M": C.S4_WALK_M,
                   "S8_TRIP_UNIT_DEFAULT": C.S8_TRIP_UNIT_DEFAULT, "S8_CHECK_REL_TOL": C.S8_CHECK_REL_TOL,
                   "STUDY_REGION": C.STUDY_REGION},
        "elapsed_s": round(time.perf_counter() - t0, 1),
    }
    if save:
        write_tables(t, meta)
    print_report(meta)
    return t, meta


def main(force=False):
    C.setup_environment()
    force = force or C.FORCE_RECOMPUTE
    if OUT_MANIFEST.exists() and not force:
        print(f"[s8] 기존 산출물이 있어 건너뜁니다 (--force 로 다시 내보냄). {OUT_MANIFEST}")
        print_report(json.loads(OUT_MANIFEST.read_text(encoding="utf-8")))
        return
    _, meta = run(save=True)
    print(f"[s8] 저장 완료 ({meta['elapsed_s']}초): {OUT_DIR}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="캐시 무시하고 다시 내보냄")
    main(force=ap.parse_args().force)
