"""
2단계 대표경로 압축 검증 (합성 데이터)

정답을 알고 있는 통행을 만들어 s2_compress.compress()에 넣고,
묶여야 할 것만 묶이는지 확인한다.

도로망: 동서 방향 대로 하나 (링크 0~9, 각 200m) + 골목 링크 몇 개.
  대로 서쪽 끝 x=950_000, 링크 k는 x = 950_000 + 200k ~ +200(k+1)

실행: python tests/test_s2.py
"""
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

TMP = Path(tempfile.mkdtemp(prefix="s2_test_"))
os.environ["BIKE_BASE_DIR"] = str(TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import config as C          # noqa: E402
import s2_compress as S2    # noqa: E402

rng = np.random.default_rng(0)
X0, Y0 = 950_010.0, 1_950_010.0      # 대로 서쪽 끝 (150m 격자 칸 안쪽으로 약간 들여서 잡음이 칸을 넘지 않게)
LEN = {k: 200.0 for k in range(10)}  # 대로 링크
LEN.update({100: 15.0, 101: 15.0,    # 짧은 골목
            200: 400.0,              # 긴 우회로
            300: 15.0})              # 대로 서쪽 끝 바깥의 짧은 진입로


def trips(prefix, n, seq, start, end, jitter_m=5.0):
    """같은 경로를 가는 통행 n개. 시종점에 잡음 jitter_m (m)."""
    return pd.DataFrame({
        "trip_id": [f"{prefix}_{i:03d}" for i in range(n)],
        "link_seq": [list(seq)] * n,
        "start_x": start[0] + rng.uniform(-jitter_m, jitter_m, n),
        "start_y": start[1] + rng.uniform(-jitter_m, jitter_m, n),
        "end_x": end[0] + rng.uniform(-jitter_m, jitter_m, n),
        "end_y": end[1] + rng.uniform(-jitter_m, jitter_m, n),
    })


def at(k):
    """대로 링크 k의 시작 지점 좌표"""
    return (X0 + 200 * k, Y0)


def build():
    parts = [
        # A: 대로 서쪽 끝 -> 동쪽 끝, 500건 (하나로 묶여야 함)
        trips("A_full", 500, range(10), at(0), at(10)),
        # B: 대로 중간(링크 5)에서 합류해 동쪽 끝까지. A와 링크가 겹치지만 시점이 달라 따로 남아야 함
        trips("B_midjoin", 40, range(5, 10), at(5), at(10)),
        # C: A와 같은 링크 시퀀스, 반대 방향 (방향 없이 합치므로 2-1에서 A와 묶여야 함)
        trips("C_reverse", 20, range(9, -1, -1), at(10), at(0)),
        # D: A와 링크 시퀀스는 같지만 시점이 대로 밖 350m 떨어진 곳 (다른 칸 -> 따로)
        trips("D_farstart", 10, range(10), (X0, Y0 + 350), at(10)),
        # E: A에 짧은 골목 하나만 더 들른 경로 (유사도 매우 높음: 2-2에서만 A와 묶여야 함)
        trips("E_alley", 30, [0, 1, 2, 100, 3, 4, 5, 6, 7, 8, 9], at(0), at(10)),
        # F: 링크 4를 긴 우회로로 바꾼 경로 (유사도 낮음: 2-2에서도 따로)
        trips("F_detour", 25, [0, 1, 2, 3, 200, 5, 6, 7, 8, 9], at(0), at(10)),
        # G: E와 같지만 종점이 200m 떨어짐 (유사도 높아도 끝점 조건으로 따로)
        trips("G_alley_farend", 15, [0, 1, 2, 101, 3, 4, 5, 6, 7, 8, 9], at(0), (X0 + 2000, Y0 + 200)),
        # I: A 앞에 진입로(300) 하나를 더한 경로. 표준 방향이 A와 반대로 잡힌다 ((9,...,0,300) < (300,0,...,9)).
        #    2-2에서 A와 묶이고, 방향을 A에 맞춰 뒤집어야 대표 시종점이 섞이지 않는다
        trips("I_prefix", 12, [300] + list(range(10)), (X0 - 10, Y0), at(10)),
    ]
    return pd.concat(parts, ignore_index=True)


def group_of(trip_route, prefix):
    return set(trip_route[trip_route.trip_id.str.startswith(prefix)].route_id)


def main():
    fails = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)

    df = S2.prepare(build())

    # --- snap: 같은 칸 / 다른 칸 ---
    gx, gy = S2.snap([0.0, 149.9, 150.0, 299.9], [0.0, 0.0, 0.0, 0.0], 150)
    check(list(gx) == [0, 0, 1, 1], f"snap 결과 {list(gx)}")

    # --- 가중 자카드 손계산 ---
    #   A(0~9, 2000m) vs E(A + 골목 15m): 2000 / 2015
    wj = S2.weighted_jaccard(range(10), [0, 1, 2, 100, 3, 4, 5, 6, 7, 8, 9], LEN)
    check(abs(wj - 2000 / 2015) < 1e-12, f"가중 자카드 A-E {wj}")
    #   A vs F(링크 4 -> 400m 우회): 1800 / 2400. 개수 자카드였다면 9/11=0.82로 높게 나왔을 것
    wj_f = S2.weighted_jaccard(range(10), [0, 1, 2, 3, 200, 5, 6, 7, 8, 9], LEN)
    check(abs(wj_f - 1800 / 2400) < 1e-12, f"가중 자카드 A-F {wj_f}")

    # --- 2-1 정확 일치 (2-2 꺼짐) ---
    routes, tr, log = S2.compress(df, grid_m=150, sim_threshold=None)
    check(log["n_trips"] == len(df), "원본 통행 수")
    check(log["n_routes_exact"] == 7, f"2-1 경로 수 {log['n_routes_exact']} (기대 7: A+C, B, D, E, F, G, I)")
    check(routes.weight.sum() == len(df), "가중치 합 != 통행 수")
    a = group_of(tr, "A_full")
    check(len(a) == 1 and routes.set_index("route_id").weight[next(iter(a))] == 520, "A 500건 + 반대 방향 C 20건이 가중치 520 경로 하나가 아님")
    check(group_of(tr, "C_reverse") == a, "2-1: 반대 방향 C가 A와 묶이지 않음")
    check(tr[tr.trip_id.str.startswith("C_")].reversed.all() and not tr[tr.trip_id.str.startswith("A_")].reversed.any(),
          "2-1: reversed 표시 오류")
    for p in ["B_midjoin", "D_farstart", "E_alley", "F_detour", "G_alley_farend", "I_prefix"]:
        check(len(group_of(tr, p)) == 1 and group_of(tr, p) != group_of(tr, "A_full"), f"2-1: {p}가 A와 묶이거나 쪼개짐")
    check(log["weights_exact"]["max"] == 520 and log["weights_exact"]["top10"][:2] == [520, 40], f"가중치 분포 {log['weights_exact']}")
    check(log["n_distinct_seq"] == 6, f"시퀀스만 본 개수 {log['n_distinct_seq']} (A·C·D가 같은 시퀀스라 6)")
    check(log["endpoint_spread_m"]["max"] < 20, f"2-1 끝점 거리 {log['endpoint_spread_m']}")
    check("건너뜀" in str(log["similarity"]), "목표 이하인데 2-2가 돌았음")

    # --- 2-2 유사도 묶기 (목표를 낮춰 강제로 켬, 임계값 0.9) ---
    routes2, tr2, log2 = S2.compress(df, length=LEN, grid_m=150, sim_threshold=0.9, target=1)
    check(isinstance(log2["similarity"], dict), f"2-2가 실행되지 않음: {log2['similarity']}")
    check(group_of(tr2, "E_alley") == group_of(tr2, "A_full"), "2-2: A와 E(골목 하나 차이)가 묶이지 않음")
    check(group_of(tr2, "I_prefix") == group_of(tr2, "A_full"), "2-2: A와 I(표준 방향 반대, 진입로 하나 차이)가 묶이지 않음")
    check(not tr2[tr2.trip_id.str.startswith("I_")].reversed.any(), "2-2: I 방향이 A에 맞춰지지 않음")
    check(log2["endpoint_spread_m"]["max"] < 30, f"2-2 끝점 거리 {log2['endpoint_spread_m']} (방향이 섞이면 약 2000m)")
    check(group_of(tr2, "F_detour") != group_of(tr2, "A_full"), "2-2: F(긴 우회)가 A와 묶임")
    check(group_of(tr2, "G_alley_farend") != group_of(tr2, "A_full"), "2-2: G(종점 200m 차이)가 A와 묶임")
    check(group_of(tr2, "B_midjoin") != group_of(tr2, "A_full"), "2-2: B(중간 합류)가 A와 묶임")
    check(log2["n_routes_final"] == 5, f"2-2 후 경로 수 {log2['n_routes_final']} (기대 5: A+C+E+I, B, D, F, G)")
    # 2-3: A+C+E+I 그룹의 대표 = 최빈 시퀀스(A), 가중치 = 500 + 20 + 30 + 12
    r = routes2.set_index("route_id").loc[next(iter(group_of(tr2, "A_full")))]
    check(r.link_seq == tuple(range(10)) and r.weight == 562 and r.n_distinct_seq == 3
          and abs(r.start_x - X0) < 10 and abs(r.end_x - (X0 + 2000)) < 10,
          f"2-3 대표경로 {r.link_seq} 가중치 {r.weight}")

    # --- LSH 누락률: 무작위 경로에서 전수 비교 결과와 대조 ---
    recall, n_true = lsh_recall(threshold=0.8)
    check(recall >= 0.99, f"LSH 후보 누락: 재현율 {recall:.3f} (정답 쌍 {n_true})")

    # --- 2-5 검증 틀: 끝점 격자 칸만 보는 목적함수면 2-1 압축 오차가 0이어야 함 ---
    cand = pd.DataFrame({"x": [X0 + 200 * k for k in range(11)], "y": Y0})
    res, summ = S2.validate_compression(toy_objective, df, routes, cand, p=3, n_solutions=20)
    check(len(res) == 20, "검증 반복 수")
    check(summ["rel_err_max"] < 1e-9 and summ["verdict"] == "진행", f"2-1 압축 오차 {summ}")
    res2, summ2 = S2.validate_compression(toy_objective, df, routes2, cand, p=3, n_solutions=20)

    # --- 출력 ---
    S2.print_report(log)
    S2.print_report(log2)
    print(f"LSH 재현율 (임계값 0.8, 전수 비교 정답 쌍 {n_true}개): {recall:.3f}")
    print(f"2-5 틀 (장난감 목적함수, 해 20개): 2-1 최대 오차 {summ['rel_err_max']:.4f}, "
          f"2-2 최대 오차 {summ2['rel_err_max']:.4f} -> {summ2['verdict']}")
    if fails:
        print("\n실패 항목:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("\n모든 검증 통과")


def toy_objective(routes, sites, reach=150):
    """장난감 목적함수: 시점과 종점 모두 대여소 reach(m) 안에 있는 경로의 가중치 합."""
    s = sites[["x", "y"]].to_numpy()

    def near(x, y):
        return (np.hypot(x[:, None] - s[None, :, 0], y[:, None] - s[None, :, 1]) <= reach).any(axis=1)
    ok = near(routes.start_x.to_numpy(), routes.start_y.to_numpy()) & near(routes.end_x.to_numpy(), routes.end_y.to_numpy())
    return float(routes.weight.to_numpy()[ok].sum())


def lsh_recall(threshold, n=300, n_links=400):
    """
    무작위 경로(격자 도로망 위의 연속 링크 구간 + 변형) n개를 만들고,
    전수 비교에서 가중 자카드 >= threshold인 쌍을 LSH 경로가 모두 합쳤는지 확인한다.
    끝점 조건은 끄고(거리 무한대) 유사도만 본다.
    """
    r = np.random.default_rng(1)
    length = {l: float(r.uniform(10, 300)) for l in range(n_links)}
    base = [list(range(s, s + r.integers(10, 30))) for s in r.integers(0, n_links - 30, 60)]
    seqs = []
    for _ in range(n):
        s = list(base[r.integers(len(base))])
        for _ in range(r.integers(0, 4)):          # 링크 몇 개 바꾸기
            s[r.integers(len(s))] = int(r.integers(n_links))
        seqs.append(tuple(s))
    groups = pd.DataFrame({"link_seq": seqs, "start_x": 0.0, "start_y": 0.0, "end_x": 0.0, "end_y": 0.0})
    lab, _ = S2.similarity_groups(groups, length, threshold, endpoint_m=np.inf)
    truth = [(i, j) for i in range(n) for j in range(i + 1, n)
             if S2.weighted_jaccard(seqs[i], seqs[j], length) >= threshold]
    hit = sum(lab[i] == lab[j] for i, j in truth)
    return (hit / len(truth) if truth else 1.0), len(truth)


if __name__ == "__main__":
    main()
