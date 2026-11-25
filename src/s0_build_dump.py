"""
0단계 입력 만들기: AI Hub 원본 -> track_point 형식 CSV

왜 필요한가
  기존 DB(track_point)는 'korea_gps_merged.csv'(AI Hub Training만 합친 파일)로 만들어졌다.
  AI Hub는 한 기록을 5분 창으로 자르고 창마다 1분 조각 5개 중 1개를 Validation(또는 비공개 Test)으로 뺐다.
  Validation을 함께 쓰면 세션 안 61초 구멍이 절반으로 준다 (서울 기준 14.8% -> 7.2%).

규칙 (기존 DB와 같게, config의 DUMP_* 값)
  1. AIHUB_SPLITS의 GPS 조각 CSV를 모두 읽는다. 파일명: TMC-GPS-{사용자}-{레코드}-{창id+순번}-Dataset.csv
  2. 좌표가 빈 행을 빼고, 남은 좌표 행이 DUMP_MIN_PIECE_POINTS 미만인 조각은 통째로 뺀 뒤,
     accuracy > DUMP_ACC_MAX_M 인 행을 뺀다
  3. (사용자, 시각) 중복은 첫 행만 남긴다 (겹치는 레코드의 같은 점. 좌표까지 같음을 확인)
  4. 사용자별 시간순으로, 간격이 DUMP_SESSION_GAP_S를 넘으면 새 세션
  5. session_id = uuid5(NAMESPACE_URL, f"{SESSION_UUID_PREFIX}/{user}/{start_ts}")

출력
  DUMP_OUT_POINTS   session_id, ts(epoch ms), geom('SRID=4326;POINT(lon lat)'), model_version
                    -> 기존 track_point.csv와 같은 칼럼·형식 (s0_extract가 geom을 파싱)
  DUMP_OUT_SESSIONS session_id, user, start_ts, end_ts, start_at, end_at, duration_min, point_count,
                    source_records, detail_label
                    -> 기존 track_point_sessions.csv 칼럼 + detail_label (AI Hub 라벨의 세부 코드, 세션 안에서 하나)

실행
  python s0_build_dump.py
  python s0_build_dump.py --verify <기존 track_point_sessions.csv>
      Training만으로 세션을 다시 만들어 기존 세션과 시작·끝 시각, 포인트 수를 대조한다 (규칙 재현 확인용)
"""
import argparse
import io
import uuid
import zipfile

import numpy as np
import pandas as pd

import config as C

SPLIT_PREFIX = {"Training": ("TS", "TL"), "Validation": ("VS", "VL")}


def _read_zip_csvs(path):
    """
    zip 안의 조각 CSV를 모두 읽어 하나로 붙인다.
    반환: DataFrame (각 CSV의 칼럼 + user, rec, piece)
          piece = '{창id}{순번}' (파일명의 5번째 필드)
    """
    frames = []
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if not name.endswith(".csv"):
                continue
            # TMC-GPS-00000050-634d...-56a7...0-Dataset.csv -> [TMC, GPS, user, rec, piece, Dataset]
            parts = name.strip("/").rsplit("/", 1)[-1][:-len(".csv")].split("-")
            d = pd.read_csv(io.BytesIO(z.read(name)), encoding="utf-8-sig")
            frames.append(d.assign(user=parts[2], rec=parts[3], piece=parts[4]))
    return pd.concat(frames, ignore_index=True)


def load_aihub(splits):
    """
    AI Hub GPS와 라벨을 읽는다.

    반환: (gps, detail)
      gps   : [timestamp, accuracy, latitude, longitude, altitude, user, rec, piece, split]
      detail: Series, index=(user, rec, piece) -> detail_label (조각 안에서 값이 하나인지 확인)
    """
    gps, lab = [], []
    for split in splits:
        s, l = SPLIT_PREFIX[split]
        base = C.AIHUB_DIR / split
        gps.append(_read_zip_csvs(base / "01.원천데이터" / f"{s}_교통수단판별_3.GPS_02.BIKE.zip").assign(split=split))
        lab.append(_read_zip_csvs(base / "02.라벨링데이터" / f"{l}_교통수단판별_2.BIKE.zip"))
    gps, lab = pd.concat(gps, ignore_index=True), pd.concat(lab, ignore_index=True)
    per_piece = lab.groupby(["user", "rec", "piece"]).detail_label
    if (per_piece.nunique() > 1).any():
        raise ValueError("한 조각 안에서 detail_label이 바뀌는 경우가 있습니다. 세션 메타에 하나로 담을 수 없습니다.")
    return gps, per_piece.first()


def clean_points(gps):
    """
    규칙 2-3: 좌표 빈 행, 좌표 행이 적은 조각, accuracy 초과 행 제거 후 (사용자, 시각) 중복 제거.
    중복은 분할 순서(Training 먼저), 조각 id 순으로 정렬한 뒤 첫 행을 남긴다 (실행마다 같은 결과).
    반환: (정제된 DataFrame, 단계별 제거 수 dict)
    """
    log = {"input_rows": len(gps)}
    g = gps.dropna(subset=["latitude", "longitude"])
    log["no_coord"] = log["input_rows"] - len(g)
    # 좌표 행이 너무 적은 조각은 통째로 뺀다 (대부분 좌표가 전부 빈 조각의 잔여 몇 행)
    n = len(g)
    size = g.groupby(["user", "rec", "piece"]).timestamp.transform("size")
    g = g[size >= C.DUMP_MIN_PIECE_POINTS]
    log["small_piece"] = n - len(g)
    n = len(g)
    g = g[g.accuracy <= C.DUMP_ACC_MAX_M]
    log["accuracy_gt_max"] = n - len(g)
    order = {s: i for i, s in enumerate(C.AIHUB_SPLITS)}
    g = g.assign(_o=g.split.map(order)).sort_values(["user", "timestamp", "_o", "piece"], kind="mergesort")
    n = len(g)
    g = g.drop_duplicates(["user", "timestamp"], keep="first").drop(columns="_o")
    log["dup_user_ts"] = n - len(g)
    log["output_rows"] = len(g)
    return g.reset_index(drop=True), log


def sessionize(g):
    """
    규칙 4-5: 사용자별 시간순으로 DUMP_SESSION_GAP_S를 넘는 간격에서 세션을 나누고 session_id를 붙인다.
    입력은 (user, timestamp) 정렬 상태여야 한다 (clean_points 출력).
    반환: g + session_id 칼럼
    """
    dt = g.groupby("user").timestamp.diff()
    new = dt.isna() | (dt > C.DUMP_SESSION_GAP_S * 1000)
    seq = new.cumsum()
    first = g.groupby(seq).agg(user=("user", "first"), start_ts=("timestamp", "min"))
    sid = {k: str(uuid.uuid5(uuid.NAMESPACE_URL, f"{C.SESSION_UUID_PREFIX}/{u}/{t}"))
           for k, u, t in zip(first.index, first.user, first.start_ts)}
    return g.assign(session_id=seq.map(sid))


def session_meta(g, detail):
    """
    세션 메타 (기존 track_point_sessions.csv 형식 + detail_label).
    source_records: 세션에 들어간 레코드. 여러 개면 ';'로 잇는다 (기존 파일은 전부 하나였음).
    """
    g = g.assign(detail=[detail.get((u, r, p)) for u, r, p in zip(g.user, g.rec, g.piece)])
    s = g.groupby("session_id", sort=False).agg(
        user=("user", "first"), start_ts=("timestamp", "min"), end_ts=("timestamp", "max"),
        point_count=("timestamp", "size"),
        source_records=("rec", lambda v: ";".join(dict.fromkeys(v))),
        detail_label=("detail", lambda v: ";".join(str(x) for x in dict.fromkeys(v))),
    ).reset_index()
    iso = lambda ms: pd.to_datetime(ms, unit="ms", utc=True).dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    s["start_at"], s["end_at"] = iso(s.start_ts), iso(s.end_ts)
    s["duration_min"] = ((s.end_ts - s.start_ts) / 60000).round(1)
    cols = ["session_id", "user", "start_ts", "end_ts", "start_at", "end_at", "duration_min", "point_count",
            "source_records", "detail_label"]
    return s[cols]


def to_track_point(g):
    """track_point 형식. geom은 파이썬 float repr(왕복 손실 없는 최단 표기)로 쓴다 (기존 파일과 같은 표기)."""
    geom = [f"SRID=4326;POINT({lo!r} {la!r})" for lo, la in zip(g.longitude.tolist(), g.latitude.tolist())]
    return pd.DataFrame({"session_id": g.session_id.to_numpy(), "ts": g.timestamp.to_numpy(np.int64),
                         "geom": geom, "model_version": C.DUMP_MODEL_VERSION})


def verify(old_sessions_path):
    """
    Training만으로 세션을 만들어 기존 세션 메타와 (user, start_ts)로 대조한다.
    반환: dict (기존 수, 새 수, 시작 일치, 끝 일치, 포인트 수 일치)
    """
    gps, _ = load_aihub(["Training"])
    g, _ = clean_points(gps)
    s = sessionize(g).groupby("session_id").agg(user=("user", "first"), start_ts=("timestamp", "min"),
                                                end_ts=("timestamp", "max"), n=("timestamp", "size"))
    old = pd.read_csv(old_sessions_path, dtype={"user": str, "session_id": str})
    m = s.merge(old, on=["user", "start_ts"], suffixes=("", "_old"))
    return {"old": len(old), "new": len(s), "start_match": len(m),
            "end_match": int((m.end_ts == m.end_ts_old).sum()), "count_match": int((m.n == m.point_count).sum())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", metavar="OLD_SESSIONS_CSV", help="Training만으로 기존 세션 규칙 재현 확인")
    args = ap.parse_args()
    if args.verify:
        print(f"[s0_build_dump] 규칙 재현 확인: {verify(args.verify)}")
        return

    C.setup_environment()
    gps, detail = load_aihub(C.AIHUB_SPLITS)
    g, log = clean_points(gps)
    g = sessionize(g)
    meta = session_meta(g, detail)
    tp = to_track_point(g)
    tp.to_csv(C.DUMP_OUT_POINTS, index=False)
    meta.to_csv(C.DUMP_OUT_SESSIONS, index=False)

    kst = lambda ms: pd.Timestamp(ms, unit="ms", tz="UTC").tz_convert(C.TIMEZONE)
    print(f"[s0_build_dump] 분할 {C.AIHUB_SPLITS}, 정제 {log}")
    print(f"  포인트 {len(tp):,}, 세션 {len(meta):,}, 사용자 {meta.user.nunique()}, "
          f"기간(KST) {kst(tp.ts.min())} ~ {kst(tp.ts.max())}")
    print(f"  레코드가 여러 개인 세션 {int(meta.source_records.str.contains(';').sum())}, "
          f"detail_label {meta.detail_label.value_counts().to_dict()}")
    print(f"  저장: {C.DUMP_OUT_POINTS}\n        {C.DUMP_OUT_SESSIONS}")


if __name__ == "__main__":
    main()
