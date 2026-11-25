"""
여정 잇기 규칙 검증 (합성 데이터)

같은 레코드 안 연속 조각을 공백 동안의 직선 이동속도로 이을지 정하는지 확인한다.

실행: python tests/test_s0_join.py
"""
import os
import sys
import tempfile
from pathlib import Path

import pandas as pd

TMP = Path(tempfile.mkdtemp(prefix="s0j_test_"))
os.environ["BIKE_BASE_DIR"] = str(TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import config as C       # noqa: E402
import s0_join as J      # noqa: E402

T0 = 1_665_000_000_000


def piece(tid, sid, t_start_s, t_end_s, x0, x1):
    """x축으로 달린 조각 하나 (시점 x0, 종점 x1, y=0)"""
    return {"trip_id": tid, "session_id": sid, "start_ts": T0 + t_start_s * 1000, "end_ts": T0 + t_end_s * 1000,
            "start_x": float(x0), "start_y": 0.0, "end_x": float(x1), "end_y": 0.0,
            "duration_s": float(t_end_s - t_start_s), "dist_m": float(abs(x1 - x0))}


def main():
    fails = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)

    trips = pd.DataFrame([
        # 레코드 R1: a -> b 공백 300초 동안 1,250m (15km/h) : 잇는다
        piece("a", "sa", 0, 240, 0, 1000),
        piece("b", "sb", 540, 780, 2250, 3250),
        # b -> c 공백 300초 동안 50m (0.6km/h, 멈춰 있음) : 새 여정
        piece("c", "sc", 1080, 1320, 3300, 4300),
        # 레코드 R2 (같은 사용자): c 끝에서 이어지는 속도여도 레코드가 다르면 새 여정
        piece("d", "sd", 1620, 1860, 5550, 6550),
        # 레코드 R3: 공백 300초 동안 5km (60km/h) : 새 여정
        piece("e", "se", 0, 240, 0, 1000),
        piece("f", "sf", 540, 780, 6000, 7000),
    ])
    meta = pd.DataFrame({"session_id": ["sa", "sb", "sc", "sd", "se", "sf"],
                         "user": ["u1", "u1", "u1", "u1", "u2", "u2"],
                         "source_records": ["R1", "R1", "R1", "R2", "R3", "R3"]})
    pieces = J.link_pieces(trips, meta).set_index("trip_id")
    jid = pieces.journey_id.to_dict()
    check(jid["a"] == jid["b"] == "a", f"a-b가 이어지지 않음: {jid}")
    check(jid["c"] == "c", "멈춰 있던 공백 뒤 c가 새 여정이 아님")
    check(jid["d"] == "d", "다른 레코드 d가 이어짐")
    check(jid["e"] == "e" and jid["f"] == "f", "60km/h 공백이 이어짐")
    check(pieces.piece_no["b"] == 1 and abs(pieces.gap_kmh["b"] - 15) < 1e-9, f"조각 번호/속도 {pieces.loc['b'].to_dict()}")

    # --- 끊긴 시간 상한 (5분 창 단위 비교): 상한 900초면 901·961초 공백은 잇고 1,261초는 끊는다 ---
    gap_trips = pd.DataFrame([
        piece("g1", "sg1", 0, 240, 0, 1000), piece("g2", "sg2", 1141, 1381, 4000, 5000),        # 공백 901초, 12km/h
        piece("g3", "sg3", 2342, 2582, 8000, 9000),                                             # 공백 961초, 11km/h
        piece("g4", "sg4", 3843, 4083, 12500, 13500),                                           # 공백 1,261초, 10km/h
    ])
    gap_meta = pd.DataFrame({"session_id": ["sg1", "sg2", "sg3", "sg4"], "user": "u3", "source_records": "R9"})
    gp = J.link_pieces(gap_trips, gap_meta, max_gap_s=900).set_index("trip_id")
    check(list(gp.journey_id) == ["g1", "g1", "g1", "g4"], f"상한 900초 결과 {gp.journey_id.to_dict()}")
    check(bool(gp.speed_ok["g4"]) and not bool(gp.joined["g4"]), "상한으로 끊긴 지점 표시 오류")
    gp_none = J.link_pieces(gap_trips, gap_meta, max_gap_s=None)
    check(gp_none.journey_id.nunique() == 1, "상한 없음인데 끊김")

    jr = J.build_journeys(trips, pieces.reset_index(), region_ids={"b"}).set_index("journey_id")
    a = jr.loc["a"]
    check(a.n_pieces == 2 and a.start_x == 0 and a.end_x == 3250 and a.span_s == 780 and a.obs_s == 480,
          f"여정 a 요약 {a.to_dict()}")
    check(bool(a.in_study_region) and not bool(jr.loc["c"].in_study_region), "연구 지역 표시 오류")

    print(pieces[["journey_id", "piece_no", "gap_before_s", "gap_kmh", "joined"]])
    print(f"잇는 속도 범위 {C.JOIN_RIDE_KMH} km/h")
    if fails:
        print("\n실패 항목:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("\n모든 검증 통과")


if __name__ == "__main__":
    main()
