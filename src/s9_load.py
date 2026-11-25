"""
9단계: PostGIS 적재

8단계 GeoParquet(data/output/export/)을 결과 DB의 bike 스키마에 넣는다. 적재 스크립트는 이것 하나다.

규칙
  - 테이블은 sql/schema.sql이 만든다. 이 스크립트는 테이블을 만들거나 고치지 않고 데이터만 넣는다 (to_postgis 쓰지 않음).
  - 넣기 전에 DB 테이블의 칼럼 이름·순서·타입·도형 타입·SRID가 8단계 계약(s8_export.SCHEMA)과 같은지 확인한다.
    다르면 아무것도 넣지 않고 멈춘다 -> schema.sql을 다시 실행하거나, 스키마를 바꾼 것이면 SCHEMA와 schema.sql을 함께 고친다.
  - 다시 실행하면 테이블을 비우고 새로 넣는다. 비우기부터 넣기·검사까지 한 트랜잭션이라, 중간에 실패하면 전의 데이터가 그대로 남고
    조회하는 쪽(Spring)은 반쯤 채워진 상태를 보지 않는다.
  - 도형은 EPSG:5179 그대로 넣는다 (EWKB). 4326 변환은 Spring 쿼리에서 한 번만 한다.

검사 (트랜잭션 안에서, 틀리면 롤백)
  테이블별 행 수 = manifest 행 수, 도형 SRID = 5179. 제약(기본키, 외래키, CHECK)은 DB가 넣을 때 검사한다.

입력  : data/output/export/{테이블}.parquet, manifest.json (8단계)
        환경변수 BIKE_RESULT_DB_URL (config.RESULT_DB_URL, 쓰기 가능한 계정)
출력  : bike 스키마 테이블 8개

실행
  python s9_load.py              # 계약 확인 -> 비우기 -> 넣기 -> 검사 -> 커밋
  python s9_load.py --check      # 계약 확인만 (넣지 않음)
"""
import argparse
import io
import json
import time

import geopandas as gpd
import pandas as pd
import psycopg2
import shapely
from psycopg2 import sql
from sqlalchemy.engine import make_url

import config as C
from s8_export import SCHEMA

EXPORT_DIR = C.S8_OUT_DIR
MANIFEST = EXPORT_DIR / "manifest.json"
DB_SCHEMA = C.RESULT_DB_SCHEMA
SRID = 5179
# 외래키가 가리키는 테이블부터 넣는다
LOAD_ORDER = ["districts", "candidates", "existing_stations", "routes",
              "scenarios", "scenario_selection", "route_benefit", "coverage_curve"]
PG_TYPES = {"int64": "bigint", "float64": "double precision", "string": "text", "bool": "boolean"}

assert sorted(LOAD_ORDER) == sorted(SCHEMA), "LOAD_ORDER와 s8_export.SCHEMA의 테이블이 다르다"


# ---------------------------------------------------------------------------
# 계약 확인
# ---------------------------------------------------------------------------
def expected_columns():
    """8단계 계약 -> {테이블: [(칼럼, DB 타입)]}. 도형은 'geometry(MULTIPOLYGON,5179)' 형식."""
    out = {}
    for name, cols in SCHEMA.items():
        out[name] = [(c, f"geometry({t.upper()},{SRID})" if c == "geom" else PG_TYPES[t]) for c, t in cols.items()]
    return out


def db_columns(cur):
    """DB bike 스키마 -> {테이블: [(칼럼, DB 타입)]} (칼럼 순서대로)."""
    cur.execute("""
        SELECT c.table_name, c.column_name, c.data_type, g.type, g.srid
        FROM information_schema.columns c
        LEFT JOIN geometry_columns g
               ON g.f_table_schema = c.table_schema AND g.f_table_name = c.table_name
              AND g.f_geometry_column = c.column_name
        WHERE c.table_schema = %(schema)s
        ORDER BY c.table_name, c.ordinal_position
    """, {"schema": DB_SCHEMA})
    out = {}
    for table, col, dtype, gtype, srid in cur.fetchall():
        typ = f"geometry({gtype},{srid})" if gtype is not None else dtype
        out.setdefault(table, []).append((col, typ))
    return out


def contract_diff(expected, actual):
    """계약과 DB의 차이 목록 (없으면 빈 리스트)."""
    errs = []
    for table, cols in expected.items():
        if table not in actual:
            errs.append(f"{DB_SCHEMA}.{table}: 테이블 없음")
        elif actual[table] != cols:
            errs.append(f"{DB_SCHEMA}.{table}: DB {actual[table]} != 계약 {cols}")
    return errs


def check_contract(cur):
    errs = contract_diff(expected_columns(), db_columns(cur))
    if errs:
        raise RuntimeError("[s9] DB 테이블이 8단계 계약과 다릅니다. 넣지 않고 멈춥니다.\n  " + "\n  ".join(errs)
                           + "\n  -> sql/schema.sql을 다시 실행하거나, 스키마를 바꾼 것이면 s8_export.SCHEMA와 schema.sql을 함께 고치세요.")


# ---------------------------------------------------------------------------
# 읽기와 넣기
# ---------------------------------------------------------------------------
def read_export():
    """8단계 산출물 -> ({테이블: DataFrame}, manifest). 파일 칼럼과 행 수가 계약·manifest와 같은지 확인한다."""
    if not MANIFEST.exists():
        raise FileNotFoundError(f"[s9] 8단계 산출물이 없습니다: {MANIFEST} (python s8_export.py 먼저)")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    tables = {}
    for name, cols in SCHEMA.items():
        path = EXPORT_DIR / f"{name}.parquet"
        df = gpd.read_parquet(path) if "geom" in cols else pd.read_parquet(path)
        if list(df.columns) != list(cols):
            raise ValueError(f"[s9] {path.name} 칼럼 {list(df.columns)} != 계약 {list(cols)} (s8_export.py --force로 다시 내보내기)")
        if len(df) != manifest["tables"][name]["rows"]:
            raise ValueError(f"[s9] {path.name} 행 수 {len(df)} != manifest {manifest['tables'][name]['rows']}")
        if "geom" in cols and len(df) and df.crs.to_epsg() != SRID:
            raise ValueError(f"[s9] {path.name} 좌표계 {df.crs} (EPSG:{SRID}여야 함)")
        tables[name] = df
    return tables, manifest


def to_copy_csv(df, name):
    """COPY ... FROM STDIN (FORMAT csv)용 버퍼. 빈 칸 = NULL, 도형 = EWKB 16진수 (SRID 포함)."""
    out = pd.DataFrame(df).copy()
    for col, typ in SCHEMA[name].items():
        if col == "geom":
            geom = shapely.set_srid(out[col].to_numpy(), SRID)
            out[col] = shapely.to_wkb(geom, hex=True, include_srid=True)
        elif typ == "string":
            s = out[col]
            if (s.dropna() == "").any():
                raise ValueError(f"[s9] {name}.{col}: 빈 문자열은 CSV에서 NULL과 구분되지 않는다")
            out[col] = s.astype(object).where(s.notna(), None)
    buf = io.StringIO()
    out.to_csv(buf, index=False, header=False, na_rep="")
    buf.seek(0)
    return buf


def qualified(name):
    return sql.SQL("{}.{}").format(sql.Identifier(DB_SCHEMA), sql.Identifier(name))


def load(conn, tables, manifest):
    """한 트랜잭션: 비우기 -> 넣기 -> 검사 -> 커밋. 실패하면 롤백."""
    counts = {}
    try:
        with conn.cursor() as cur:
            check_contract(cur)
            cur.execute(sql.SQL("TRUNCATE {}").format(sql.SQL(", ").join(qualified(n) for n in LOAD_ORDER)))
            for name in LOAD_ORDER:
                cols = sql.SQL(", ").join(sql.Identifier(c) for c in SCHEMA[name])
                cur.copy_expert(sql.SQL("COPY {} ({}) FROM STDIN WITH (FORMAT csv)").format(qualified(name), cols)
                                .as_string(conn), to_copy_csv(tables[name], name))
            errs = []
            for name in LOAD_ORDER:
                cur.execute(sql.SQL("SELECT count(*) FROM {}").format(qualified(name)))
                counts[name] = cur.fetchone()[0]
                if counts[name] != manifest["tables"][name]["rows"]:
                    errs.append(f"{name}: DB {counts[name]}행 != manifest {manifest['tables'][name]['rows']}행")
                if "geom" in SCHEMA[name]:
                    cur.execute(sql.SQL("SELECT DISTINCT ST_SRID(geom) FROM {}").format(qualified(name)))
                    srids = {r[0] for r in cur.fetchall()}
                    if srids - {SRID}:
                        errs.append(f"{name}: SRID {srids}")
            if errs:
                raise RuntimeError("[s9] 적재 뒤 검사 실패 (롤백)\n  " + "\n  ".join(errs))
            for name in LOAD_ORDER:
                cur.execute(sql.SQL("ANALYZE {}").format(qualified(name)))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return counts


def connect():
    if not C.RESULT_DB_URL:
        raise RuntimeError("[s9] 환경변수 BIKE_RESULT_DB_URL이 없습니다 "
                           "(예: postgresql+psycopg2://postgres:비밀번호@localhost:5432/bike_station)")
    u = make_url(C.RESULT_DB_URL)
    return psycopg2.connect(host=u.host, port=u.port or 5432, user=u.username, password=u.password, dbname=u.database)


def main(check_only=False):
    t0 = time.perf_counter()
    tables, manifest = read_export()
    conn = connect()
    try:
        if check_only:
            with conn.cursor() as cur:
                check_contract(cur)
            print(f"[s9] 계약 확인 통과: {DB_SCHEMA} 스키마 테이블 {len(SCHEMA)}개가 8단계 계약과 같습니다 (넣지 않음)")
            return
        counts = load(conn, tables, manifest)
    finally:
        conn.close()
    print(f"[s9] 적재 완료 ({time.perf_counter() - t0:.1f}초, 8단계 내보내기 {manifest['created']})")
    for name in LOAD_ORDER:
        print(f"  {DB_SCHEMA}.{name:<20} {counts[name]:>7,}행")
    if manifest.get("pending"):
        print("[s9] 참고: 8단계에서 비어 있던 것")
        for s in manifest["pending"]:
            print(f"  - {s}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="DB 테이블이 계약과 같은지만 확인")
    main(check_only=ap.parse_args().check)
