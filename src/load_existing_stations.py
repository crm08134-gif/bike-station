"""
기존 따릉이 대여소 위치 적재 (지도 표시 전용)

서울시 공공자전거 대여소 정보 엑셀 -> 정제 -> GeoDataFrame(EPSG:4326) -> EPSG:5179 -> GeoParquet -> PostGIS
bike.existing_station_info. 분석 단계(1~8단계)와 9단계 결과 테이블은 건드리지 않는다.

엑셀 구조 (26.6월 기준 파일에서 확인)
  시트 '대여소현황', 1~5행은 병합된 다중 헤더라 pandas가 칼럼 이름을 제대로 만들지 못한다.
  -> 헤더 없이(header=None) 6행부터 읽고 칼럼 이름은 아래 COLUMNS로 직접 붙인다.
  -> 대신 헤더 칸 몇 개(HEADER_CHECK)가 기대한 글자인지 먼저 확인한다. 양식이 바뀌어 칼럼이 밀렸으면 여기서 멈춘다.

정제 규칙
  - 제외: 위경도 빈 칸 / 숫자가 아님 / 0 / 서울 범위 밖 (config.EXISTING_STATIONS_LAT_RANGE, _LON_RANGE).
    좌표는 고치지 않는다. 위도·경도가 뒤바뀐 것으로 보이는 행도 바꾸지 않고 제외하며 개수만 알려준다.
  - 대여소 번호가 비었거나 겹치면 기본키로 쓸 수 없으므로 넣지 않고 멈춘다.
  - 대여소 이름은 앞뒤 공백만 지운다 (원본 엑셀에 ' 망원역 1번출구 앞'처럼 앞 공백이 많다).

DB 적재 (s9_load.py와 같은 방식)
  - 테이블은 sql/existing_station_info.sql이 만든다. 이 스크립트는 칼럼이 맞는지 확인하고 데이터만 넣는다.
  - 다시 실행하면 비우고 새로 넣는다. 비우기부터 넣기·검사까지 한 트랜잭션이라 실패하면 전의 데이터가 남는다.
  - 값은 파라미터 바인딩으로만 넘긴다. 도형은 EPSG:5179 EWKB (4326 변환은 Spring 쿼리에서 한다).

입력  : config.EXISTING_STATIONS_XLSX, 환경변수 BIKE_RESULT_DB_URL (쓰기 가능한 계정)
출력  : data/output/existing_station_info.parquet, bike.existing_station_info

실행
  python load_existing_stations.py           # 읽기 -> 정제 -> parquet -> DB
  python load_existing_stations.py --no-db   # parquet까지만 (DB 없이 정제 결과 확인)
"""
import argparse
import numbers
import time

import geopandas as gpd
import pandas as pd
import psycopg2
import shapely
from psycopg2 import sql
from psycopg2.extras import execute_values
from sqlalchemy.engine import make_url

import config as C

SRID = 5179
DB_SCHEMA = C.RESULT_DB_SCHEMA
TABLE = C.EXISTING_STATIONS_TABLE

# 엑셀 A~J열 (K열은 시트 범위에만 있고 값이 없다)
COLUMNS = ["station_id",   # A 대여소 번호
           "station_name", # B 보관소(대여소)명
           "gu_name",      # C 소재지(위치) > 자치구
           "address",      # D 소재지(위치) > 상세주소
           "lat",          # E 소재지(위치) > 위도
           "lon",          # F 소재지(위치) > 경도
           "installed",    # G 설치 시기
           "lcd_docks",    # H 설치형태 > LCD > 거치대수
           "qr_docks",     # I 설치형태 > QR > 거치대수
           "op_type"]      # J 운영 방식
# (헤더 행 번호 0부터, 열 번호 0부터) -> 기대하는 글자 (공백·줄바꿈 무시)
HEADER_CHECK = {(0, 0): "대여소번호", (0, 1): "보관소(대여소)명", (2, 2): "자치구", (2, 4): "위도", (2, 5): "경도"}

# DB 테이블 계약: (칼럼, 타입) 순서대로. sql/existing_station_info.sql과 같아야 한다
DB_COLUMNS = [("station_id", "text"), ("station_name", "text"), ("gu_name", "text"),
              ("geom", f"geometry(POINT,{SRID})")]


# ---------------------------------------------------------------------------
# 읽기
# ---------------------------------------------------------------------------
def check_header(path):
    """헤더 칸이 기대한 글자인지 확인. 다르면 칼럼이 밀린 것이므로 멈춘다."""
    head = pd.read_excel(path, sheet_name=C.EXISTING_STATIONS_SHEET, header=None,
                         nrows=C.EXISTING_STATIONS_HEADER_ROWS, dtype=object)
    errs = []
    for (r, c), want in HEADER_CHECK.items():
        got = head.iat[r, c] if r < head.shape[0] and c < head.shape[1] else None
        if "".join(str(got).split()) != want:
            errs.append(f"{r + 1}행 {chr(ord('A') + c)}열: {got!r} (기대: {want!r})")
    if errs:
        raise ValueError("[existing] 엑셀 헤더가 예상과 다릅니다 (양식이 바뀌었으면 COLUMNS를 고치세요)\n  "
                         + "\n  ".join(errs))


def read_xlsx(path):
    """헤더를 건너뛰고 A~J열을 읽어 COLUMNS 이름을 붙인다. 타입은 pandas 추론 그대로 (밀림 확인용)."""
    check_header(path)
    df = pd.read_excel(path, sheet_name=C.EXISTING_STATIONS_SHEET, header=None,
                       skiprows=C.EXISTING_STATIONS_HEADER_ROWS, usecols="A:J", names=COLUMNS)
    blank = df.isna().all(axis=1)
    if blank.any():
        print(f"[existing] 완전히 빈 행 {int(blank.sum())}개는 대여소가 아니므로 셈에서 뺍니다")
        df = df[~blank].reset_index(drop=True)
    return df


def to_station_id(v):
    """대여소 번호 -> 문자열. 엑셀이 숫자로 저장한 102(또는 102.0)는 '102'로, 글자는 앞뒤 공백만 지운다."""
    if pd.isna(v):
        return None
    if isinstance(v, numbers.Number) and float(v).is_integer():
        return str(int(v))
    s = str(v).strip()
    return s or None


# ---------------------------------------------------------------------------
# 정제
# ---------------------------------------------------------------------------
def exclusion_reason(df):
    """행마다 제외 사유 (제외하지 않으면 None). 앞의 사유가 먼저 붙는다."""
    lat = pd.to_numeric(df.lat, errors="coerce")
    lon = pd.to_numeric(df.lon, errors="coerce")
    (lat_lo, lat_hi), (lon_lo, lon_hi) = C.EXISTING_STATIONS_LAT_RANGE, C.EXISTING_STATIONS_LON_RANGE
    in_seoul = lat.between(lat_lo, lat_hi) & lon.between(lon_lo, lon_hi)
    reason = pd.Series(None, index=df.index, dtype=object)
    rules = [("위경도 빈 칸", df.lat.isna() | df.lon.isna()),
             ("위경도가 숫자가 아님", lat.isna() | lon.isna()),
             ("위경도 0", (lat == 0) | (lon == 0)),
             ("서울 범위 밖", ~in_seoul)]
    for name, mask in rules:
        reason = reason.where(reason.notna() | ~mask, name)
    # 참고용: 범위 밖 중 위도·경도를 바꾸면 서울 안에 들어오는 행 (고치지 않는다)
    swapped = (reason == "서울 범위 밖") & lon.between(lat_lo, lat_hi) & lat.between(lon_lo, lon_hi)
    return reason, lat, lon, int(swapped.sum())


def clean(df):
    """정제 -> (EPSG:5179 GeoDataFrame, 사유별 제외 수, 뒤바뀐 것으로 보이는 행 수)."""
    sid = df.station_id.map(to_station_id)
    if sid.isna().any():
        raise ValueError(f"[existing] 대여소 번호가 빈 행 {int(sid.isna().sum())}개 "
                         f"(엑셀 {(df.index[sid.isna()] + C.EXISTING_STATIONS_HEADER_ROWS + 1).tolist()[:10]}행)")
    dup = sid[sid.duplicated(keep=False)]
    if len(dup):
        raise ValueError(f"[existing] 대여소 번호 중복 {dup.nunique()}개: {sorted(dup.unique())[:10]}")

    reason, lat, lon, n_swapped = exclusion_reason(df)
    keep = reason.isna()
    name = df.station_name.map(lambda v: None if pd.isna(v) else (str(v).strip() or None))
    gu = df.gu_name.map(lambda v: None if pd.isna(v) else (str(v).strip() or None))
    gdf = gpd.GeoDataFrame(
        {"station_id": sid[keep].to_numpy(), "station_name": name[keep].to_numpy(), "gu_name": gu[keep].to_numpy()},
        geometry=gpd.points_from_xy(lon[keep], lat[keep]), crs=C.CRS_SRC,
    ).rename_geometry("geom").to_crs(C.CRS_WORK)
    return gdf, reason[~keep].value_counts().to_dict(), n_swapped


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------
def qualified():
    return sql.SQL("{}.{}").format(sql.Identifier(DB_SCHEMA), sql.Identifier(TABLE))


def check_table(cur):
    """DB 테이블 칼럼 이름·순서·타입·도형 타입·SRID가 DB_COLUMNS와 같은지. 다르면 넣지 않고 멈춘다."""
    cur.execute("""
        SELECT c.column_name, c.data_type, g.type, g.srid
        FROM information_schema.columns c
        LEFT JOIN geometry_columns g
               ON g.f_table_schema = c.table_schema AND g.f_table_name = c.table_name
              AND g.f_geometry_column = c.column_name
        WHERE c.table_schema = %(schema)s AND c.table_name = %(table)s
        ORDER BY c.ordinal_position
    """, {"schema": DB_SCHEMA, "table": TABLE})
    actual = [(col, f"geometry({gtype},{srid})" if gtype is not None else dtype)
              for col, dtype, gtype, srid in cur.fetchall()]
    if actual != DB_COLUMNS:
        raise RuntimeError(f"[existing] {DB_SCHEMA}.{TABLE}: DB {actual or '테이블 없음'} != 기대 {DB_COLUMNS}\n"
                           f"  -> sql/existing_station_info.sql을 실행하세요")


def load(conn, gdf):
    """한 트랜잭션: 확인 -> 비우기 -> 넣기 -> 검사 -> 커밋. 실패하면 롤백. 적재 행 수를 돌려준다."""
    ewkb = shapely.to_wkb(shapely.set_srid(gdf.geom.to_numpy(), SRID), include_srid=True)
    rows = [(s, n, g, psycopg2.Binary(b))
            for s, n, g, b in zip(gdf.station_id, gdf.station_name, gdf.gu_name, ewkb)]
    try:
        with conn.cursor() as cur:
            check_table(cur)
            cur.execute(sql.SQL("TRUNCATE {}").format(qualified()))
            execute_values(cur,
                           sql.SQL("INSERT INTO {} (station_id, station_name, gu_name, geom) VALUES %s")
                           .format(qualified()).as_string(conn),
                           rows, template="(%s, %s, %s, ST_GeomFromEWKB(%s))", page_size=1000)
            cur.execute(sql.SQL("SELECT count(*), array_agg(DISTINCT ST_SRID(geom)) FROM {}").format(qualified()))
            n, srids = cur.fetchone()
            if n != len(gdf) or set(srids or []) - {SRID}:
                raise RuntimeError(f"[existing] 적재 뒤 검사 실패 (롤백): DB {n}행 / 넣은 {len(gdf)}행, SRID {srids}")
            cur.execute(sql.SQL("ANALYZE {}").format(qualified()))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return n


def connect():
    if not C.RESULT_DB_URL:
        raise RuntimeError("[existing] 환경변수 BIKE_RESULT_DB_URL이 없습니다 "
                           "(예: postgresql+psycopg2://postgres:비밀번호@localhost:5432/bike_station)")
    u = make_url(C.RESULT_DB_URL)
    return psycopg2.connect(host=u.host, port=u.port or 5432, user=u.username, password=u.password, dbname=u.database)


def main(no_db=False):
    t0 = time.perf_counter()
    C.setup_environment()
    path = C.EXISTING_STATIONS_XLSX
    raw = read_xlsx(path)
    print(f"[existing] 읽음: {path.name} / 시트 '{C.EXISTING_STATIONS_SHEET}' / "
          f"{C.EXISTING_STATIONS_HEADER_ROWS + 1}행부터")
    with pd.option_context("display.width", 200, "display.max_columns", None):
        print("[existing] 앞 3행 (정제 전)")
        print(raw.head(3).to_string())
        print("[existing] 칼럼 dtype")
        print(raw.dtypes.to_string())

    gdf, excluded, n_swapped = clean(raw)
    gdf.to_parquet(C.EXISTING_STATIONS_PARQUET)

    n_loaded = None
    if not no_db:
        conn = connect()
        try:
            n_loaded = load(conn, gdf)
        finally:
            conn.close()

    print(f"[existing] 엑셀 행 수      {len(raw):>7,}")
    print(f"[existing] 제외 행 수      {sum(excluded.values()):>7,}")
    for name, n in excluded.items():
        print(f"             - {name}: {n:,}")
    if n_swapped:
        print(f"             (서울 범위 밖 중 위도·경도가 뒤바뀐 것으로 보이는 행 {n_swapped:,}개, 고치지 않음)")
    print(f"[existing] 정제 후 행 수   {len(gdf):>7,} -> {C.EXISTING_STATIONS_PARQUET}")
    if n_loaded is None:
        print("[existing] DB 적재 건너뜀 (--no-db)")
    else:
        print(f"[existing] 적재 행 수      {n_loaded:>7,} -> {DB_SCHEMA}.{TABLE}")
    print(f"[existing] {time.perf_counter() - t0:.1f}초")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-db", action="store_true", help="parquet까지만 만들고 DB에는 넣지 않는다")
    main(no_db=ap.parse_args().no_db)
