"""
0단계 정제 로직 검증 (합성 데이터)

정답을 알고 있는 궤적을 만들어 s0_extract.clean()에 넣고,
각 규칙이 의도한 만큼만 작동하는지 확인한다.

실행: python tests/test_s0.py
"""
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

# config를 import하기 전에 테스트용 경로와 입력 방식을 지정한다
TMP = Path(tempfile.mkdtemp(prefix="s0_test_"))
os.environ["BIKE_BASE_DIR"] = str(TMP)
os.environ["BIKE_S0_SOURCE"] = "file"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import config as C          # noqa: E402
import s0_extract as S0     # noqa: E402
from pyproj import Transformer  # noqa: E402

C.PERIOD_START, C.PERIOD_END = "2026-09-01", "2026-09-08"
rng = np.random.default_rng(0)
TO_4326 = Transformer.from_crs(C.CRS_WORK, C.CRS_SRC, always_xy=True)
ORIGIN = (958_000.0, 1_944_000.0)   # 서울 강남 부근 (EPSG:5179)


def kst_ms(s):
    return int(pd.Timestamp(s, tz="Asia/Seoul").value // 10**6)


def ride(n, dt_s, kmh, start="2026-09-02 08:00", heading_deg=0.0, turn_every=40, noise_m=0.0, origin=ORIGIN):
    """
    일정 속도로 달리는 궤적 (40점마다 90도 회전하는 계단형 경로).
    반환: DataFrame [ts, x, y]
    """
    step = kmh / 3.6 * dt_s
    ang = np.deg2rad(heading_deg) + np.deg2rad(90) * (np.arange(n) // turn_every % 2)
    dx, dy = step * np.cos(ang), step * np.sin(ang)
    x = origin[0] + np.r_[0, np.cumsum(dx[:-1])]
    y = origin[1] + np.r_[0, np.cumsum(dy[:-1])]
    if noise_m:
        x = x + rng.normal(0, noise_m, n)
        y = y + rng.normal(0, noise_m, n)
    ts = kst_ms(start) + (np.arange(n) * dt_s * 1000).astype(np.int64)
    return pd.DataFrame({"ts": ts, "x": x, "y": y})


def shift(df, idx, dx, dy):
    """idx 위치의 점을 (dx, dy)m 만큼 튀게 만든다."""
    df = df.copy()
    df.loc[idx, "x"] += dx
    df.loc[idx, "y"] += dy
    return df


# ---------------------------------------------------------------------------
# 시나리오: (세션 이름, 궤적, 기대값)
#   trips   : 분할 후 통행 수
#   spikes  : 제거될 튐 포인트 수
#   reasons : 통행별 제외 사유 (None = 유지)
# ---------------------------------------------------------------------------
base = ride(120, 5, 15)
gap_a = ride(60, 5, 15)
gap_b = ride(60, 5, 15, start="2026-09-02 08:15", origin=(gap_a.x.iloc[-1] + 50, gap_a.y.iloc[-1]))
stop_a = ride(60, 5, 15)
stop_b = ride(60, 5, 15, start=pd.Timestamp("2026-09-02 08:00") + pd.Timedelta(seconds=59 * 5 + 180),
              origin=(stop_a.x.iloc[-1], stop_a.y.iloc[-1]))

SCENARIOS = [
    ("S01_normal",          base,                                                     dict(trips=1, spikes=0, reasons=[None])),
    ("S02_one_spike",       shift(base, 50, 400, 0),                                  dict(trips=1, spikes=1, reasons=[None])),
    ("S03_spike_cluster3",  shift(shift(shift(base, 60, 300, 0), 61, 305, 8), 62, 298, 15),
                                                                                      dict(trips=1, spikes=3, reasons=[None])),
    ("S04_bad_start_end",   shift(shift(shift(base, 0, -500, 0), 1, -495, 5), 119, 0, 600),
                                                                                      dict(trips=1, spikes=3, reasons=[None])),
    ("S05_adjacent_spikes", shift(shift(base, 40, 400, 0), 41, 0, 400),               dict(trips=1, spikes=2, reasons=[None])),
    ("S06_gap_10min",       pd.concat([gap_a, gap_b], ignore_index=True),             dict(trips=2, spikes=0, reasons=[None, None])),
    ("S07_stop_3min",       pd.concat([stop_a, stop_b], ignore_index=True),           dict(trips=1, spikes=0, reasons=[None])),
    ("S08_few_points",      ride(8, 5, 15),                                           dict(trips=1, spikes=0, reasons=["few_points"])),
    ("S09_too_short",       ride(30, 1, 15),                                          dict(trips=1, spikes=0, reasons=["too_short"])),
    ("S10_too_slow",        ride(100, 10, 2),                                         dict(trips=1, spikes=0, reasons=["too_slow"])),
    ("S11_too_fast_car",    ride(100, 5, 50),                                         dict(trips=1, spikes=0, reasons=["too_fast"])),
    ("S12_too_long",        ride(1000, 5, 18),                                        dict(trips=1, spikes=0, reasons=["too_long"])),
    ("S13_jitter_1s",       ride(400, 1, 15, noise_m=3),                              dict(trips=1, spikes=0, reasons=[None])),
    ("S14_drift_start5",    shift(base, [0, 1, 2, 3, 4], 350, 350),                   dict(trips=1, spikes=5, reasons=[None])),
]
# 기간 판정용 (결과에 나오면 안 되거나, 통째로 나와야 함)
OUT_OF_PERIOD = ride(100, 5, 15, start="2026-09-10 08:00")            # 기간 밖 -> 제외
BEFORE_START = ride(120, 5, 15, start="2026-08-31 23:55")             # 시작 전 출발 -> 제외
CROSS_END = ride(120, 5, 15, start="2026-09-07 23:58")                # 기간 끝 직전 출발 -> 통째로 포함


def build_dump():
    frames = []
    for name, df, _ in SCENARIOS:
        frames.append(df.assign(session_id=name))
    frames += [OUT_OF_PERIOD.assign(session_id="X_out_of_period"),
               BEFORE_START.assign(session_id="X_before_start"),
               CROSS_END.assign(session_id="X_cross_end")]
    raw = pd.concat(frames, ignore_index=True)
    # 중복 행 3개 추가 (PK상 DB에는 없어야 하지만 파일 입력 대비)
    raw = pd.concat([raw, raw[raw.session_id == "S01_normal"].iloc[:3]], ignore_index=True)
    lon, lat = TO_4326.transform(raw.x.to_numpy(), raw.y.to_numpy())
    raw = raw.assign(lon=lon, lat=lat, model_version="v1").sample(frac=1, random_state=1)   # 순서 섞기
    return raw[["session_id", "ts", "lon", "lat", "model_version"]]


def check_geom_input(raw, check):
    """
    DB 테이블을 그대로 내보낸 CSV (lon/lat 대신 geom 문자열 칼럼)를 _read_from_file()이 제대로 읽는지 확인한다.
    형식: EWKT('SRID=4326;POINT(lon lat)', 실제 덤프 형식), WKT, EWKB hex. SRID가 4326이 아니면 에러가 나야 한다.
    반환: 형식별 최대 좌표 오차(도) 딕셔너리
    """
    import shapely

    lon, lat = raw.lon.to_numpy(), raw.lat.to_numpy()
    pts = shapely.set_srid(shapely.points(lon, lat), 4326)
    # 파이썬 float의 repr = 왕복 손실 없는 최단 표기 (np.float64의 repr은 'np.float64(...)'라서 tolist()로 바꿈)
    variants = {
        "ewkt": [f"SRID=4326;POINT({a!r} {b!r})" for a, b in zip(lon.tolist(), lat.tolist())],
        "wkt": [f"POINT({a!r} {b!r})" for a, b in zip(lon.tolist(), lat.tolist())],
        "ewkb_hex": shapely.to_wkb(pts, hex=True, include_srid=True),
    }
    base_cols = raw[["session_id", "ts", "model_version"]]
    errs = {}
    saved = C.RAW_DUMP_PATH
    try:
        for name, geom in variants.items():
            path = TMP / f"dump_{name}.csv"
            base_cols.assign(geom=geom).to_csv(path, index=False)
            C.RAW_DUMP_PATH = path
            got = S0._read_from_file()
            check(list(got.columns) == S0.RAW_COLUMNS, f"geom 입력({name}): 칼럼 {list(got.columns)}")
            check(got.ts.dtype == "int64" and got.lon.dtype == "float64", f"geom 입력({name}): dtype 오류")
            errs[name] = float(max(np.abs(got.lon.to_numpy() - lon).max(), np.abs(got.lat.to_numpy() - lat).max()))
            check(errs[name] < 1e-12, f"geom 입력({name}): 좌표 오차 {errs[name]:.2e}도")

        # 다른 좌표계로 저장된 geom은 추측해서 읽지 말고 에러를 내야 한다
        path = TMP / "dump_srid5179.csv"
        base_cols.assign(geom=[g.replace("SRID=4326", "SRID=5179") for g in variants["ewkt"]]).to_csv(path, index=False)
        C.RAW_DUMP_PATH = path
        try:
            S0._read_from_file()
            check(False, "geom 입력: SRID 5179인데 에러가 나지 않음")
        except ValueError:
            pass
    finally:
        C.RAW_DUMP_PATH = saved
    return errs


def main():
    raw = build_dump()
    points, trips, log = S0.clean(raw)
    fails = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)

    # --- geom 문자열 칼럼 입력 (DB 테이블 그대로 내보낸 CSV) ---
    geom_errs = check_geom_input(raw, check)

    # --- 시나리오별 확인 ---
    raw_counts = raw.groupby("session_id").size()
    kept_counts = trips.groupby("session_id").n_points.sum()
    rows = []
    for name, df, exp in SCENARIOS:
        t = trips[trips.session_id == name].sort_values("trip_id")
        removed = len(df) - kept_counts.get(name, 0)
        reasons = [None if pd.isna(r) else r for r in t.drop_reason]
        ok = (len(t) == exp["trips"]) and (removed == exp["spikes"]) and (reasons == exp["reasons"])
        check(ok, f"{name}: trips={len(t)} spikes={removed} reasons={reasons} / 기대 {exp}")
        rows.append((name, len(t), removed, ",".join(str(r) for r in reasons),
                     round(t.dist_m.sum()), round(t.avg_kmh.mean(), 1), "OK" if ok else "FAIL"))

    # --- 기간 판정 ---
    sids = set(trips.session_id)
    check("X_out_of_period" not in sids, "기간 밖 세션이 포함됨")
    check("X_before_start" not in sids, "기간 시작 전 출발 세션이 포함됨")
    check(trips[trips.session_id == "X_cross_end"].n_points.sum() == len(CROSS_END),
          "기간 끝을 걸친 세션이 잘림")

    # --- 중복 제거 ---
    check(log["n_dup_points_removed"] == 3, f"중복 제거 수 {log['n_dup_points_removed']} (기대 3)")

    # --- 튐 제거 결과: 정상 점은 하나도 안 지워졌는지 (S02: 50번 점만 빠져야 함) ---
    s2 = points[points.session_id == "S02_one_spike"].sort_values("ts")
    check(len(s2) == 119 and kst_ms("2026-09-02 08:00") + 50 * 5000 not in set(s2.ts),
          "S02: 튄 점이 아닌 다른 점이 제거됨")

    # --- 벡터화 선별 vs 전체 반복문 결과 일치 ---
    df = S0.project(S0.sort_dedup(S0.filter_scope(raw, {}), {}))
    df = S0.split_trips(df, {}).sort_values(["trip_id", "ts"], kind="mergesort").reset_index(drop=True)
    fast = S0.remove_spikes(df, {})
    full = S0.remove_spikes(df, {}, check_all=True)
    check(fast[["trip_id", "ts"]].equals(full[["trip_id", "ts"]]), "선별 버전과 전체 반복 버전 결과 불일치")

    # --- 잡음 보정 거리: 1초 간격 잡음 궤적의 평균속도가 실제(15km/h)에 가까운지 ---
    s13 = trips[trips.session_id == "S13_jitter_1s"].iloc[0]
    raw13 = s13.dist_raw_m / s13.duration_s * 3.6
    check(abs(s13.avg_kmh - 15) / 15 < 0.10,
          f"S13 잡음 보정 평균속도 {s13.avg_kmh:.1f}km/h (실제 15, 보정 전 {raw13:.1f})")
    jitter_note = f"S13 평균속도: 보정 전 {raw13:.1f} km/h -> 보정 후 {s13.avg_kmh:.1f} km/h (실제 15.0)"

    # --- 거리 계산 검증: 정상 궤적 거리 = (n-1) * step ---
    s1 = trips[trips.session_id == "S01_normal"].iloc[0]
    expected = 119 * 15 / 3.6 * 5
    check(abs(s1.dist_m - expected) < 0.5, f"S01 거리 {s1.dist_m:.1f} (기대 {expected:.1f})")

    # --- 좌표 왕복 오차 (5179 -> 4326 -> 5179) ---
    back = S0.project(raw[raw.session_id == "S01_normal"].drop_duplicates(["session_id", "ts"]).sort_values("ts"))
    err = np.hypot(back.x.to_numpy() - base.x.to_numpy(), back.y.to_numpy() - base.y.to_numpy()).max()
    check(err < 0.01, f"좌표 왕복 오차 {err:.4f}m")

    # --- 출력 ---
    print(f"{'시나리오':<22}{'통행':>4}{'튐제거':>7}  {'제외사유':<20}{'거리m':>8}{'평균km/h':>9}  결과")
    for r in rows:
        print(f"{r[0]:<22}{r[1]:>4}{r[2]:>7}  {r[3]:<20}{r[4]:>8}{r[5]:>9}  {r[6]}")
    S0.print_report(log)
    print(f"좌표 왕복 최대 오차: {err * 1000:.3f} mm")
    print(jitter_note)
    print("geom 입력 최대 좌표 오차(도): " + ", ".join(f"{k} {v:.1e}" for k, v in geom_errs.items()))
    if fails:
        print("\n실패 항목:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("\n모든 검증 통과")


if __name__ == "__main__":
    main()
