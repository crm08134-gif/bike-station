"""
0단계 보조 분석: 세션이 실제 통행 전체인가, 긴 통행의 조각인가

배경
  0단계 결과 세션 길이가 4분 또는 5분에 몰려 있고, 세션 안에 정확히 61초짜리 공백이 흔했다.
  통행 단위(세션을 그대로 쓸지, 이어 붙일지)를 정하기 전에 두 가지를 확인한다.

  1. 제공처 원본 대조: 원본(korea_gps_merged.csv)은 1분 세그먼트에 수단 라벨이 붙은 형태다.
     - 세션 안 61초 공백, 세션 사이 공백 시간대에 원본에는 다른 수단 데이터가 있는가?
       (있으면 BIKE만 뽑으면서 생긴 구멍, 없으면 원본 자체의 결측)
     - 원본 BIKE 포인트 중 track_point에 없는 점은 무엇인가?
  2. 연속 세션 간 거리 검사: 같은 사용자의 세션 k 끝점 -> 세션 k+1 시작점
     - 공백 시간, 직선거리, 직선 이동속도(= 거리 / 공백 시간)
     - 이동속도가 LINK_RIDE_KMH 범위면 '타고 가던 중 잘림'으로 본다.

입력  : C.PROVIDER_RAW_PATH, C.SESSIONS_META_PATH, 0단계 산출물(s0_trips.parquet)과 원본 덤프
출력  : data/interim/s0_session_pairs.parquet  연속 세션 쌍별 공백·거리·속도·사이 시간대 원본 수단
        화면 출력: 요약 표

실행
  python s0_session_links.py
"""
import numpy as np
import pandas as pd

import config as C
import s0_extract as S0

OUT_PAIRS = C.DATA_INTERIM / "s0_session_pairs.parquet"


def load_provider_segments():
    """
    제공처 원본을 읽어 포인트와 1분 세그먼트 요약을 만든다.

    segment_id 형식: TMC-GPS-{사용자}-{레코드}-{세그먼트}-Dataset
    반환: (points, segs)
      points: [timestamp_ms, accuracy, latitude, longitude, mode, segment_id, user]
      segs  : 세그먼트당 한 행 [segment_id, user, rec, mode, t0, t1, n]
    """
    import pyarrow as pa
    import pyarrow.csv as pc

    tbl = pc.read_csv(C.PROVIDER_RAW_PATH, convert_options=pc.ConvertOptions(
        column_types={"segment_id": pa.string(), "mode": pa.string()},
        include_columns=["timestamp_ms", "accuracy", "latitude", "longitude", "mode", "segment_id"]))
    pts = tbl.to_pandas()
    parts = pts.segment_id.str.split("-", expand=True)
    pts["user"], rec = parts[2], parts[3]
    segs = (pts.assign(rec=rec).groupby("segment_id")
               .agg(user=("user", "first"), rec=("rec", "first"), mode=("mode", "first"),
                    t0=("timestamp_ms", "min"), t1=("timestamp_ms", "max"), n=("timestamp_ms", "size"))
               .reset_index())
    return pts, segs


def modes_between(segs, user, t_from, t_to):
    """
    user의 원본 세그먼트 중 (t_from, t_to) 시간대와 겹치는 것의 수단 목록.
    반환: 'BIKE,WALK' 같은 문자열. 겹치는 세그먼트가 없으면 '(없음)'.
    """
    s = segs[(segs.user == user) & (segs.t1 > t_from) & (segs.t0 < t_to)]
    return ",".join(sorted(s["mode"].unique())) or "(없음)"


def compare_with_provider(pts, segs, raw, meta):
    """
    원본 BIKE 포인트와 track_point를 (사용자, 시각)으로 대조한다.

    반환: dict (원본 BIKE 수, 원본 내 중복, track_point에 없는 점 수와 그 accuracy 범위, 남은 점의 accuracy 최대값)
    """
    bike = pts[pts["mode"] == "BIKE"]
    tp = raw[["session_id", "ts"]].merge(meta[["session_id", "user"]], on="session_id")
    m = bike.merge(tp.rename(columns={"ts": "timestamp_ms"}), on=["user", "timestamp_ms"], how="left")
    missing = m.session_id.isna()
    return {
        "provider_bike_points": len(bike),
        "provider_bike_dup_user_ts": int(bike.duplicated(["user", "timestamp_ms"]).sum()),
        "not_in_track_point": int(missing.sum()),
        "not_in_track_point_segments": int(m.loc[missing, "segment_id"].nunique()),
        "not_in_track_point_accuracy_min": float(m.loc[missing, "accuracy"].min()),
        "in_track_point_accuracy_max": float(m.loc[~missing, "accuracy"].max()),
        "records_with_bike_single_mode": bool(
            segs[segs.rec.isin(segs.loc[segs["mode"] == "BIKE", "rec"])].groupby("rec")["mode"].nunique().eq(1).all()),
    }


def inner_gaps(raw, meta, segs, min_gap_s=30):
    """
    세션 안의 공백(min_gap_s 초 초과)마다 그 시간대에 원본에 있던 수단을 찾는다.
    반환: DataFrame [session_id, user, gap_s, filled_by]
    """
    r = raw.sort_values(["session_id", "ts"])
    dt = r.groupby("session_id").ts.diff()
    g = r.assign(prev_ts=r.ts - dt, gap_s=dt / 1000)[dt > min_gap_s * 1000]
    g = g.merge(meta[["session_id", "user"]], on="session_id")
    g["filled_by"] = [modes_between(segs, u, a, b) for u, a, b in zip(g.user, g.prev_ts, g.ts)]
    return g[["session_id", "user", "gap_s", "filled_by"]]


def session_pairs(trips, meta, segs):
    """
    같은 사용자의 시간상 연속한 두 세션(통행)을 짝지어 공백 동안의 이동을 계산한다.
    거리는 EPSG:5179 좌표(s0_trips의 start_x/y, end_x/y)로 계산한다.

    반환: DataFrame [user, same_record, gap_s, dist_m, kmh, ridden, between_modes, gap_bin]
      kmh    : 직선거리 / 공백 시간 (실제 이동속도의 하한)
      ridden : kmh가 LINK_RIDE_KMH 범위 안이면 True
    """
    t = (trips.merge(meta[["session_id", "user", "source_records"]], on="session_id")
              .sort_values(["user", "start_ts"]).reset_index(drop=True))
    same = t.user.to_numpy()[1:] == t.user.to_numpy()[:-1]
    a = t.iloc[:-1][same].reset_index(drop=True)
    b = t.iloc[1:][same].reset_index(drop=True)
    pr = pd.DataFrame({
        "user": a.user,
        "same_record": a.source_records.to_numpy() == b.source_records.to_numpy(),
        "gap_s": (b.start_ts.to_numpy() - a.end_ts.to_numpy()) / 1000,
        "dist_m": np.hypot(b.start_x.to_numpy() - a.end_x.to_numpy(), b.start_y.to_numpy() - a.end_y.to_numpy()),
    })
    pr["kmh"] = pr.dist_m / pr.gap_s * 3.6
    lo, hi = C.LINK_RIDE_KMH
    pr["ridden"] = pr.kmh.between(lo, hi)
    pr["between_modes"] = [modes_between(segs, u, x, y)
                           for u, x, y in zip(pr.user, a.end_ts.to_numpy(), b.start_ts.to_numpy())]
    bins = C.LINK_GAP_BINS_S
    labels = [f"{_fmt(lo_)}~{_fmt(hi_)}" for lo_, hi_ in zip(bins[:-1], bins[1:])]
    pr["gap_bin"] = pd.cut(pr.gap_s, bins, labels=labels)
    return pr


def _fmt(s):
    """초를 '5분', '1시간', '∞' 같은 짧은 표기로."""
    if s == float("inf"):
        return "∞"
    return f"{s / 3600:g}시간" if s >= 3600 else f"{s / 60:g}분"


def main():
    C.setup_environment()
    raw = S0.load_raw()
    meta = pd.read_csv(C.SESSIONS_META_PATH, dtype=str)
    trips = pd.read_parquet(S0.OUT_TRIPS)
    pts, segs = load_provider_segments()

    print("[원본 구성]")
    print(f"  포인트 {len(pts):,}, 세그먼트 {len(segs):,} (세그먼트당 포인트 중앙값 {segs.n.median():.0f}), "
          f"수단별 포인트 {pts['mode'].value_counts().to_dict()}")
    print(f"  원본 대조: {compare_with_provider(pts, segs, raw, meta)}")

    g = inner_gaps(raw, meta, segs)
    print(f"\n[세션 안 공백 >30초] {len(g)}건, 공백 길이 상위 {g.gap_s.round().value_counts().head(3).to_dict()}")
    print(f"  그 시간대 원본 세그먼트: {g.filled_by.value_counts().to_dict()}")

    pr = session_pairs(trips, meta, segs)
    pr.to_parquet(OUT_PAIRS, index=False)
    lo, hi = C.LINK_RIDE_KMH
    summ = pr.groupby("gap_bin", observed=True).agg(
        쌍=("dist_m", "size"),
        거리_p50_m=("dist_m", "median"),
        거리_p90_m=("dist_m", lambda s: s.quantile(.9)),
        직선속도_p50=("kmh", "median"),
        **{f"{lo}~{hi}kmh_비율": ("ridden", "mean")},
        같은곳_200m미만=("dist_m", lambda s: (s < 200).mean()),
        같은레코드=("same_record", "mean"),
    ).round(2)
    print(f"\n[연속 세션 쌍] 같은 사용자 {len(pr)}쌍")
    print(summ.to_string())
    near = pr[pr.gap_s <= 3600]
    print(f"\n  1시간 이내 쌍 사이 시간대 원본 수단: {near.between_modes.value_counts().to_dict()}")
    print(f"[s0_session_links] 저장: {OUT_PAIRS}")


if __name__ == "__main__":
    main()
