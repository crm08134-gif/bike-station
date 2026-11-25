"""
2단계: 대표경로 압축

사실상 같은 통행을 하나로 묶어 최적화에 들어가는 경로 개수를 줄인다.
같은 길을 가는 통행 500건은 최적화 입장에서 가중치 500짜리 경로 1개와 같다.

입력  : data/interim/s1_routes.parquet  통행별 링크 시퀀스와 매칭 경로 시종점 (config의 입력 계약 참고)
        data/interim/s1_links.parquet   링크 길이 (2-2를 할 때만)
출력  : data/interim/s2_routes.parquet     대표경로: route_id, link_seq, weight, start_x, start_y, end_x, end_y
        data/interim/s2_trip_route.parquet 통행 -> 대표경로 대응: trip_id, route_id, reversed (2-5 검증과 추적용)
        data/interim/s2_stats.json         압축률, 가중치 분포, 사용한 설정값

처리 순서
  0.   방향 없애기      A→B와 B→A를 같은 경로로 본다 (2026-09-28 결정). orient() 참고
  2-1. 정확 일치 묶기   key = (snap(시점), snap(종점), tuple(link_seq))  (표준 방향 기준)
       끝점을 키에 넣는 이유: 링크 시퀀스만 보면, 대로 서쪽 끝부터 동쪽 끝까지 가는 통행과
       대로 중간에서 합류해 동쪽 끝까지 가는 통행이 많이 겹친다. 둘을 묶으면 대표경로는 긴 쪽이 되고,
       중간에서 합류한 사람까지 서쪽 끝에서 출발한 것으로 계산돼 없는 수요가 생기고 있는 수요는 빠진다.
       목적함수가 경로의 시작과 끝을 기준선으로 쓰기 때문이다.
  2-2. 유사도 기반 묶기 (조건부: 2-1 결과가 S2_TARGET_ROUTES보다 많고 S2_SIM_THRESHOLD가 정해졌을 때)
       링크 길이 가중 자카드. MinHash + LSH로 후보를 좁힌 뒤 정확한 값으로 거르고 union-find로 묶는다.
       시점끼리·종점끼리 S2_SIM_ENDPOINT_M을 넘게 떨어지면 유사도가 높아도 묶지 않는다.
  2-3. 대표경로 선정    그룹 안에서 통행 수가 가장 많은 링크 시퀀스. 가중치 = 그룹의 통행 수.
  2-4. 압축률 보고      원본 통행 수, 2-1 후 경로 수와 압축률, 가중치 분포 (최대, 중앙값, 상위 10개)
  2-5. 압축 오차 검증   validate_compression(): 5단계 평가 함수가 생기면 그쪽에서 호출한다.

실행
  python s2_compress.py            # 캐시가 있으면 건너뜀
  python s2_compress.py --force    # 강제 재계산
"""
import argparse
import json
import time
from collections import Counter

import numpy as np
import pandas as pd

import config as C

OUT_ROUTES = C.DATA_INTERIM / "s2_routes.parquet"
OUT_TRIP_ROUTE = C.DATA_INTERIM / "s2_trip_route.parquet"
OUT_STATS = C.DATA_INTERIM / "s2_stats.json"

ROUTE_COLUMNS = ["trip_id", "link_seq", "start_x", "start_y", "end_x", "end_y"]


# ===========================================================================
# 입력
# ===========================================================================
def load_routes(path=None):
    """1단계 산출물을 읽고 link_seq를 해시 가능한 tuple로 바꾼다."""
    path = path or C.S1_ROUTES_PATH
    if not path.exists():
        raise FileNotFoundError(f"1단계 맵매칭 산출물이 없습니다: {path}\n"
                                "config.py의 '2단계 입력 계약' 형식으로 1단계를 먼저 실행해야 합니다.")
    df = pd.read_parquet(path)
    missing = set(ROUTE_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"{path.name}에 칼럼이 없습니다: {sorted(missing)}")
    return prepare(df)


def prepare(df):
    """link_seq를 int tuple로 통일한다 (parquet list는 numpy 배열로 읽혀 해시가 안 된다)."""
    df = df[ROUTE_COLUMNS].copy()
    df["link_seq"] = [tuple(int(v) for v in s) for s in df.link_seq]
    empty = df.link_seq.map(len) == 0
    if empty.any():
        raise ValueError(f"링크 시퀀스가 빈 통행 {empty.sum()}개: 1단계에서 매칭 실패 통행은 빼고 저장해야 합니다.")
    return df.reset_index(drop=True)


def orient(df):
    """
    방향을 없앤다 (2026-09-28 결정: 경로는 방향 없이 합친다).
    A→B와 B→A를 같은 경로로 보도록, 링크 시퀀스와 뒤집은 시퀀스 중 사전순으로 작은 쪽을 표준 방향으로 삼고
    뒤집은 통행은 시종점 좌표도 맞바꾼다. flipped 칼럼에 뒤집었는지 남긴다.
    시퀀스만 보고 정하므로 같은 링크 시퀀스는 끝점 격자 칸과 상관없이 항상 같은 방향이 된다.
    앞뒤가 같은 시퀀스(왕복 등)는 시점 좌표가 종점보다 작은 쪽으로 정한다.
    """
    df = df.copy()
    fwd = df.link_seq
    rev = fwd.map(lambda s: s[::-1])
    start = list(zip(df.start_x, df.start_y))
    end = list(zip(df.end_x, df.end_y))
    flip = np.array([r < f or (r == f and e < s) for f, r, s, e in zip(fwd, rev, start, end)], dtype=bool)
    df["link_seq"] = [r if f else s for s, r, f in zip(fwd, rev, flip)]
    df = _swap_ends(df, flip)
    df["flipped"] = flip
    return df


def _swap_ends(df, mask):
    """mask인 행의 시점과 종점 좌표를 맞바꾼다."""
    df = df.copy()
    for a, b in [("start_x", "end_x"), ("start_y", "end_y")]:
        va, vb = df[a].to_numpy().copy(), df[b].to_numpy().copy()
        df[a] = np.where(mask, vb, va)
        df[b] = np.where(mask, va, vb)
    return df


def load_link_lengths(path=None):
    path = path or C.S1_LINKS_PATH
    links = pd.read_parquet(path, columns=["link_id", "length_m"])
    return dict(zip(links.link_id.astype(np.int64), links.length_m.astype(float)))


# ===========================================================================
# 2-1. 정확 일치 묶기
# ===========================================================================
def snap(x, y, grid_m):
    """좌표를 grid_m 격자의 칸 번호 (정수 쌍)로 바꾼다. 같은 칸이면 같은 끝점으로 본다."""
    return np.floor(np.asarray(x) / grid_m).astype(np.int64), np.floor(np.asarray(y) / grid_m).astype(np.int64)


def exact_groups(df, grid_m):
    """
    key = (snap(시점), snap(종점), link_seq)가 같은 통행을 한 그룹으로 묶는다.
    반환: 통행별 그룹 번호 배열 (0부터, 그룹의 첫 통행 순서대로)
    """
    sx, sy = snap(df.start_x, df.start_y, grid_m)
    ex, ey = snap(df.end_x, df.end_y, grid_m)
    keys = list(zip(sx.tolist(), sy.tolist(), ex.tolist(), ey.tolist(), df.link_seq))
    ids = {}
    return np.array([ids.setdefault(k, len(ids)) for k in keys], dtype=np.int64)


def summarize_groups(df, gid):
    """
    그룹별 요약: 통행 수, 링크 시퀀스, 시종점(구성 통행의 중앙값).
    2-1 그룹이면 링크 시퀀스가 하나뿐이다.
    """
    d = df.assign(gid=gid)
    g = d.groupby("gid", sort=True)
    out = g[["start_x", "start_y", "end_x", "end_y"]].median()
    out["n_trips"] = g.size()
    out["link_seq"] = g.link_seq.first()
    return out.reset_index()


# ===========================================================================
# 2-2. 유사도 기반 묶기
# ===========================================================================
def weighted_jaccard(a, b, length):
    """
    링크 길이로 가중한 자카드 = 공통 링크 길이 합 / 합집합 링크 길이 합.
    링크 개수로 세면 짧은 골목 하나와 긴 대로 하나가 같은 무게가 되어 유사도가 왜곡된다.
    한 경로 안에서 같은 링크를 여러 번 지나도 한 번으로 센다 (집합 비교).
    """
    a, b = set(a), set(b)
    inter = sum(length[l] for l in a & b)
    union = sum(length[l] for l in a | b)
    return inter / union if union > 0 else 0.0


class UnionFind:
    def __init__(self, n):
        self.parent = np.arange(n)

    def find(self, i):
        root = i
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[i] != root:          # 경로 압축
            self.parent[i], i = root, self.parent[i]
        return root

    def union(self, i, j):
        ri, rj = self.find(i), self.find(j)
        if ri != rj:
            self.parent[max(ri, rj)] = min(ri, rj)

    def labels(self):
        return np.array([self.find(i) for i in range(len(self.parent))])


def _tokens(seq, length, token_m):
    """링크를 token_m마다 토큰 하나로 나눈다. 토큰 수가 길이에 비례해 MinHash가 길이 가중 자카드를 근사한다."""
    out = []
    for l in set(seq):
        n = max(1, int(np.ceil(length[l] / token_m)))
        out.extend(f"{l}:{k}".encode() for k in range(n))
    return out


def similarity_groups(groups, length, threshold, endpoint_m, token_m=None, num_perm=None, seed=None, margin=None):
    """
    2-1 그룹들을 유사도로 한 번 더 묶는다.
    반환: (2-1 그룹별 새 그룹 번호, 진단 정보 dict)

    1) 그룹마다 MinHash를 만들어 LSH에 넣고, 비슷할 가능성이 있는 쌍만 후보로 뽑는다 (모든 쌍 비교는 불가능).
    2) 후보 쌍의 시점끼리·종점끼리 거리가 endpoint_m 이하이고 정확한 가중 자카드가 threshold 이상이면 union.
       방향이 없으므로 한쪽을 뒤집어 맞춘 경우(시점-종점, 종점-시점)도 인정한다.
       뒤집어 맞춘 그룹의 방향은 align_groups()가 대표 쪽에 맞춰 준다.
    주의: union-find는 전이적이라 A~B, B~C면 A와 C가 직접 조건을 못 넘어도 같은 그룹이 된다.
          그래서 그룹 안 끝점이 대표에서 얼마나 떨어졌는지(최대 거리)를 진단값으로 남긴다.
    """
    from datasketch import MinHash, MinHashLSH

    token_m = token_m or C.S2_MINHASH_TOKEN_M
    num_perm = num_perm or C.S2_MINHASH_PERM
    seed = C.S2_MINHASH_SEED if seed is None else seed
    margin = C.S2_LSH_MARGIN if margin is None else margin

    seqs = groups.link_seq.tolist()
    unknown = {l for s in seqs for l in s} - set(length)
    if unknown:
        raise ValueError(f"링크 길이 표에 없는 link_id {len(unknown)}개 (예: {sorted(unknown)[:5]})")

    lsh = MinHashLSH(threshold=max(0.05, threshold - margin), num_perm=num_perm, weights=C.S2_LSH_WEIGHTS)
    mhs = []
    for i, s in enumerate(seqs):
        m = MinHash(num_perm=num_perm, seed=seed)
        m.update_batch(_tokens(s, length, token_m))
        lsh.insert(i, m)
        mhs.append(m)

    sxy = groups[["start_x", "start_y"]].to_numpy()
    exy = groups[["end_x", "end_y"]].to_numpy()
    uf = UnionFind(len(groups))
    n_cand = n_far = n_low = n_merge = 0
    for i in range(len(groups)):
        for j in lsh.query(mhs[i]):
            if j <= i:
                continue
            n_cand += 1
            # 방향이 없으므로 시점-시점·종점-종점 또는 시점-종점·종점-시점 중 한쪽만 맞으면 된다
            same = max(np.hypot(*(sxy[i] - sxy[j])), np.hypot(*(exy[i] - exy[j])))
            cross = max(np.hypot(*(sxy[i] - exy[j])), np.hypot(*(exy[i] - sxy[j])))
            if min(same, cross) > endpoint_m:
                n_far += 1
                continue
            if weighted_jaccard(seqs[i], seqs[j], length) < threshold:
                n_low += 1
                continue
            uf.union(i, j)
            n_merge += 1

    diag = {"n_candidate_pairs": n_cand, "n_rejected_endpoint": n_far,
            "n_rejected_similarity": n_low, "n_unions": n_merge}
    return uf.labels(), diag


def align_groups(groups, glab):
    """
    2-2로 합쳐진 그룹 안에서 2-1 그룹들의 방향을 맞춘다.
    orient()의 표준 방향은 시퀀스마다 따로 정해지므로, 비슷한 두 시퀀스가 서로 반대 방향으로 저장됐을 수 있다.
    그대로 두면 대표 시종점(중앙값)이 양쪽 끝을 섞어 버린다.
    기준 = 합쳐진 그룹에서 통행 수가 가장 많은 2-1 그룹. 나머지는 끝점이 뒤집힌 쪽으로 더 가까우면 뒤집는다.
    반환: 2-1 그룹별 뒤집기 여부 (bool 배열)
    """
    s = groups[["start_x", "start_y"]].to_numpy()
    e = groups[["end_x", "end_y"]].to_numpy()
    anchor = pd.Series(groups.n_trips.to_numpy()).groupby(glab).idxmax().reindex(glab).to_numpy()
    same = np.hypot(*(s - s[anchor]).T) + np.hypot(*(e - e[anchor]).T)
    cross = np.hypot(*(s - e[anchor]).T) + np.hypot(*(e - s[anchor]).T)
    return cross < same


# ===========================================================================
# 2-3. 대표경로 선정
# ===========================================================================
def pick_representatives(df, gid):
    """
    그룹별 대표경로. 대표 = 그룹 안에서 통행 수가 가장 많은 링크 시퀀스 (동률이면 그룹 안에서 먼저 나온 것).
    가중치 = 그룹의 전체 통행 수. 시종점 = 그룹 전체 통행 시종점의 중앙값.
    메도이드는 계산량 대비 이득이 적어 쓰지 않는다.
    반환: (대표경로 DataFrame, 통행 -> route_id 대응 DataFrame)
    """
    d = df.assign(gid=gid)
    g = d.groupby("gid", sort=True)
    routes = g[["start_x", "start_y", "end_x", "end_y"]].median()
    routes["weight"] = g.size()
    # Counter.most_common은 동률이면 먼저 센 것을 앞에 둔다 -> 입력 순서 기준으로 결정적
    routes["link_seq"] = g.link_seq.agg(lambda s: Counter(s).most_common(1)[0][0])
    routes["n_distinct_seq"] = g.link_seq.nunique()
    routes = routes.reset_index()
    # route_id: 가중치 큰 순서 (보고와 확인이 편하도록)
    routes = routes.sort_values(["weight", "gid"], ascending=[False, True], kind="mergesort").reset_index(drop=True)
    routes["route_id"] = np.arange(len(routes), dtype=np.int64)
    # reversed: 통행이 대표경로와 반대 방향으로 지나갔는지
    trip_route = d[["trip_id", "gid", "flipped"]].merge(routes[["gid", "route_id"]], on="gid")         .rename(columns={"flipped": "reversed"})[["trip_id", "route_id", "reversed"]]
    routes = routes[["route_id", "link_seq", "weight", "n_distinct_seq", "start_x", "start_y", "end_x", "end_y"]]
    return routes, trip_route


def endpoint_spread(df, trip_route, routes):
    """통행 시종점이 소속 대표경로의 시종점에서 떨어진 거리 (m). 2-2 전이 묶음의 부작용 확인용."""
    d = df[["trip_id", "start_x", "start_y", "end_x", "end_y"]].merge(trip_route, on="trip_id") \
        .merge(routes[["route_id", "start_x", "start_y", "end_x", "end_y"]], on="route_id", suffixes=("", "_r"))
    ds = np.hypot(d.start_x - d.start_x_r, d.start_y - d.start_y_r)
    de = np.hypot(d.end_x - d.end_x_r, d.end_y - d.end_y_r)
    return np.maximum(ds, de)


# ===========================================================================
# 2-4. 압축률 측정
# ===========================================================================
def weight_stats(weights):
    w = np.sort(np.asarray(weights))[::-1]
    return {"n_routes": int(len(w)), "max": int(w[0]), "median": float(np.median(w)),
            "mean": round(float(w.mean()), 2), "top10": [int(v) for v in w[:10]],
            "top10_trip_share": round(float(w[:10].sum() / w.sum()), 4),
            "n_weight_1": int((w == 1).sum())}


def band_advice(n):
    """2-1 후 경로 수에 따른 다음 단계 안내 (설계 문서의 구간 규칙)."""
    lo, hi = C.S2_BAND_MID
    if n <= C.S2_BAND_OK:
        return "그대로 진행"
    if lo <= n <= hi:
        return "2-2 유사도 묶기 추가, 또는 격자를 300m로 키워 재시도"
    if n >= C.S2_BAND_FAIL:
        return "압축으로 해결되지 않음: 6단계 휴리스틱 직행 또는 샘플링"
    return "규칙이 정해지지 않은 구간: 사용자와 결정"


def compress(df, length=None, grid_m=None, sim_threshold=None, target=None, endpoint_m=None):
    """
    2-1 ~ 2-3 전체. 테스트에서 설정값을 바꿔 부를 수 있도록 인자로 받는다 (None이면 config 값).
    반환: (대표경로, 통행->경로 대응, 로그 dict)
    """
    grid_m = grid_m or C.S2_ENDPOINT_GRID_M
    target = target or C.S2_TARGET_ROUTES
    endpoint_m = endpoint_m or C.S2_SIM_ENDPOINT_M
    sim_threshold = C.S2_SIM_THRESHOLD if sim_threshold is None else sim_threshold
    log = {"n_trips": int(len(df))}

    # 방향 없애기: A→B와 B→A를 같은 키로
    df = orient(df)
    log["n_flipped"] = int(df.flipped.sum())

    # 2-1
    gid = exact_groups(df, grid_m)
    groups = summarize_groups(df, gid)
    log["n_routes_exact"] = int(len(groups))
    log["ratio_exact"] = round(len(groups) / len(df), 4)
    log["weights_exact"] = weight_stats(groups.n_trips)
    log["n_distinct_seq"] = int(df.link_seq.nunique())   # 끝점을 빼고 시퀀스만 봤을 때의 개수 (참고)
    log["advice"] = band_advice(len(groups))

    # 2-2 (조건부)
    if len(groups) <= target:
        log["similarity"] = f"건너뜀: 2-1 결과 {len(groups):,}개 <= 목표 {target:,}개"
    elif sim_threshold is None:
        log["similarity"] = "건너뜀: S2_SIM_THRESHOLD 미정 (압축률 보고 뒤 사용자와 결정)"
    else:
        if length is None:
            length = load_link_lengths()
        t = time.time()
        glab, diag = similarity_groups(groups, length, sim_threshold, endpoint_m)
        # 합쳐진 그룹 안에서 방향 맞추기 (대표 시종점 중앙값이 양 끝을 섞지 않도록)
        gflip = align_groups(groups, glab)
        tflip = gflip[gid]
        df = _swap_ends(df, tflip)
        df["link_seq"] = [s[::-1] if f else s for s, f in zip(df.link_seq, tflip)]
        df["flipped"] = df.flipped.to_numpy() ^ tflip
        diag["n_groups_realigned"] = int(gflip.sum())
        gid = glab[gid]                     # 통행 -> 2-1 그룹 -> 2-2 그룹
        diag["elapsed_s"] = round(time.time() - t, 1)
        diag["threshold"] = sim_threshold
        log["similarity"] = diag

    # 2-3
    routes, trip_route = pick_representatives(df, gid)
    log["n_routes_final"] = int(len(routes))
    log["ratio_final"] = round(len(routes) / len(df), 4)
    log["weights_final"] = weight_stats(routes.weight)
    spread = endpoint_spread(df, trip_route, routes)
    log["endpoint_spread_m"] = {k: round(float(np.percentile(spread, q)), 1)
                                for k, q in [("50%", 50), ("90%", 90), ("99%", 99), ("max", 100)]}
    assert routes.weight.sum() == len(df), "가중치 합이 통행 수와 다름"
    return routes, trip_route, log


def print_report(log):
    print("\n========== 2단계 대표경로 압축 결과 ==========")
    print(f"원본 통행                  {log['n_trips']:>10,}")
    print(f"2-1 정확 일치 후 경로      {log['n_routes_exact']:>10,}  (압축률 {log['ratio_exact']:.1%} = 경로/통행)")
    print(f"  참고: 끝점 없이 시퀀스만 {log['n_distinct_seq']:>10,}  (방향 없음, 표준 방향으로 뒤집은 통행 {log['n_flipped']:,})")
    w = log["weights_exact"]
    print(f"  경로당 가중치  최대 {w['max']:,}, 중앙값 {w['median']:g}, 평균 {w['mean']}, 가중치 1인 경로 {w['n_weight_1']:,}")
    print(f"  상위 10개      {w['top10']}  (전체 통행의 {w['top10_trip_share']:.1%})")
    print(f"  다음 단계 안내 {log['advice']}")
    print(f"2-2 유사도 묶기            {log['similarity']}")
    if isinstance(log["similarity"], dict):
        w = log["weights_final"]
        print(f"2-2 후 경로                {log['n_routes_final']:>10,}  (압축률 {log['ratio_final']:.1%})")
        print(f"  경로당 가중치  최대 {w['max']:,}, 중앙값 {w['median']:g}, 상위 10개 {w['top10']}")
    print(f"통행 끝점 - 대표 끝점 거리(m, 시종점 중 큰 값)  {log['endpoint_spread_m']}")
    print("==============================================\n")


# ===========================================================================
# 2-5. 압축 오차 검증 (5단계 평가 함수 완성 뒤)
# ===========================================================================
def validate_compression(objective, trips, routes, candidates, p, n_solutions=None, seed=None):
    """
    압축이 목적함수를 얼마나 왜곡하는지 잰다.
      1. 후보지 p개를 무작위로 골라 가상 해를 만든다.
      2. 원본 통행 전체(각 가중치 1)로 목적함수를 계산한다.
      3. 대표경로(가중치 = 그룹 통행 수)로 같은 계산을 한다.
      4. 상대오차 |압축 - 원본| / |원본|을 무작위 해 n_solutions개에 대해 구한다.

    objective(routes_df, sites) -> float
        routes_df: link_seq, weight, start_x, start_y, end_x, end_y 칼럼 (원본 통행은 weight=1로 넘김)
        sites    : candidates에서 고른 행들
    판정: 최대 상대오차가 S2_VALID_OK 이내면 진행, S2_VALID_FAIL을 넘으면 재조정 (그 사이는 사용자와 결정).
    반환: (해별 결과 DataFrame, 요약 dict)
    """
    n_solutions = n_solutions or C.S2_VALID_N_SOLUTIONS
    rng = np.random.default_rng(C.S2_VALID_SEED if seed is None else seed)
    original = trips.assign(weight=1)
    rows = []
    for k in range(n_solutions):
        pick = rng.choice(len(candidates), size=p, replace=False)
        sites = candidates.iloc[np.sort(pick)]
        f_o = objective(original, sites)
        f_c = objective(routes, sites)
        rel = abs(f_c - f_o) / abs(f_o) if f_o != 0 else (0.0 if f_c == 0 else np.inf)
        rows.append({"solution": k, "f_original": f_o, "f_compressed": f_c, "rel_err": rel})
    res = pd.DataFrame(rows)
    worst = float(res.rel_err.max())
    verdict = ("진행" if worst <= C.S2_VALID_OK else
               "재조정: 유사도 임계값을 올리거나 격자를 100m로 줄여 재측정" if worst > C.S2_VALID_FAIL else
               "사용자와 결정")
    summary = {"n_solutions": n_solutions, "p": p, "rel_err_mean": float(res.rel_err.mean()),
               "rel_err_median": float(res.rel_err.median()), "rel_err_max": worst, "verdict": verdict}
    return res, summary


# ===========================================================================
def main(force=False):
    C.setup_environment()
    force = force or C.FORCE_RECOMPUTE
    if OUT_ROUTES.exists() and OUT_TRIP_ROUTE.exists() and OUT_STATS.exists() and not force:
        print(f"[s2] 기존 산출물이 있어 건너뜁니다 (--force 로 재계산). {OUT_STATS}")
        print_report(json.loads(OUT_STATS.read_text(encoding="utf-8")))
        return

    t = time.time()
    df = load_routes()
    routes, trip_route, log = compress(df)
    log["elapsed_s"] = round(time.time() - t, 1)
    log["config"] = {k: getattr(C, k) for k in [
        "S2_ENDPOINT_GRID_M", "S2_TARGET_ROUTES", "S2_SIM_THRESHOLD", "S2_SIM_ENDPOINT_M",
        "S2_MINHASH_TOKEN_M", "S2_MINHASH_PERM", "S2_MINHASH_SEED", "S2_LSH_MARGIN", "S2_LSH_WEIGHTS"]}

    out = routes.copy()
    out["link_seq"] = out.link_seq.map(list)
    out.to_parquet(OUT_ROUTES, index=False)
    trip_route.to_parquet(OUT_TRIP_ROUTE, index=False)
    OUT_STATS.write_text(json.dumps(log, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print_report(log)
    print(f"[s2] 저장 완료 ({log['elapsed_s']}초): {OUT_ROUTES.name}, {OUT_TRIP_ROUTE.name}, {OUT_STATS.name}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="캐시 무시하고 재계산")
    main(force=ap.parse_args().force)
