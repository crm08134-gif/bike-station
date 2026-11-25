"""
8단계 내보내기 검증 (합성 입력)

구 2개 (A: x 0~1000, B: x 1000~2000, y 0~1000), 후보지 4개, 대표경로 2개
  c0 (100, 500) A   c1 (900, 500) A   c2 (1100, 500) B   c3 (1900, 500) B (기존 대여소 ST1)
  R0 (w=2) (0,500)-(2000,500) 2000m, 여정 2개: 관측 50/100초, 100/100초 -> obs_ratio 0.75, 직선 이음 200m -> 0.1
  R1 (w=1) (0,100)-(800,100)   800m, 여정 1개: 90/100초 -> obs_ratio 0.9
시나리오: absolute p=2, 선택 c0, c3 -> R0 혜택 1800m, R1 0 -> 목적값 2 x 1800 = 3600

1) 테이블 만들기 + 계약 검사 통과, 칼럼 값 (obs_ratio, straight_ratio, 구 배정, 구별 통계)
2) 혜택 0 경로는 route_benefit에 없음, scenario_id는 조건 + p로 정해짐
3) 목적값·외래키·커버리지가 어긋나면 검증이 막음
4) 7단계 입력(부트스트랩, 커버리지 곡선)이 있으면 반영, 조건 칼럼이 있으면 기본값 대신 사용
5) GeoParquet 저장 -> 읽기: 좌표계 EPSG:5179, 칼럼 순서·타입 유지

실행: python tests/test_s8.py
"""
import copy
import os
import sys
import tempfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely

os.environ["BIKE_BASE_DIR"] = str(Path(tempfile.mkdtemp(prefix="s8_test_")))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import config as C  # noqa: E402
import s8_export as S8  # noqa: E402

COND = f"absolute-new-w{C.S4_WALK_M}-{C.S8_TRIP_UNIT_DEFAULT}"


def synthetic_inputs():
    sgg = gpd.GeoDataFrame({"sgg": ["11010", "11020"], "sgg_name": ["A구", "B구"], "area_km2": [1.0, 1.0]},
                           geometry=[shapely.box(0, 0, 1000, 1000), shapely.box(1000, 0, 2000, 1000)], crs=C.CRS_WORK)
    cands = pd.DataFrame({"cand_id": [0, 1, 2, 3], "x": [100.0, 900, 1100, 1900], "y": [500.0] * 4,
                          "n_routes": [2, 1, 1, 1], "n_trips": [3, 2, 2, 2],
                          "is_existing": [False, False, False, True], "station_id": [None, None, None, "ST1"]})
    lines = pd.DataFrame({
        "route_id": [0, 1], "route_len": [2000.0, 800.0], "n_breaks": [1, 0], "break_m": [200.0, 0.0],
        "repeat_ratio": [0.0, 0.1], "trim_start_m": [0.0, 0.0], "trim_end_m": [0.0, 0.0],
        "geometry": shapely.to_wkb([shapely.LineString([(0, 500), (2000, 500)]),
                                    shapely.LineString([(0, 100), (800, 100)])])})
    s2_routes = pd.DataFrame({"route_id": [0, 1], "weight": [2, 1]})
    trip_route = pd.DataFrame({"trip_id": ["t0", "t1", "t2"], "route_id": [0, 0, 1], "reversed": [False] * 3})
    journeys = pd.DataFrame({"journey_id": ["t0", "t1", "t2", "t_out"], "obs_s": [50.0, 100, 90, 10],
                             "span_s": [100.0, 100, 100, 10]})
    selected = pd.DataFrame({"mode": ["absolute"] * 2, "p": [2, 2], "cand_id": [0, 3], "used": [True, True]})
    s6_routes = pd.DataFrame({
        "mode": ["absolute"] * 2, "p": [2, 2], "route_id": [0, 1], "weight": [2.0, 1.0], "route_len": [2000.0, 800.0],
        "u": [1900.0, 0], "v": [100.0, 0], "g": [1800.0, 0], "benefit_m": [1800.0, 0], "benefit": [1800.0, 0],
        "start_cand": [0, -1], "end_cand": [3, -1]})
    stats = {"upper_bound": {"absolute": 3600.0, "ratio": 1.8},
             "runs": [{"mode": "absolute", "p": 2, "status": "optimal", "obj_eval": 3600.0, "mip_gap": 0.0,
                       "n_selected": 2, "n_routes_benefit": 1},
                      {"mode": "ratio", "p": 2, "status": "infeasible", "obj_eval": None, "mip_gap": None,
                       "n_selected": 0, "n_routes_benefit": 0}]}
    return {"cands": cands, "s2_routes": s2_routes, "trip_route": trip_route, "journeys": journeys, "lines": lines,
            "selected": selected, "s6_routes": s6_routes, "stats": stats, "sgg": sgg, "boot": None, "coverage": None}


def expect_fail(t, fragment):
    try:
        S8.validate(t)
    except AssertionError as e:
        assert fragment in str(e), f"다른 이유로 실패: {e}"
        return
    raise AssertionError(f"검증이 '{fragment}'를 잡아야 한다")


def test_build_and_values():
    t = S8.build_tables(synthetic_inputs())
    S8.validate(t)
    assert set(t) == set(S8.SCHEMA)
    for name, cols in S8.SCHEMA.items():
        assert list(t[name].columns) == list(cols), name
    r = t["routes"].set_index("route_id")
    assert abs(r.obs_ratio[0] - 0.75) < 1e-12 and abs(r.obs_ratio[1] - 0.9) < 1e-12
    assert abs(r.straight_ratio[0] - 0.1) < 1e-12 and r.straight_ratio[1] == 0
    assert r.weight.tolist() == [2, 1]
    c = t["candidates"].set_index("cand_id")
    assert c.sgg_code.tolist() == ["11010", "11010", "11020", "11020"]
    assert c.boot_freq.isna().all()
    d = t["districts"].set_index("sgg_code")
    assert d.n_candidates.tolist() == [2, 2]
    assert d.n_routes.tolist() == [2, 1]
    assert abs(d.route_km["11010"] - 1.8) < 1e-9 and abs(d.route_km["11020"] - 1.0) < 1e-9
    assert set(shapely.get_type_id(d.geom.to_numpy())) == {shapely.GeometryType.MULTIPOLYGON.value}
    ex = t["existing_stations"]
    assert ex.station_id.tolist() == ["ST1"] and ex.cand_id.tolist() == [3] and ex.sgg_code.tolist() == ["11020"]
    print("[ok] 테이블 만들기 + 칼럼 값")


def test_scenarios():
    t = S8.build_tables(synthetic_inputs())
    s = t["scenarios"]
    assert s.scenario_id.tolist() == [f"{COND}-p2"], "해가 없는 실행(obj_eval None)은 빠진다"
    assert s.cond_id.tolist() == [COND] and s.objective.tolist() == [3600.0] and s.upper_bound.tolist() == [3600.0]
    rb = t["route_benefit"]
    assert rb.route_id.tolist() == [0], "혜택 0 경로는 넣지 않는다"
    assert rb.start_cand.tolist() == [0] and rb.end_cand.tolist() == [3]
    assert sorted(t["scenario_selection"].cand_id) == [0, 3]
    cv = t["coverage_curve"]
    assert cv.p.tolist() == [2] and cv.objective.tolist() == [3600.0]
    print("[ok] 시나리오 id, 혜택 > 0만, 커버리지 = 시나리오 값")


def test_validation_catches():
    base = S8.build_tables(synthetic_inputs())
    t = copy.deepcopy(base)
    t["scenarios"].loc[0, "objective"] = 3000.0
    expect_fail(t, "sum(weight * benefit)")
    t = copy.deepcopy(base)
    t["scenario_selection"].loc[0, "cand_id"] = 99
    expect_fail(t, "scenario_selection.cand_id")
    t = copy.deepcopy(base)
    t["scenarios"].loc[0, "n_selected"] = 3
    expect_fail(t, "n_selected")
    t = copy.deepcopy(base)
    t["route_benefit"] = pd.concat([t["route_benefit"], t["route_benefit"]], ignore_index=True)
    expect_fail(t, "기본키")
    inp = synthetic_inputs()
    inp["coverage"] = pd.DataFrame({"mode": ["absolute"] * 3, "p": [1, 2, 3], "objective": [0.0, 3500.0, 3600.0]})
    expect_fail(S8.build_tables(inp), "커버리지")
    inp = synthetic_inputs()
    inp["cands"].loc[0, ["x", "y"]] = [5000.0, 5000.0]            # 어느 구에도 없음
    expect_fail(S8.build_tables(inp), "sgg_code")
    print("[ok] 목적값·외래키·선택 수·기본키·커버리지·구 배정 어긋남을 막음")


def test_step7_inputs():
    inp = synthetic_inputs()
    inp["boot"] = pd.DataFrame({"cand_id": [0, 3], "boot_freq": [0.9, 0.4]})
    inp["coverage"] = pd.DataFrame({"mode": ["absolute"] * 3, "p": [1, 2, 3], "objective": [0.0, 3600.0, 3600.0]})
    t = S8.build_tables(inp)
    S8.validate(t)
    bf = t["candidates"].set_index("cand_id").boot_freq
    assert bf[0] == 0.9 and bf[3] == 0.4 and bf[[1, 2]].isna().all()
    cv = t["coverage_curve"]
    assert cv.p.tolist() == [1, 2, 3] and (cv.upper_bound == 3600.0).all(), "곡선 상한은 같은 조건 시나리오 값"
    assert S8.pending_items(t, inp) == [
        f"민감도: 도보 반경·통행 단위·설치 유형 조건이 1개뿐 (1개 조건 = 변형 수)"]

    # 민감도 결과가 조건 칼럼을 담아 오면 기본값 대신 쓴다 (같은 구조에 행만 추가)
    inp = synthetic_inputs()
    extra = {"build_type": "new", "walk_m": 200, "trip_unit": "journey_gap20m"}
    run = dict(inp["stats"]["runs"][0], **extra)
    inp["stats"]["runs"].append(run)
    inp["selected"] = pd.concat([inp["selected"], inp["selected"].assign(**extra)], ignore_index=True)
    inp["s6_routes"] = pd.concat([inp["s6_routes"], inp["s6_routes"].assign(**extra)], ignore_index=True)
    t = S8.build_tables(inp)
    S8.validate(t)
    assert sorted(t["scenarios"].scenario_id) == sorted([f"{COND}-p2", "absolute-new-w200-journey_gap20m-p2"])
    assert len(t["scenario_selection"]) == 4 and len(t["route_benefit"]) == 2
    print("[ok] 부트스트랩·커버리지 곡선 반영, 민감도 조건은 행으로 추가")


def test_roundtrip():
    t = S8.build_tables(synthetic_inputs())
    S8.write_tables(t, {"test": True})
    for name, cols in S8.SCHEMA.items():
        path = S8.OUT_DIR / f"{name}.parquet"
        if "geom" in cols:
            g = gpd.read_parquet(path)
            assert g.crs.to_epsg() == 5179 and g.geometry.name == "geom", name
        else:
            g = pd.read_parquet(path)
        assert list(g.columns) == list(cols), name
        assert len(g) == len(t[name]), name
    s = pd.read_parquet(S8.OUT_DIR / "scenarios.parquet")
    assert str(s.p.dtype) == "int64" and str(s.walk_m.dtype) == "int64"
    print("[ok] GeoParquet 저장·읽기 (EPSG:5179, 칼럼 순서)")


if __name__ == "__main__":
    test_build_and_values()
    test_scenarios()
    test_validation_catches()
    test_step7_inputs()
    test_roundtrip()
    print("전부 통과")
