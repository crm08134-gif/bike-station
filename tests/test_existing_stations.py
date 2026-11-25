"""
기존 대여소 적재 스크립트 검증 (DB·엑셀 없이)

1) 대여소 번호: 숫자 102 / 102.0 -> '102', 글자는 앞뒤 공백만 지움
2) 제외 규칙: 빈 칸 / 숫자 아님 / 0 / 서울 범위 밖을 사유별로 세고, 뒤바뀐 좌표는 고치지 않고 제외 + 개수만
3) 남은 행: 이름 공백 제거, EPSG:5179로 변환, 4326으로 되돌리면 원래 좌표
4) 대여소 번호가 겹치면 멈춘다
5) sql/existing_station_info.sql의 칼럼 순서·타입이 스크립트 계약(DB_COLUMNS)과 같다
6) 9단계(schema.sql DROP, s9_load TRUNCATE)가 이 테이블을 건드리지 않는다

실행: python tests/test_existing_stations.py
"""
import os
import re
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

os.environ["BIKE_BASE_DIR"] = str(Path(tempfile.mkdtemp(prefix="existing_test_")))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import load_existing_stations as L  # noqa: E402


def frame(rows):
    df = pd.DataFrame(rows, columns=["station_id", "station_name", "gu_name", "lat", "lon"])
    for c in L.COLUMNS:
        if c not in df:
            df[c] = None
    return df[L.COLUMNS]


def test_station_id():
    assert L.to_station_id(102) == "102"
    assert L.to_station_id(102.0) == "102"
    assert L.to_station_id(" ST-01 ") == "ST-01"
    assert L.to_station_id(np.nan) is None and L.to_station_id("  ") is None
    print("[ok] 대여소 번호 변환")


def test_exclusion():
    df = frame([
        (1, " 망원역 1번출구 앞", "마포구", 37.5556488, 126.91062927),   # 정상
        (2, "빈칸", "마포구", None, 126.9),                            # 빈 칸
        (3, "글자", "마포구", "37.5a", 126.9),                         # 숫자 아님
        (4, "영", "마포구", 0, 0),                                      # 0
        (5, "뒤바뀜", "마포구", 126.91, 37.55),                         # 범위 밖 (뒤바뀐 것으로 보임)
        (6, "부산", "해운대구", 35.16, 129.16),                         # 범위 밖
        (7, "경계", "강남구", 37.7, 127.3),                             # 경계값은 포함
    ])
    gdf, excluded, n_swapped = L.clean(df)
    assert excluded == {"위경도 빈 칸": 1, "위경도가 숫자가 아님": 1, "위경도 0": 1, "서울 범위 밖": 2}, excluded
    assert n_swapped == 1
    assert gdf.station_id.tolist() == ["1", "7"]
    assert gdf.station_name.tolist() == ["망원역 1번출구 앞", "경계"]
    assert gdf.crs.to_epsg() == 5179 and gdf.geometry.name == "geom"
    back = gdf.to_crs(4326)
    assert abs(back.geom.iloc[0].y - 37.5556488) < 1e-9 and abs(back.geom.iloc[0].x - 126.91062927) < 1e-9
    print("[ok] 제외 규칙, 좌표 변환")


def test_duplicate_id_stops():
    df = frame([(1, "a", "마포구", 37.55, 126.91), (1.0, "b", "마포구", 37.56, 126.92)])
    try:
        L.clean(df)
    except ValueError as e:
        assert "중복" in str(e)
    else:
        raise AssertionError("번호 중복인데 멈추지 않았다")
    print("[ok] 번호 중복이면 멈춤")


def test_sql_matches_contract():
    text = (ROOT / "sql" / "existing_station_info.sql").read_text(encoding="utf-8")
    body = re.search(r"CREATE TABLE bike\.existing_station_info \((.*?)\n\);", text, re.S).group(1)
    found = []
    for line in body.splitlines():
        line = line.split("--")[0].strip().rstrip(",")
        if not line:
            continue
        col, rest = line.split(None, 1)
        gm = re.match(r"geometry\((\w+), (\d+)\)", rest)
        found.append((col, f"geometry({gm.group(1).upper()},{gm.group(2)})" if gm else rest.split()[0]))
    assert found == L.DB_COLUMNS, f"sql {found} != 계약 {L.DB_COLUMNS}"
    assert "USING gist (geom)" in text and "GRANT SELECT ON bike.existing_station_info TO bike_reader" in text
    print("[ok] existing_station_info.sql 칼럼 = 계약, GIST 인덱스, bike_reader 권한")


def test_stage9_leaves_table_alone():
    """9단계가 이 테이블을 지우거나 비우지 않는다: s9_load의 TRUNCATE 대상(LOAD_ORDER)과 schema.sql의 DROP에 없다."""
    import s9_load as S9
    assert L.TABLE not in S9.LOAD_ORDER
    schema = (ROOT / "sql" / "schema.sql").read_text(encoding="utf-8")
    drop = re.search(r"DROP TABLE IF EXISTS (.*?);", schema, re.S).group(1)
    assert f"bike.{L.TABLE}" not in drop and "DROP SCHEMA" not in schema
    print("[ok] 9단계 TRUNCATE·DROP 대상이 아님")


if __name__ == "__main__":
    test_station_id()
    test_exclusion()
    test_duplicate_id_stops()
    test_sql_matches_contract()
    test_stage9_leaves_table_alone()
    print("전부 통과")
