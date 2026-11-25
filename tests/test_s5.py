"""
5단계 평가 함수 검증 (합성 접근점 표)

경로 (가중치, 길이)와 걸린 후보지의 a (m)
  R0 (w=2, 2000)  c0:0    c1:300   c2:900   c3:1600
  R1 (w=1, 1000)  c1:100  c4:700
  R2 (w=1,  400)  c0:0    c2:400                 -> 간격 최대 400 < 500 : 어떤 해로도 혜택 0
  R3 (w=1,  800)  c5:0    c6:500                 -> 간격 정확히 500 : 혜택 인정 (미만일 때만 0)
  R9 (w=1)        행 없음 (도달 불가)             -> 혜택 0, 배열에는 자리 있음
후보지 c7: 어떤 경로에도 안 걸림 (골라도 값 변화 없음)

실행: python tests/test_s5.py
"""
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

os.environ["BIKE_BASE_DIR"] = str(Path(tempfile.mkdtemp(prefix="s5_test_")))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import s5_objective as S5  # noqa: E402

ROWS = [  # route_id, cand_id, a, weight, route_len
    (0, 0, 0, 2, 2000), (0, 1, 300, 2, 2000), (0, 2, 900, 2, 2000), (0, 3, 1600, 2, 2000),
    (1, 1, 100, 1, 1000), (1, 4, 700, 1, 1000),
    (2, 0, 0, 1, 400), (2, 2, 400, 1, 400),
    (3, 5, 0, 1, 800), (3, 6, 500, 1, 800),
]
ROUTE_IDS = [0, 1, 2, 3, 9]


def make(mode="absolute", min_ride=500, shuffle=False):
    acc = pd.DataFrame(ROWS, columns=["route_id", "cand_id", "a", "weight", "route_len"])
    if shuffle:   # 입력 순서와 상관없이 같아야 한다 (평가기가 route_id, a로 다시 정렬)
        acc = acc.sample(frac=1, random_state=3)
    return S5.Evaluator(acc, mode=mode, min_ride_m=min_ride, route_ids=ROUTE_IDS,
                        route_weight=[2, 1, 1, 1, 1], route_len=[2000, 1000, 400, 800, 0],
                        cand_ids=list(range(8)))


def main():
    fails = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)

    ev = make()
    cases = [  # 선택 집합, 기대 목적값 absolute, 기대 혜택 배열 absolute
        (set(), 0, [0, 0, 0, 0, 0]),
        ({1}, 0, [0, 0, 0, 0, 0]),                         # 경로마다 1개뿐
        ({1, 2}, 1200, [600, 0, 0, 0, 0]),                  # R0: 900-300
        ({0, 2}, 1800, [900, 0, 0, 0, 0]),                  # R2: 400 < 500 -> 0
        ({0, 3}, 3200, [1600, 0, 0, 0, 0]),
        ({0, 1, 2, 3}, 3200, [1600, 0, 0, 0, 0]),           # 중간 대여소는 값에 영향 없음
        ({1, 4}, 600, [0, 600, 0, 0, 0]),
        ({5, 6}, 500, [0, 0, 0, 500, 0]),                   # 정확히 500은 인정
        ({5, 6, 7}, 500, [0, 0, 0, 500, 0]),                # 안 걸린 후보지 c7
        (set(range(8)), 3200 + 600 + 500, [1600, 600, 0, 500, 0]),
    ]
    for sel, f_exp, b_exp in cases:
        for fn in (ev.evaluate, ev.evaluate_loop):
            f, b = fn(sel)
            check(abs(f - f_exp) < 1e-9 and np.allclose(b, b_exp), f"{fn.__name__} {sel}: {f}, {b} (기대 {f_exp}, {b_exp})")

    # ratio: 혜택 / 경로 길이
    er = make("ratio")
    f, b = er.evaluate({1, 2})
    check(abs(f - 2 * 600 / 2000) < 1e-12 and np.allclose(b, [0.3, 0, 0, 0, 0]), f"ratio {{1,2}}: {f}, {b}")
    f, b = er.evaluate(set(range(8)))
    check(abs(f - (2 * 0.8 + 0.6 + 500 / 800)) < 1e-12, f"ratio 전체: {f}, {b}")
    # 호출할 때 변형을 바꿀 수 있다
    check(abs(ev.evaluate({1, 2}, "ratio")[0] - 0.6) < 1e-12, "mode 인자 무시됨")

    # 최소 탑승 거리 설정: 0이면 R2의 400도 인정
    e0 = make(min_ride=0)
    f, b = e0.evaluate({0, 2})
    check(abs(f - (1800 + 400)) < 1e-9 and np.allclose(b, [900, 0, 400, 0, 0]), f"최소 탑승 0: {f}, {b}")

    # 입력 순서 무관
    es = make(shuffle=True)
    for sel, f_exp, _ in cases:
        check(abs(es.evaluate(sel)[0] - f_exp) < 1e-9, f"섞은 입력 {sel}")

    # 목록에 없는 후보지 id는 오류
    for fn in (ev.evaluate, ev.evaluate_loop):
        try:
            fn({1, 99})
            fails.append(f"{fn.__name__}: 없는 id를 받아들임")
        except ValueError:
            pass

    # 무작위 해: 벡터화 = 반복문, 두 변형
    rng = np.random.default_rng(0)
    for _ in range(200):
        sel = rng.choice(8, size=rng.integers(0, 9), replace=False)
        for m in S5.MODES:
            f_v, b_v = ev.evaluate(sel, m)
            f_l, b_l = ev.evaluate_loop(sel, m)
            if abs(f_v - f_l) > 1e-9 or not np.allclose(b_v, b_l):
                fails.append(f"벡터화 != 반복문 {sorted(sel)} {m}")
                break

    # 상한: 전부 고른 해, 어떤 해로도 혜택 0인 경로 = R2, R9
    ub = S5.upper_bound(ev)
    check(ub["obj_absolute"] == 4300 and ub["never_route_ids"] == [2, 9], f"상한 {ub}")

    if fails:
        print("실패:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("test_s5: 모두 통과")


if __name__ == "__main__":
    main()
