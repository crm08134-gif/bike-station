"""
1단계 노드 -> 링크 변환 검증 (엔진 없이)

경로 좌표를 노드 순서와 링크로 바꾸는 규칙만 확인한다.
엔진 매칭 자체는 s1_compare.py의 실데이터 지표로 본다.

실행: python tests/test_s1.py
"""
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="s1_test_"))
os.environ["BIKE_BASE_DIR"] = str(TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import s1_match as SM  # noqa: E402


def main():
    fails = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)

    # --- 링크: 방향 없음, 도로망에 없는 쌍(건너뜀)은 빼고 센다 ---
    adj = {(1, 2), (2, 3), (3, 4), (7, 8)}
    pairs, jump = SM.nodes_to_pairs([1, 2, 3, 2, 3, 4, 7, 8], adj)
    check(pairs == [(1, 2), (2, 3), (2, 3), (2, 3), (3, 4), (7, 8)] and jump == 1, f"링크 {pairs}, 건너뜀 {jump}")
    pairs_rev, _ = SM.nodes_to_pairs([4, 3, 2, 1], adj)
    check(pairs_rev == [(3, 4), (2, 3), (1, 2)], f"반대 방향 링크 {pairs_rev}")

    # --- 경로 좌표 -> 노드: 노드 위 점만 (0.3m 안), 연속 중복 제거 ---
    import numpy as np
    from scipy.spatial import cKDTree
    nxy = np.array([[0, 0], [20, 0], [40, 0]], float)
    route = np.array([[0.05, 0], [7.3, 1.2], [19.95, 0.02], [20.0, 0.0], [31, 0.5], [40, 0.1]])
    got = SM.nodes_from_coords(route, cKDTree(nxy), np.array([101, 102, 103]), 0.3)
    check(got == [101, 102, 103], f"좌표 -> 노드 {got}")

    # --- 시작·끝 반쪽 링크: 경로가 링크 (1,2) 중간(x=5)에서 시작 -> 반대편 노드 1 ---
    xy = {1: np.array([0.0, 0.0]), 2: np.array([20.0, 0.0]), 3: np.array([20.0, 20.0])}
    nb = SM.neighbors({(1, 2), (2, 3)})
    check(SM.end_edge(np.array([5.0, 0.3]), 2, nb, xy, 1.0) == 1, "시작 반쪽 링크를 못 찾음")
    check(SM.end_edge(np.array([5.0, 3.0]), 2, nb, xy, 1.0) is None, "1m 밖인데 반쪽 링크로 잡음")

    if fails:
        print("실패 항목:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("모든 검증 통과")


if __name__ == "__main__":
    main()
