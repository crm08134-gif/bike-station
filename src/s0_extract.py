"""
0단계: 데이터 준비

입력  : DB 테이블 track_point 또는 그 덤프 파일
        (session_id, ts[epoch ms], geom[GEOGRAPHY POINT 4326], model_version)
출력  : data/interim/s0_points.parquet  정제된 포인트 (남은 통행만)
        data/interim/s0_trips.parquet   모든 통행의 요약과 제외 사유
        data/interim/s0_stats.json      정제 로그와 핵심 통계

처리 순서 (순서가 결과에 영향을 주므로 바꾸지 말 것)
  1. 기간·모델버전·영역 필터
  2. (session_id, ts) 정렬, 중복 제거
  3. 좌표 변환 4326 -> 5179
  4. 통행 분할: 시간 공백이 SPLIT_GAP_S를 넘으면 별개 통행
  5. 튐 포인트 제거 (통행 내부에서, 앵커 기반 전후방 필터)
  6. 통행 지표 재계산 (포인트 수, 거리, 시간, 평균속도)
  7. 통행 단위 제외 규칙 적용

실행
  python s0_extract.py            # 캐시가 있으면 건너뜀
  python s0_extract.py --force    # 강제 재계산
  python s0_extract.py --probe    # 데이터 기간·규모만 빠르게 확인 (기간 정할 때 사용)
"""
import argparse
import json
import time

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from pyproj import Transformer

import config as C

# 산출물 경로
OUT_POINTS = C.DATA_INTERIM / "s0_points.parquet"
OUT_TRIPS = C.DATA_INTERIM / "s0_trips.parquet"
OUT_STATS = C.DATA_INTERIM / "s0_stats.json"

RAW_COLUMNS = ["session_id", "ts", "lon", "lat", "model_version"]


# ===========================================================================
# 1. 원본 읽기
# ===========================================================================
def _period_ms():
    """설정의 분석 기간(한국시간)을 epoch ms 정수 쌍으로 바꾼다."""
    start = pd.Timestamp(C.PERIOD_START, tz=C.TIMEZONE)
    end = pd.Timestamp(C.PERIOD_END, tz=C.TIMEZONE)
    return int(start.value // 10**6), int(end.value // 10**6)


# 기간 앞뒤로 여유를 두고 읽는다. 세션 단위 기간 판정은 파이썬에서 한다.
SQL_EXTRACT = """
SELECT session_id::text           AS session_id,
       ts                         AS ts,
       ST_X(geom::geometry)       AS lon,
       ST_Y(geom::geometry)       AS lat,
       model_version              AS model_version
FROM track_point
WHERE ts >= :t0 AND ts < :t1
"""

SQL_PROBE = """
SELECT to_char(to_timestamp(ts / 1000.0) AT TIME ZONE 'Asia/Seoul', 'YYYY-MM-DD') AS day,
       model_version,
       count(*)                   AS n_points,
       count(DISTINCT session_id) AS n_sessions
FROM track_point
GROUP BY 1, 2
ORDER BY 1, 2
"""


def _raw_cache_path():
    return C.DATA_RAW / f"track_point_{C.PERIOD_START}_{C.PERIOD_END}.parquet"


def _read_from_db(force):
    """
    DB에서 기간 데이터를 읽어 parquet으로 캐싱한다.

    반환: DataFrame [session_id(str), ts(int64), lon(float64), lat(float64), model_version(str)]
    """
    from sqlalchemy import create_engine, text

    cache = _raw_cache_path()
    if cache.exists() and not force:
        print(f"[s0] DB 추출 캐시 사용: {cache}")
        return pd.read_parquet(cache)

    if not C.DB_URL:
        raise RuntimeError("환경변수 BIKE_DB_URL이 없습니다. config.py의 DB_URL 설명을 참고하세요.")

    t0, t1 = _period_ms()
    margin = C.PERIOD_MARGIN_H * 3600 * 1000
    params = {"t0": t0 - margin, "t1": t1 + margin}

    schema = pa.schema([
        ("session_id", pa.string()), ("ts", pa.int64()),
        ("lon", pa.float64()), ("lat", pa.float64()), ("model_version", pa.string()),
    ])
    tmp = cache.with_suffix(".tmp")   # 중간에 끊기면 불완전한 캐시가 남지 않도록 임시 파일에 쓰고 마지막에 이름 변경
    engine = create_engine(C.DB_URL)
    n = 0
    with engine.connect().execution_options(stream_results=True) as conn, \
            pq.ParquetWriter(tmp, schema) as writer:
        for chunk in pd.read_sql(text(SQL_EXTRACT), conn, params=params, chunksize=C.DB_CHUNK_ROWS):
            chunk = chunk.astype({"session_id": str, "ts": "int64", "lon": "float64",
                                  "lat": "float64", "model_version": str})
            writer.write_table(pa.Table.from_pandas(chunk[RAW_COLUMNS], schema=schema, preserve_index=False))
            n += len(chunk)
            print(f"[s0] DB 읽는 중... {n:,}행")
    tmp.replace(cache)
    print(f"[s0] DB 추출 완료: {n:,}행 -> {cache}")
    return pd.read_parquet(cache)


def _geom_to_lonlat(geom):
    """
    geom 문자열 칼럼을 lon, lat 배열로 바꾼다.

    입력: pandas Series[str]. 아래 형식을 받는다.
          - WKT / EWKT : 'POINT(126.9 37.5)', 'SRID=4326;POINT(126.9 37.5)'  (DB를 ::text 없이 내보낸 경우)
          - WKB hex    : '0101000020E6100000...'                           (geom을 그대로 \\copy 한 경우)
    반환: (lon, lat) float64 numpy 배열
    SRID가 적혀 있는데 4326이 아니면 에러를 낸다 (좌표계를 추측하지 않는다).
    """
    import shapely

    s = geom.astype(str).str.strip()
    if s.str.fullmatch(r"[0-9A-Fa-f]+").all():
        g = shapely.from_wkb(s.to_numpy())
        srid = shapely.get_srid(g)
        srid = srid[srid != 0]                       # SRID 없는 WKB는 0
    else:
        # GEOS의 WKT 파서는 EWKT의 'SRID=xxxx;' 접두어를 받지 않으므로 떼어내서 따로 검사한다.
        parts = s.str.extract(r"^(?:SRID=(\d+);)?(.*)$")
        srid = parts[0].dropna().astype(int).unique()
        g = shapely.from_wkt(parts[1].to_numpy())
    bad = set(np.unique(srid).tolist()) - {int(C.CRS_SRC.split(":")[1])}
    if bad:
        raise ValueError(f"geom의 SRID가 {C.CRS_SRC}가 아닙니다: {sorted(bad)}")
    return shapely.get_x(g), shapely.get_y(g)


def _read_from_file():
    """
    덤프 파일(parquet 또는 csv)을 읽는다.

    입력: C.RAW_DUMP_PATH. 좌표는 아래 둘 중 하나로 받는다.
          - lon, lat 칼럼 (칼럼명 lng / latitude / longitude 도 받아준다)
          - geom 칼럼 (DB 테이블을 그대로 내보낸 경우. WKT/EWKT 또는 WKB hex 문자열)
    반환: DataFrame [session_id(str), ts(int64), lon(float64), lat(float64), model_version(str)]
    """
    path = C.RAW_DUMP_PATH
    if not path.exists():
        raise FileNotFoundError(f"덤프 파일이 없습니다: {path}")
    if path.suffix == ".csv":
        # 좌표는 float64로 명시해서 읽는다 (정밀도 보존). geom은 문자열 그대로 읽어 shapely로 파싱한다.
        df = pd.read_csv(path, dtype={"session_id": str, "model_version": str, "geom": str})
    else:
        df = pd.read_parquet(path)
    df = df.rename(columns={"lng": "lon", "longitude": "lon", "latitude": "lat"})
    if "geom" in df.columns and not {"lon", "lat"} <= set(df.columns):
        lon, lat = _geom_to_lonlat(df["geom"])
        df = df.assign(lon=lon, lat=lat)
    missing = set(RAW_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"덤프 파일에 필요한 칼럼이 없습니다: {missing}")
    return df[RAW_COLUMNS].astype({"session_id": str, "ts": "int64", "lon": "float64",
                                   "lat": "float64", "model_version": str})


def load_raw(force=False):
    """설정(S0_SOURCE)에 따라 DB 또는 파일에서 원본 포인트를 읽는다."""
    if C.S0_SOURCE == "db":
        return _read_from_db(force)
    if C.S0_SOURCE == "file":
        return _read_from_file()
    raise ValueError(f"S0_SOURCE는 'db' 또는 'file'이어야 합니다: {C.S0_SOURCE}")


def probe():
    """분석 기간을 정하기 위해 날짜·모델버전별 포인트 수와 세션 수를 출력한다."""
    if C.S0_SOURCE == "db":
        from sqlalchemy import create_engine, text
        with create_engine(C.DB_URL).connect() as conn:
            res = pd.read_sql(text(SQL_PROBE), conn)
    else:
        df = _read_from_file()
        df["day"] = pd.to_datetime(df.ts, unit="ms", utc=True).dt.tz_convert(C.TIMEZONE).dt.strftime("%Y-%m-%d")
        res = (df.groupby(["day", "model_version"])
                 .agg(n_points=("ts", "size"), n_sessions=("session_id", "nunique"))
                 .reset_index())
    print(res.to_string(index=False))
    return res


# ===========================================================================
# 2. 정제 단계별 함수
# ===========================================================================
def filter_scope(df, log):
    """
    기간·모델버전·영역 필터.
    기간은 '세션의 첫 포인트 시각' 기준으로 판정한다 (자정 걸친 통행이 잘리지 않도록).
    """
    log["n_points_input"] = len(df)
    log["n_sessions_input"] = int(df.session_id.nunique())

    df = df.dropna(subset=["session_id", "ts", "lon", "lat"])

    t0, t1 = _period_ms()
    first_ts = df.groupby("session_id").ts.transform("min")
    df = df[(first_ts >= t0) & (first_ts < t1)]
    log["n_sessions_in_period"] = int(df.session_id.nunique())

    log["model_version_counts"] = df.model_version.value_counts().to_dict()
    if C.MODEL_VERSIONS is not None:
        df = df[df.model_version.isin(C.MODEL_VERSIONS)]

    if C.BBOX_4326 is not None:
        lon0, lat0, lon1, lat1 = C.BBOX_4326
        df = df[df.lon.between(lon0, lon1) & df.lat.between(lat0, lat1)]

    log["n_points_after_scope"] = len(df)
    return df


def sort_dedup(df, log):
    """(session_id, ts) 정렬 후 같은 시각 중복 포인트 제거. DB의 PK상 원래 없어야 정상."""
    df = df.sort_values(["session_id", "ts"], kind="mergesort")
    before = len(df)
    df = df.drop_duplicates(["session_id", "ts"], keep="first")
    log["n_dup_points_removed"] = before - len(df)
    return df.reset_index(drop=True)


def project(df):
    """위경도(4326)를 EPSG:5179 미터 좌표 x, y로 변환해 칼럼을 추가한다."""
    tf = Transformer.from_crs(C.CRS_SRC, C.CRS_WORK, always_xy=True)
    x, y = tf.transform(df.lon.to_numpy(), df.lat.to_numpy())
    return df.assign(x=x, y=y)


def split_trips(df, log):
    """
    세션 안에서 시간 공백이 SPLIT_GAP_S를 넘는 곳을 기준으로 통행을 나눈다.
    trip_id = "{session_id}_{분할순번:03d}"

    입력은 (session_id, ts) 정렬 상태여야 한다.
    """
    sid = df.session_id.to_numpy()
    ts = df.ts.to_numpy()
    new_session = np.r_[True, sid[1:] != sid[:-1]]
    gap = np.r_[False, np.diff(ts) > C.SPLIT_GAP_S * 1000] & ~new_session
    # 세션 안에서 공백이 나올 때마다 순번 +1
    seg = pd.Series(gap.astype(int)).groupby(sid).cumsum().to_numpy()
    df = df.assign(trip_id=[f"{s}_{k:03d}" for s, k in zip(sid, seg)])

    n_trips = df.trip_id.nunique()
    per_session = df.groupby("session_id").trip_id.nunique()
    log["n_gap_splits"] = int(gap.sum())
    log["n_sessions_split"] = int((per_session > 1).sum())
    log["n_trips_after_split"] = int(n_trips)
    return df


# ---------------------------------------------------------------------------
# 튐 포인트 제거 (앵커 기반 전후방 필터)
# ---------------------------------------------------------------------------
# 왜 이 방식인가
#   GPS 튐은 한 점만 튀는 경우도 있지만, 고층 건물 사이에서는 2~5개 점이 함께
#   엉뚱한 곳으로 밀려나는 경우가 흔하다. "들어오는 속도와 나가는 속도가 모두 빠른 점"만
#   지우는 규칙은 연속으로 튄 점들(서로 가까이 붙어 있음)을 하나도 잡지 못한다.
#
# 알고리즘 ('도달 불가' = 속도 > SPIKE_SPEED_KMH 이고 거리 > SPIKE_MIN_JUMP_M)
#   1. 도달 불가 구간에서 통행을 끊어 '서로 일관된 조각'들로 나눈다.
#   2. 포인트가 가장 많은 조각을 앵커(믿을 수 있는 구간)로 삼는다.
#   3. 앵커 끝에서 뒤로 가며: 마지막으로 채택한 점에서 다음 점까지 도달 가능하면 채택,
#      불가능하면 버린다. 버린 점은 기준점이 되지 않는다.
#   4. 앵커 시작에서 앞으로 가며 같은 방식으로 판정한다.
#   => 튄 점이 몇 개 연속이든, 통행 첫 점이 튀었든, 튄 점이 따로따로 붙어 있든 모두 처리된다.
#
# 도달 불가 구간이 하나도 없는 통행은 손대지 않는다 (대부분의 통행).
# 이 판정만 벡터화하고, 의심 통행에 대해서만 반복문을 돈다.

def _unreachable(d_m, dt_ms):
    """
    거리 d_m(m)를 시간 dt_ms(ms) 안에 자전거로 갈 수 없으면 True.
    '속도 > SPIKE_SPEED_KMH' 이면서 '거리 > SPIKE_MIN_JUMP_M' 일 때만 도달 불가로 본다.
    (거리 조건이 없으면 1초 간격 데이터의 GPS 잡음까지 튐으로 잡힌다)
    numpy 배열과 스칼라 모두 받는다.
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        v = d_m / (np.abs(dt_ms) / 1000.0) * 3.6
    return (v > C.SPIKE_SPEED_KMH) & (d_m > C.SPIKE_MIN_JUMP_M)


def filter_trip_spikes(ts, x, y):
    """
    통행 하나의 튐 점을 판정한다.

    입력: ts(ms), x, y  -- 시간순 정렬된 numpy 배열 (같은 통행)
    반환: 남길 점이면 True인 bool 배열
    """
    n = len(ts)
    keep = np.ones(n, dtype=bool)
    if n < 3:
        return keep   # 2점 이하는 어느 쪽이 튄 것인지 판단 불가. 통행 규칙(포인트 수)에서 걸러진다.

    bad_seg = _unreachable(np.hypot(np.diff(x), np.diff(y)), np.diff(ts))
    breaks = np.flatnonzero(bad_seg)              # 구간 k는 점 k와 k+1 사이
    if len(breaks) == 0:
        return keep

    # 1-2. 조각 나누기와 앵커 선택 (길이가 같으면 앞쪽 조각)
    starts = np.r_[0, breaks + 1]
    ends = np.r_[breaks, n - 1]
    k = int(np.argmax(ends - starts))
    a0, a1 = starts[k], ends[k]

    keep[:] = False
    keep[a0:a1 + 1] = True

    # 3. 앵커 뒤쪽
    ref = a1
    for j in range(a1 + 1, n):
        if not _unreachable(np.hypot(x[j] - x[ref], y[j] - y[ref]), ts[j] - ts[ref]):
            keep[j] = True
            ref = j

    # 4. 앵커 앞쪽
    ref = a0
    for j in range(a0 - 1, -1, -1):
        if not _unreachable(np.hypot(x[ref] - x[j], y[ref] - y[j]), ts[ref] - ts[j]):
            keep[j] = True
            ref = j
    return keep


def remove_spikes(df, log, check_all=False):
    """
    튐 포인트를 제거한다. 입력은 (trip_id, ts) 정렬 상태여야 한다.

    check_all=True면 의심 통행 선별(벡터화)을 건너뛰고 모든 통행에 반복문을 돈다.
    검증용이며, 두 결과가 같아야 한다.
    """
    code = pd.factorize(df.trip_id)[0]
    ts, x, y = df.ts.to_numpy(), df.x.to_numpy(), df.y.to_numpy()

    # 의심 통행 선별: 같은 통행 안에 도달 불가 구간이 하나라도 있으면 의심
    same = code[1:] == code[:-1]
    fast_seg = same & _unreachable(np.hypot(np.diff(x), np.diff(y)), np.diff(ts))
    suspect = np.unique(code[1:][fast_seg])
    targets = np.unique(code) if check_all else suspect

    keep = np.ones(len(df), dtype=bool)
    bounds = np.r_[0, np.flatnonzero(~same) + 1, len(df)]   # 통행 c의 행 범위: bounds[c]:bounds[c+1]
    for c in targets:
        s, e = bounds[c], bounds[c + 1]
        keep[s:e] = filter_trip_spikes(ts[s:e], x[s:e], y[s:e])

    log["n_trips_with_fast_segment"] = int(len(suspect))
    log["n_spike_points_removed"] = int((~keep).sum())
    return df[keep].reset_index(drop=True)


# ---------------------------------------------------------------------------
# 통행 지표와 제외 규칙
# ---------------------------------------------------------------------------
def path_length_thinned(x, y, min_step):
    """
    GPS 잡음에 덜 민감한 경로 길이(m).

    직전 기준점에서 min_step 이상 떨어진 점이 나올 때만 거리를 더하고 기준점을 옮긴다.
    마지막 점까지 남은 거리는 끝에 더한다.
    왜: 1초 간격처럼 촘촘한 데이터는 잡음 때문에 점들이 지그재그로 흔들려,
        인접 점 거리를 그냥 합하면 실제보다 크게 나온다 (합성 테스트에서 +55%).
    입력: 한 통행의 x, y numpy 배열 (EPSG:5179, 시간순)
    """
    total = 0.0
    rx, ry = x[0], y[0]
    for xi, yi in zip(x[1:], y[1:]):
        d = ((xi - rx) ** 2 + (yi - ry) ** 2) ** 0.5
        if d >= min_step:
            total += d
            rx, ry = xi, yi
    total += ((x[-1] - rx) ** 2 + (y[-1] - ry) ** 2) ** 0.5
    return total


def trip_metrics(df):
    """
    통행별 요약을 계산한다. 입력은 (trip_id, ts) 정렬 상태.
    반환 칼럼: trip_id, session_id, n_points,
              dist_m      (잡음 보정 거리, 필터와 평균속도에 사용)
              dist_raw_m  (인접 점 거리 단순 합, 참고용)
              duration_s, avg_kmh, start_ts, end_ts, start_x, start_y, end_x, end_y
    """
    code, uniques = pd.factorize(df.trip_id)
    x, y, ts = df.x.to_numpy(), df.y.to_numpy(), df.ts.to_numpy()
    same = code[1:] == code[:-1]
    seg = np.where(same, np.hypot(np.diff(x), np.diff(y)), 0.0)
    dist_raw = np.bincount(code[1:], weights=seg, minlength=len(uniques))

    bounds = np.r_[0, np.flatnonzero(~same) + 1, len(df)]
    dist = np.array([path_length_thinned(x[s:e], y[s:e], C.DIST_MIN_STEP_M)
                     for s, e in zip(bounds[:-1], bounds[1:])])

    g = df.groupby(code, sort=True)
    first, last = g.head(1), g.tail(1)
    out = pd.DataFrame({
        "trip_id": uniques,
        "session_id": first.session_id.to_numpy(),
        "n_points": g.size().to_numpy(),
        "dist_m": dist,
        "dist_raw_m": dist_raw,
        "start_ts": first.ts.to_numpy(), "end_ts": last.ts.to_numpy(),
        "start_x": first.x.to_numpy(), "start_y": first.y.to_numpy(),
        "end_x": last.x.to_numpy(), "end_y": last.y.to_numpy(),
    })
    out["duration_s"] = (out.end_ts - out.start_ts) / 1000.0
    with np.errstate(divide="ignore", invalid="ignore"):
        out["avg_kmh"] = np.where(out.duration_s > 0, out.dist_m / out.duration_s * 3.6, np.nan)
    return out


# 규칙 이름과 '제외 조건'. 순서대로 적용하며, 처음 걸린 규칙이 제외 사유가 된다.
TRIP_RULES = [
    ("few_points", lambda t: t.n_points < C.MIN_POINTS),
    ("too_short", lambda t: t.dist_m < C.MIN_DIST_M),
    ("too_long", lambda t: t.dist_m > C.MAX_DIST_M),
    ("too_slow", lambda t: t.avg_kmh.isna() | (t.avg_kmh < C.MIN_AVG_KMH)),
    ("too_fast", lambda t: t.avg_kmh > C.MAX_AVG_KMH),
]


def apply_trip_rules(trips, log):
    """제외 사유(drop_reason)와 유지 여부(keep)를 붙인다."""
    reason = pd.Series(pd.NA, index=trips.index, dtype="object")
    independent = {}
    sequential = {}
    for name, cond in TRIP_RULES:
        hit = cond(trips).fillna(False)
        independent[name] = int(hit.sum())                 # 이 규칙 하나만 적용했을 때 걸리는 수
        new = hit & reason.isna()
        sequential[name] = int(new.sum())                  # 앞 규칙을 통과한 것 중 이 규칙으로 제거된 수
        reason[new] = name
    trips = trips.assign(drop_reason=reason, keep=reason.isna())
    log["trip_rule_removed_sequential"] = sequential
    log["trip_rule_hits_independent"] = independent
    log["n_trips_kept"] = int(trips.keep.sum())
    return trips


def sampling_stats(df):
    """정제 후 남은 통행의 GPS 샘플링 간격과 포인트 간 이동거리 분포 (1단계 맵매칭 설정용)."""
    code = pd.factorize(df.trip_id)[0]
    same = code[1:] == code[:-1]
    dt = (np.diff(df.ts.to_numpy()) / 1000.0)[same]
    step = np.hypot(np.diff(df.x.to_numpy()), np.diff(df.y.to_numpy()))[same]
    if len(dt) == 0:
        return {}
    q = [10, 50, 90, 99]
    return {
        "dt_s_percentiles": dict(zip([f"p{v}" for v in q], np.percentile(dt, q).round(2).tolist())),
        "dt_s_max": float(dt.max()),
        "share_dt_le_1s": float((dt <= 1).mean().round(4)),
        "share_dt_le_5s": float((dt <= 5).mean().round(4)),
        "share_dt_le_15s": float((dt <= 15).mean().round(4)),
        "step_m_percentiles": dict(zip([f"p{v}" for v in q], np.percentile(step, q).round(1).tolist())),
    }


# ===========================================================================
# 3. 전체 실행
# ===========================================================================
def clean(raw):
    """
    원본 포인트를 받아 정제한다.
    반환: (points, trips, log)
      points: 남은 통행의 포인트 [trip_id, session_id, ts, lon, lat, x, y, model_version]
      trips : 모든 통행 요약 + keep, drop_reason
      log   : 단계별 건수 딕셔너리
    """
    log = {}
    df = filter_scope(raw, log)
    if df.empty:
        raise ValueError(
            f"기간·범위 필터 후 남은 포인트가 없습니다 (입력 {len(raw):,}행). "
            f"config의 PERIOD_START/END({C.PERIOD_START}~{C.PERIOD_END}), MODEL_VERSIONS, BBOX_4326을 확인하세요. "
            "데이터 기간은 --probe로 볼 수 있습니다.")
    df = sort_dedup(df, log)
    df = project(df)
    df = split_trips(df, log)
    df = df.sort_values(["trip_id", "ts"], kind="mergesort").reset_index(drop=True)
    df = remove_spikes(df, log)

    trips = trip_metrics(df)
    trips = apply_trip_rules(trips, log)

    keep_ids = set(trips.loc[trips.keep, "trip_id"])
    points = df[df.trip_id.isin(keep_ids)].reset_index(drop=True)
    points = points[["trip_id", "session_id", "ts", "lon", "lat", "x", "y", "model_version"]]

    log["n_points_kept"] = len(points)
    # 잡음이 거리를 얼마나 부풀리는지 (1에 가까울수록 깨끗한 데이터)
    ok = trips.keep & (trips.dist_m > 0)
    if ok.any():
        ratio = trips.loc[ok, "dist_raw_m"] / trips.loc[ok, "dist_m"]
        log["dist_raw_over_thinned"] = ratio.describe(percentiles=[.5, .9, .99]).round(3).to_dict()
    log["sampling"] = sampling_stats(points)
    kept = trips[trips.keep]
    if len(kept):
        log["kept_trip_dist_m"] = kept.dist_m.describe(percentiles=[.1, .5, .9]).round(1).to_dict()
        log["kept_trip_avg_kmh"] = kept.avg_kmh.describe(percentiles=[.1, .5, .9]).round(2).to_dict()
        log["bbox_4326"] = [float(points.lon.min()), float(points.lat.min()),
                            float(points.lon.max()), float(points.lat.max())]
    return points, trips, log


def print_report(log):
    """체크포인트 보고용 요약 출력."""
    print("\n========== 0단계 정제 결과 ==========")
    print(f"입력 포인트            {log['n_points_input']:>12,}")
    print(f"입력 세션              {log['n_sessions_input']:>12,}")
    print(f"기간 내 세션           {log['n_sessions_in_period']:>12,}")
    print(f"범위 필터 후 포인트    {log['n_points_after_scope']:>12,}")
    print(f"중복 포인트 제거       {log['n_dup_points_removed']:>12,}")
    print(f"공백 분할 지점         {log['n_gap_splits']:>12,}  (분할된 세션 {log['n_sessions_split']:,})")
    print(f"분할 후 통행           {log['n_trips_after_split']:>12,}")
    print(f"튐 포인트 제거         {log['n_spike_points_removed']:>12,}  (과속 구간이 있던 통행 {log['n_trips_with_fast_segment']:,})")
    print("\n통행 제외 규칙         순차 제거    단독 적용 시")
    for name, _ in TRIP_RULES:
        print(f"  {name:<18}{log['trip_rule_removed_sequential'][name]:>10,}{log['trip_rule_hits_independent'][name]:>14,}")
    print(f"\n최종 통행              {log['n_trips_kept']:>12,}")
    print(f"최종 포인트            {log['n_points_kept']:>12,}")
    print(f"\n모델 버전별 포인트     {log['model_version_counts']}")
    if "dist_raw_over_thinned" in log:
        r = log["dist_raw_over_thinned"]
        print(f"잡음에 의한 거리 부풀림  중앙값 x{r['50%']}, 90% x{r['90%']}, 99% x{r['99%']}")
    if log.get("sampling"):
        s = log["sampling"]
        print(f"샘플링 간격(초)        {s['dt_s_percentiles']}")
        print(f"포인트 간 거리(m)      {s['step_m_percentiles']}")
    if "bbox_4326" in log:
        print(f"데이터 영역(4326)      {log['bbox_4326']}")
    print("=====================================\n")


def main(force=False):
    C.setup_environment()
    force = force or C.FORCE_RECOMPUTE
    if OUT_POINTS.exists() and OUT_TRIPS.exists() and OUT_STATS.exists() and not force:
        print(f"[s0] 기존 산출물이 있어 건너뜁니다 (--force 로 재계산). {OUT_STATS}")
        print_report(json.loads(OUT_STATS.read_text()))
        return

    t = time.time()
    raw = load_raw(force)
    points, trips, log = clean(raw)
    log["elapsed_s"] = round(time.time() - t, 1)
    log["config"] = {k: getattr(C, k) for k in [
        "PERIOD_START", "PERIOD_END", "MODEL_VERSIONS", "BBOX_4326", "SPLIT_GAP_S",
        "SPIKE_SPEED_KMH", "SPIKE_MIN_JUMP_M", "DIST_MIN_STEP_M", "MIN_POINTS", "MIN_DIST_M", "MAX_DIST_M", "MIN_AVG_KMH", "MAX_AVG_KMH"]}

    points.to_parquet(OUT_POINTS, index=False)
    trips.to_parquet(OUT_TRIPS, index=False)
    OUT_STATS.write_text(json.dumps(log, ensure_ascii=False, indent=2, default=str))
    print_report(log)
    print(f"[s0] 저장 완료 ({log['elapsed_s']}초): {OUT_POINTS.name}, {OUT_TRIPS.name}, {OUT_STATS.name}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="캐시 무시하고 재계산")
    ap.add_argument("--probe", action="store_true", help="날짜·모델버전별 규모만 확인")
    args = ap.parse_args()
    if args.probe:
        C.setup_environment()
        probe()
    else:
        main(force=args.force)
