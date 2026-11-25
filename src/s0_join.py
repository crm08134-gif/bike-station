"""
0단계 뒤: 세션 조각을 여정으로 잇기 (통행 단위 결정 (다), 2026-09-28)

배경
  제공처 원본은 한 레코드를 5분 창으로 나누고 창마다 1분 조각 4개만 담았으며, 창이 통째로 빠진 곳도 있다.
  그래서 0단계 통행(세션)은 긴 주행의 관측된 조각인 경우가 많다.
  목적함수는 경로의 시작과 끝을 기준선으로 쓰므로, 잘린 지점을 시종점으로 두면 없는 수요가 생기고
  한 주행이 여러 번 세어진다.

규칙
  같은 사용자, 같은 제공처 레코드의 연속 유지 통행 k, k+1 사이에서
    직선 이동속도 = (k 종점 -> k+1 시점 직선거리) / 공백 시간 이 JOIN_RIDE_KMH 범위면 잇는다.
  범위 밖(멈춰 있었거나 너무 멀리 감)이면 새 여정을 시작한다.
  속도가 맞아도 공백이 JOIN_MAX_GAP_S(5분 창 단위 비교)보다 길면 잇지 않는다. 그 지점은 여정 경계가 되고
  양쪽 조각은 버리지 않고 각각 독립 여정으로 남는다.
  0단계에서 제외된 통행(300m 미만 등)은 잇지 않는다. 그 자리는 더 긴 공백으로 남고 속도 검사는 그대로 적용된다.

여정의 공백 구간 경로는 1단계 맵매칭이 도로망으로 채운다 (관측되지 않은 부분이라 추정이다).

입력  : s0_trips.parquet, s0_points.parquet, C.SESSIONS_META_PATH (user, source_records), 행정 경계
출력  : data/interim/s0_journeys.parquet        여정 1행: journey_id, user, source_records, n_pieces,
                                                 start_ts, end_ts, span_s, obs_s, dist_obs_m, 시종점(x, y), in_study_region
        data/interim/s0_journey_pieces.parquet  조각(0단계 통행) 1행: trip_id, journey_id, piece_no, gap_before_s, gap_kmh
        data/interim/s0_join_stats.json

journey_id = 첫 조각의 trip_id

실행
  python s0_join.py            # 캐시가 있으면 건너뜀
  python s0_join.py --force
"""
import argparse
import json

import numpy as np
import pandas as pd

import config as C
import region_compare as RC
import s0_extract as S0

OUT_JOURNEYS = C.DATA_INTERIM / "s0_journeys.parquet"
OUT_PIECES = C.DATA_INTERIM / "s0_journey_pieces.parquet"
OUT_STATS = C.DATA_INTERIM / "s0_join_stats.json"


_DEFAULT = object()


def link_pieces(trips, meta, max_gap_s=_DEFAULT):
    """
    유지 통행을 (사용자, 레코드, 시작 시각) 순으로 놓고 이을지 정한다.
    max_gap_s: 끊긴 시간 상한 (초, 5분 창 단위로 비교). 기본은 C.JOIN_MAX_GAP_S, None이면 상한 없음.
    반환: 조각 표 [trip_id, journey_id, piece_no, gap_before_s, gap_kmh, same_record, speed_ok, joined]
      speed_ok & ~joined 인 조각 = 상한 때문에 끊긴 지점 (앞 조각과 별개 여정으로 시작)
    """
    max_gap_s = C.JOIN_MAX_GAP_S if max_gap_s is _DEFAULT else max_gap_s
    k = trips.merge(meta[["session_id", "user", "source_records"]], on="session_id", how="left")
    k = k.sort_values(["user", "source_records", "start_ts"], kind="mergesort").reset_index(drop=True)
    same = (k.user == k.user.shift()) & (k.source_records == k.source_records.shift())
    gap_s = (k.start_ts - k.end_ts.shift()) / 1000
    dist = np.hypot(k.start_x - k.end_x.shift(), k.start_y - k.end_y.shift())
    with np.errstate(divide="ignore", invalid="ignore"):
        kmh = dist / gap_s * 3.6
    lo, hi = C.JOIN_RIDE_KMH
    speed_ok = same & kmh.between(lo, hi)
    joined = speed_ok.copy()
    if max_gap_s is not None:
        w = C.PROVIDER_WINDOW_S
        joined &= (np.floor(gap_s / w) * w) <= max_gap_s
    chain = (~joined).cumsum()
    first_id = k.trip_id.groupby(chain).transform("first")
    return pd.DataFrame({
        "trip_id": k.trip_id, "journey_id": first_id,
        "piece_no": k.groupby(chain).cumcount(),
        "gap_before_s": gap_s.where(same), "gap_kmh": kmh.where(same),
        "same_record": same, "speed_ok": speed_ok, "joined": joined,
    })


def study_region_trips():
    """연구 지역 안에서 REGION_MIN_TRIP_M 이상 관측된 0단계 통행 id 집합 (region_compare와 같은 기준)."""
    seg, _, _ = RC.load_segments()
    km_in = seg[RC.in_region(seg, C.STUDY_REGION)].groupby("trip_id").len_m.sum()
    return set(km_in[km_in >= C.REGION_MIN_TRIP_M].index)


def build_journeys(trips, pieces, region_ids):
    """조각을 여정으로 요약한다. 시점 = 첫 조각 시점, 종점 = 마지막 조각 종점."""
    d = pieces.merge(trips, on="trip_id").sort_values(["journey_id", "piece_no"])
    d["in_region"] = d.trip_id.isin(region_ids)
    g = d.groupby("journey_id", sort=False)
    first, last = g.head(1).set_index("journey_id"), g.tail(1).set_index("journey_id")
    j = pd.DataFrame({
        "n_pieces": g.size(),
        "start_ts": first.start_ts, "end_ts": last.end_ts,
        "obs_s": g.duration_s.sum(),
        "dist_obs_m": g.dist_m.sum(),
        "start_x": first.start_x, "start_y": first.start_y,
        "end_x": last.end_x, "end_y": last.end_y,
        "in_study_region": g.in_region.any(),
    })
    j["span_s"] = (j.end_ts - j.start_ts) / 1000
    return j.reset_index()


def summarize(journeys, pieces):
    s = journeys[journeys.in_study_region]
    same = pieces[pieces.same_record]
    return {
        "n_trips_input": int(len(pieces)),
        "n_same_record_pairs": int(len(same)),
        "n_speed_ok_pairs": int(pieces.speed_ok.sum()),
        "n_cut_by_max_gap": int((pieces.speed_ok & ~pieces.joined).sum()),
        "n_joined_pairs": int(pieces.joined.sum()),
        "n_journeys": int(len(journeys)),
        "n_journeys_study_region": int(len(s)),
        "study_region_pieces_hist": s.n_pieces.value_counts().sort_index().to_dict(),
        # 여정 시간 중 조각 사이 공백의 비율 (세션 안 61초 구멍은 조각 안에 포함되어 여기서 빠진다)
        "study_region_gap_time_frac": round(float(1 - s.obs_s.sum() / s.span_s.sum()), 4),
        "joined_gap_s_percentiles": {f"p{q}": float(np.percentile(pieces.gap_before_s[pieces.joined], q))
                                     for q in (10, 50, 90, 100)} if pieces.joined.any() else {},
    }


def sensitivity(trips, meta, region_ids):
    """끊긴 시간 상한별 이은 쌍, 여정 수, 연구 지역 여정 수 (나중 민감도 분석 재료)."""
    rows = []
    for cap in C.JOIN_MAX_GAP_SENS_S:
        pc = link_pieces(trips, meta, max_gap_s=cap)
        jr = build_journeys(trips, pc, region_ids)
        s = jr[jr.in_study_region]
        rows.append({"max_gap_s": cap, "n_joined_pairs": int(pc.joined.sum()), "n_journeys": int(len(jr)),
                     "n_journeys_study_region": int(len(s)), "n_multi_piece_study_region": int((s.n_pieces > 1).sum()),
                     "study_region_gap_time_frac": round(float(1 - s.obs_s.sum() / s.span_s.sum()), 4)})
    return rows


def main(force=False):
    C.setup_environment()
    force = force or C.FORCE_RECOMPUTE
    if OUT_JOURNEYS.exists() and OUT_PIECES.exists() and OUT_STATS.exists() and not force:
        print(f"[s0_join] 기존 산출물이 있어 건너뜁니다 (--force 로 재계산). {OUT_STATS}")
        print(OUT_STATS.read_text(encoding="utf-8"))
        return
    trips = pd.read_parquet(S0.OUT_TRIPS)
    trips = trips[trips.keep].drop(columns=["keep", "drop_reason"])
    meta = pd.read_csv(C.SESSIONS_META_PATH, dtype={"session_id": str, "user": str, "source_records": str})
    region_ids = study_region_trips()
    pieces = link_pieces(trips, meta)
    journeys = build_journeys(trips, pieces, region_ids)
    log = summarize(journeys, pieces)
    log["max_gap_sensitivity"] = sensitivity(trips, meta, region_ids)
    log["config"] = {"JOIN_RIDE_KMH": C.JOIN_RIDE_KMH, "JOIN_MAX_GAP_S": C.JOIN_MAX_GAP_S,
                     "PROVIDER_WINDOW_S": C.PROVIDER_WINDOW_S, "STUDY_REGION": C.STUDY_REGION,
                     "REGION_MIN_TRIP_M": C.REGION_MIN_TRIP_M}
    pieces.drop(columns=["same_record"]).to_parquet(OUT_PIECES, index=False)
    journeys.to_parquet(OUT_JOURNEYS, index=False)
    OUT_STATS.write_text(json.dumps(log, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("\n========== 여정 잇기 결과 ==========")
    print(f"0단계 유지 통행(조각)        {log['n_trips_input']:>8,}")
    print(f"같은 레코드 연속 쌍          {log['n_same_record_pairs']:>8,}  (속도 조건 통과 {log['n_speed_ok_pairs']:,}, "
          f"상한 {C.JOIN_MAX_GAP_S}초로 끊음 {log['n_cut_by_max_gap']:,}, 이은 쌍 {log['n_joined_pairs']:,})")
    print(f"여정                         {log['n_journeys']:>8,}  (연구 지역 {log['n_journeys_study_region']:,})")
    print(f"연구 지역 여정의 조각 수     {log['study_region_pieces_hist']}")
    print(f"연구 지역 여정 시간 중 조각 사이 공백  {log['study_region_gap_time_frac']:.1%}")
    print(f"이은 공백 길이(초)           {log['joined_gap_s_percentiles']}")
    print("끊긴 시간 상한별 (민감도)")
    for r in log["max_gap_sensitivity"]:
        cap = "없음" if r["max_gap_s"] is None else f"{r['max_gap_s'] // 60}분"
        print(f"  상한 {cap:>4}: 이은 쌍 {r['n_joined_pairs']:>4}, 여정 {r['n_journeys']:>4}, "
              f"연구 지역 여정 {r['n_journeys_study_region']:>4} (여러 조각 {r['n_multi_piece_study_region']}), "
              f"공백 시간 {r['study_region_gap_time_frac']:.1%}")
    print("=====================================\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="캐시 무시하고 재계산")
    main(force=ap.parse_args().force)
