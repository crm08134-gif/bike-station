"""
9단계 적재 스크립트 검증 (DB 없이)

1) 계약 -> DB 타입: int64 bigint, float64 double precision, string text, bool boolean, geom geometry(타입,5179)
2) 계약 비교: 테이블 없음, 칼럼 순서·타입·SRID가 다르면 잡는다
3) COPY용 CSV: 빈 칸 = NULL, 도형은 SRID 5179 EWKB로 되돌렸을 때 같은 도형, 빈 문자열은 거부
4) sql/schema.sql의 CREATE TABLE 칼럼 순서가 계약과 같다 (DB 없이 파일로 확인)

실행: python tests/test_s9.py
"""
import csv
import os
import re
import sys
import tempfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely

os.environ["BIKE_BASE_DIR"] = str(Path(tempfile.mkdtemp(prefix="s9_test_")))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import s8_export as S8  # noqa: E402
import s9_load as S9  # noqa: E402


def test_expected_columns():
    exp = S9.expected_columns()
    assert set(exp) == set(S8.SCHEMA)
    cand = dict(exp["candidates"])
    assert cand["cand_id"] == "bigint" and cand["boot_freq"] == "double precision"
    assert cand["sgg_code"] == "text" and cand["is_existing"] == "boolean"
    assert cand["geom"] == "geometry(POINT,5179)"
    assert dict(exp["districts"])["geom"] == "geometry(MULTIPOLYGON,5179)"
    assert [c for c, _ in exp["routes"]] == list(S8.SCHEMA["routes"])
    print("[ok] 계약 -> DB 타입")


def test_contract_diff():
    exp = S9.expected_columns()
    assert S9.contract_diff(exp, {k: list(v) for k, v in exp.items()}) == []
    act = {k: list(v) for k, v in exp.items() if k != "routes"}
    assert any("routes: 테이블 없음" in e for e in S9.contract_diff(exp, act))
    act = {k: list(v) for k, v in exp.items()}
    act["scenarios"][0], act["scenarios"][1] = act["scenarios"][1], act["scenarios"][0]      # 순서
    act["candidates"] = [(c, "geometry(POINT,4326)" if c == "geom" else t) for c, t in act["candidates"]]  # SRID
    act["route_benefit"] = [(c, "integer" if c == "route_id" else t) for c, t in act["route_benefit"]]    # 타입
    errs = S9.contract_diff(exp, act)
    assert len(errs) == 3 and all(any(n in e for e in errs) for n in ("scenarios", "candidates", "route_benefit"))
    print("[ok] 계약 비교 (테이블 없음, 순서, SRID, 타입)")


def test_copy_csv():
    pts = [shapely.Point(947300, 1937500), shapely.Point(1, 2)]
    df = gpd.GeoDataFrame({"cand_id": [0, 1], "sgg_code": pd.array(["11545", None], dtype="string"),
                           "n_routes": [1, 2], "n_trips": [3, 4], "is_existing": [False, True],
                           "station_id": pd.array([None, "ST1"], dtype="string"), "boot_freq": [float("nan"), 0.25]},
                          geometry=pts, crs="EPSG:5179").rename_geometry("geom")
    rows = list(csv.reader(S9.to_copy_csv(df, "candidates")))
    assert len(rows) == 2 and len(rows[0]) == len(S8.SCHEMA["candidates"])
    assert rows[0][1] == "11545" and rows[1][1] == "" and rows[0][5] == "" and rows[0][6] == ""
    assert rows[1][6] == "0.25" and rows[1][4] == "True"
    g = shapely.from_wkb(bytes.fromhex(rows[0][7]))
    assert shapely.get_srid(g) == 5179 and g.equals(pts[0])
    bad = df.copy()
    bad["station_id"] = pd.array(["", "ST1"], dtype="string")
    try:
        S9.to_copy_csv(bad, "candidates")
    except ValueError as e:
        assert "빈 문자열" in str(e)
    else:
        raise AssertionError("빈 문자열을 거부해야 한다")
    f = pd.DataFrame({"scenario_id": ["a-p1"], "route_id": [0], "benefit_m": [1234.5678901234567],
                      "benefit": [0.1 + 0.2], "start_cand": [1], "end_cand": [2]})
    r = list(csv.reader(S9.to_copy_csv(f, "route_benefit")))[0]
    assert float(r[2]) == 1234.5678901234567 and float(r[3]) == 0.1 + 0.2, "실수는 손실 없이 쓴다"
    print("[ok] COPY CSV (NULL, EWKB SRID 5179, 빈 문자열 거부, 실수 정밀도)")


def test_schema_sql_matches_contract():
    text = (ROOT / "sql" / "schema.sql").read_text(encoding="utf-8")
    text = re.sub(r"--[^\n]*", "", text)
    type_map = {"bigint": "int64", "double precision": "float64", "text": "string", "boolean": "bool"}
    for name, cols in S8.SCHEMA.items():
        m = re.search(rf"CREATE TABLE bike\.{name} \((.*?)\n\);", text, re.S)
        assert m, f"schema.sql에 {name} 없음"
        found = []
        for line in m.group(1).split("\n"):
            line = line.strip()
            if not line or line.split()[0] in ("PRIMARY", "UNIQUE"):
                continue
            col = line.split()[0]
            rest = line[len(col):].strip()
            gm = re.match(r"geometry\((\w+), 5179\)", rest)
            if gm:
                found.append((col, gm.group(1)))
            else:
                typ = next(t for t in type_map if rest.startswith(t))
                found.append((col, type_map[typ]))
        assert found == list(cols.items()), f"{name}: schema.sql {found} != 계약 {list(cols.items())}"
    print("[ok] schema.sql 칼럼 = 계약")


if __name__ == "__main__":
    test_expected_columns()
    test_contract_diff()
    test_copy_csv()
    test_schema_sql_matches_contract()
    print("전부 통과")
