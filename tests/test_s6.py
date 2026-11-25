"""
6단계 MILP 검증 (합성 접근점 표)

1) 5단계 테스트와 같은 작은 표에서 p별 최적값을 손으로 구한 값과 비교
   R0 (w=2, 2000)  c0:0    c1:300   c2:900   c3:1600
   R1 (w=1, 1000)  c1:100  c4:700
   R2 (w=1,  400)  c0:0    c2:400        -> 간격 최대 400 < 500: 전처리에서 빠진다
   R3 (w=1,  800)  c5:0    c6:500        -> 정확히 500은 인정
   R9 (w=1)        행 없음 (도달 불가)
   c7: 어떤 경로에도 안 걸림 -> 전처리에서 빠진다
2) 무작위 작은 표에서 전수 탐색(후보지 조합 전부를 5단계 평가 함수로 잰 최댓값)과 MILP 최적값 비교, 두 변형
3) MILP 목적값 = 선택된 후보지를 평가 함수로 다시 잰 값

실행: python tests/test_s6.py
"""
import itertools
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

os.environ["BIKE_BASE_DIR"] = str(Path(tempfile.mkdtemp(prefix="s6_test_")))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import s5_objective as S5  # noqa: E402
import s6_milp as S6  # noqa: E402

ROWS = [  # route_id, cand_id, a, weight, route_len
    (0, 0, 0, 2, 2000), (0, 1, 300, 2, 2000), (0, 2, 900, 2, 2000), (0, 3, 1600, 2, 2000),
    (1, 1, 100, 1, 1000), (1, 4, 700, 1, 1000),
    (2, 0, 0, 1, 400), (2, 2, 400, 1, 400),
    (3, 5, 0, 1, 800), (3, 6, 500, 1, 800),
]


def fixed_evaluator(mode="absolute", min_ride=500):
    acc = pd.DataFrame(ROWS, columns=["route_id", "cand_id", "a", "weight", "route_len"])
    return S5.Evaluator(acc, mode=mode, min_ride_m=min_ride, route_ids=[0, 1, 2, 3, 9],
                        route_weight=[2, 1, 1, 1, 1], route_len=[2000, 1000, 400, 800, 0], cand_ids=list(range(8)))


def brute(ev, p, mode):
    """후보지 p개 이하 조합 전부 (걸리는 후보지만). 목적함수가 선택 추가로 줄지 않으므로 정확히 p개만 봐도 되지만 안전하게 전부."""
    cands = np.unique(ev._acc.cand_id)
    best = 0.0
    for r in range(0, min(p, len(cands)) + 1):
        for comb in itertools.combinations(cands, r):
            best = max(best, ev.evaluate(comb, mode)[0])
    return best


def random_access(rng, n_routes, n_cands):
    rows = []
    for r in range(n_routes):
        L = float(rng.uniform(300, 3000))
        k = int(rng.integers(1, 6))
        cs = rng.choice(n_cands, size=k, replace=False)
        for c in cs:
            rows.append((r, int(c), float(rng.uniform(0, L)), int(rng.integers(1, 4)), L))
    return pd.DataFrame(rows, columns=["route_id", "cand_id", "a", "weight", "route_len"])


def main():
    fails = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)

    # ---- 1) 고정 표: 손으로 구한 최적값
    ev = fixed_evaluator()
    model = S6.MilpModel(ev)
    sz = model.size()
    check(sz["n_routes"] == 3 and sz["n_routes_dropped"] == 2, f"경로 전처리 {sz}")        # R2, R9 제외
    check(sz["n_cands"] == 7 and sz["n_cands_dropped"] == 1, f"후보지 전처리 {sz}")          # c7 제외 (c0, c2는 R0에 남음)
    expect_abs = {0: 0, 1: 0, 2: 3200, 3: 3200, 4: 3800, 5: 3800, 6: 4300, 7: 4300}
    # p=2: R0 c0,c3 = 2*1600. p=4: + R1 c1,c4 = 600. p=6: + R3 c5,c6 = 500 (정확히 500 인정)
    for p, f_exp in expect_abs.items():
        sol = model.solve(p, "absolute", time_limit=60)
        chk = S6.check_solution(model, sol)
        check(sol["status"] == "optimal", f"p={p} 상태 {sol['status']}")
        check(abs(sol["obj_milp"] - f_exp) < 1e-6, f"p={p} MILP {sol['obj_milp']} (기대 {f_exp})")
        check(chk["ok"] and abs(chk["obj_eval"] - f_exp) < 1e-6, f"p={p} 평가 함수 {chk['obj_eval']}")
        check(chk["n_selected"] <= p, f"p={p} 선택 {chk['n_selected']}개")
    # p=3: 3번째 선택은 어떤 경로의 혜택도 늘리지 못한다 -> 쓰임 2개
    chk = S6.check_solution(model, model.solve(3, "absolute"))
    check(chk["n_used"] == 2, f"p=3 쓰인 후보지 {chk['n_used']}")

    # ratio: p=2 R0 2*0.8=1.6, p=4 + R1 0.6 = 2.2 vs R0 + R3(0.625) = 2.225 -> R3
    for p, f_exp in {2: 1.6, 4: 1.6 + 0.625, 6: 1.6 + 0.6 + 0.625}.items():
        sol = model.solve(p, "ratio")
        check(abs(sol["obj_milp"] - f_exp) < 1e-6, f"ratio p={p} MILP {sol['obj_milp']} (기대 {f_exp})")

    # 최소 탑승 0: R2(0~400)도 혜택. c0, c2를 고르면 R0 900*2 + R2 400 = 2200 < c0,c3 3200
    e0 = fixed_evaluator(min_ride=0)
    m0 = S6.MilpModel(e0)
    check(m0.size()["n_routes"] == 4, f"최소 탑승 0 경로 {m0.size()}")
    for p in range(0, 8):
        sol = m0.solve(p, "absolute")
        check(abs(sol["obj_milp"] - brute(e0, p, "absolute")) < 1e-6, f"최소 탑승 0 p={p} {sol['obj_milp']}")

    # 최소 탑승 700: R0 c1-c2(600)은 인정 안 됨
    e7 = fixed_evaluator(min_ride=700)
    m7 = S6.MilpModel(e7)
    for p in range(0, 8):
        sol = m7.solve(p, "absolute")
        f_b = brute(e7, p, "absolute")
        check(abs(sol["obj_milp"] - f_b) < 1e-6, f"최소 탑승 700 p={p} {sol['obj_milp']} (전수 {f_b})")

    # ---- 2) 무작위 표: 전수 탐색과 비교
    rng = np.random.default_rng(0)
    n_inst = 0
    for inst in range(25):
        acc = random_access(rng, n_routes=int(rng.integers(3, 9)), n_cands=int(rng.integers(5, 10)))
        min_ride = float(rng.choice([0, 300, 500]))
        evr = S5.Evaluator(acc, min_ride_m=min_ride)
        mr = S6.MilpModel(evr)
        for mode in S5.MODES:
            for p in (1, 2, 3, 5):
                sol = mr.solve(p, mode, time_limit=60)
                chk = S6.check_solution(mr, sol)
                f_b = brute(evr, p, mode)
                tol = 1e-6 * max(1.0, abs(f_b))
                check(abs(sol["obj_milp"] - f_b) <= tol + 1e-4 * abs(f_b),
                      f"무작위 {inst} {mode} p={p} 최소탑승 {min_ride}: MILP {sol['obj_milp']} 전수 {f_b}")
                check(chk["ok"], f"무작위 {inst} {mode} p={p}: MILP {sol['obj_milp']} 평가 함수 {chk['obj_eval']}")
                n_inst += 1

    if fails:
        print("실패:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print(f"test_s6: 모두 통과 (무작위 비교 {n_inst}건)")


if __name__ == "__main__":
    main()
