"""
5단계: 목적함수와 평가 함수

최적화를 짜기 전에 평가 함수부터 만든다. '후보지 집합 -> 목적함수 값' 하나로 사전 실험(무작위 조합 분포),
2-5 압축 오차 검증, 6단계 MILP 해의 목적값 대조를 모두 한다.
6단계는 MILP만 쓴다 (2026-09-28 사용자 결정: 휴리스틱 없음). MILP는 목적함수를 선형식으로 바꿔 풀기 때문에,
MILP가 보고한 목적값과 이 함수로 해를 다시 잰 값이 같아야 선형화가 맞게 된 것이다.

정의
  경로 f에 걸린 '선택된 후보지'들의 a(경로 시작점 기준 누적거리, 4단계) 중
    혜택(f) = max(a) - min(a)    걸린 선택 후보지가 2개 이상일 때
    혜택(f) = 0                  그 외
  목적함수 = sum(weight_f * 혜택(f))
  - 가장 앞의 대여소에서 빌려 가장 뒤의 대여소에 반납한다고 보는 것이다. 사이에 있는 대여소는 값에 영향이 없다.
  - 최소 탑승 거리: 혜택(m)이 S5_MIN_RIDE_M(500m) 미만이면 0. 그보다 짧은 구간은 사람들이 그냥 걷는다.
  - 변형 (S5_MODE)
      absolute : 혜택을 m 그대로. 긴 통행에 무게가 실린다.
      ratio    : 혜택 / 경로 전체 길이 (0~1). 통행 건수를 공평하게 취급한다.
                 최소 탑승 거리 판정은 나누기 전 m 값으로 한다.

구현 (벡터화)
  접근점 표를 route_id, a 순으로 정렬해 두면 경로 하나의 행이 한 덩어리로 붙는다.
  덩어리 시작 행 번호(starts)를 알면 np.maximum.reduceat(값, starts)가 덩어리별 최댓값을 한 번에 낸다.
  선택되지 않은 행은 최댓값 계산에서는 -inf, 최솟값 계산에서는 +inf로 바꿔 넣어 결과에 끼지 않게 한다.
    선택 0개 경로: max = -inf, min = +inf  -> 개수 조건(2개 이상)으로 0 처리
    선택 1개 경로: max = min               -> 개수 조건으로 0 처리
  주의: reduceat은 빈 덩어리를 제대로 다루지 못한다 (starts[i] == starts[i+1]이면 빈 구간이 아니라
  그 위치 원소 하나를 돌려준다). 그래서 행이 있는 경로만 starts에 넣고, 행이 없는 경로(도달 불가)는 혜택 0으로 채운다.
  반복문 구현(evaluate_loop)은 벡터화 구현 검증용으로만 남긴다.

입력  : data/interim/s4_access.parquet      route_id, cand_id, a, weight, route_len
        data/interim/s2_routes.parquet      route_id, weight (혜택 배열의 경로 순서, 도달 불가 경로 포함)
        data/interim/s3_candidates.parquet  cand_id (무작위 조합을 뽑는 후보지 전체)
출력  : data/interim/s5_random_eval.parquet 무작위 조합 1행: p, sample, obj_absolute, obj_ratio,
                                            n_routes_benefit (혜택 > 0 경로 수),
                                            n_routes_below_min (선택 2개 이상인데 최소 탑승 거리 미만으로 0이 된 경로 수)
        data/interim/s5_stats.json

실행
  python s5_objective.py            # 캐시가 있으면 건너뜀
  python s5_objective.py --force
"""
import argparse
import json
import time

import numpy as np
import pandas as pd

import config as C

IN_ACCESS = C.DATA_INTERIM / "s4_access.parquet"
IN_ROUTES = C.DATA_INTERIM / "s2_routes.parquet"
IN_CANDS = C.DATA_INTERIM / "s3_candidates.parquet"
OUT_RANDOM = C.DATA_INTERIM / "s5_random_eval.parquet"
OUT_STATS = C.DATA_INTERIM / "s5_stats.json"

MODES = ("absolute", "ratio")


class Evaluator:
    """
    접근점 표를 한 번 정리해 두고, 후보지 집합을 받을 때마다 목적함수를 계산한다.

    access    : DataFrame [route_id, cand_id, a, weight, route_len] (정렬 여부 상관없음)
    route_ids : 혜택 배열의 경로 순서. None이면 접근점 표에 나온 경로. 도달 불가 경로를 넣으면 혜택 0으로 나온다.
    route_weight, route_len : route_ids와 같은 순서의 배열. None이면 접근점 표에서 가져온다 (도달 불가 경로는 0).
    cand_ids  : 고를 수 있는 후보지 전체. None이면 접근점 표에 나온 후보지. 어떤 경로에도 안 걸린 후보지도 고를 수 있게
                3단계 후보지 전체를 넘기는 것이 기본이다.
    """

    def __init__(self, access, mode=None, min_ride_m=None, route_ids=None, route_weight=None, route_len=None,
                 cand_ids=None):
        self.mode = C.S5_MODE if mode is None else mode
        if self.mode not in MODES:
            raise ValueError(f"mode는 {MODES} 중 하나: {self.mode}")
        self.min_ride_m = C.S5_MIN_RIDE_M if min_ride_m is None else min_ride_m

        acc = access.sort_values(["route_id", "a"], kind="stable", ignore_index=True)
        per_route = acc.groupby("route_id", sort=True).agg(weight=("weight", "first"), route_len=("route_len", "first"))
        self.route_ids = per_route.index.to_numpy() if route_ids is None else np.asarray(route_ids)
        R = len(self.route_ids)
        self.route_weight = (per_route.weight.reindex(self.route_ids, fill_value=0).to_numpy(float)
                             if route_weight is None else np.asarray(route_weight, dtype=float))
        self.route_len = (per_route.route_len.reindex(self.route_ids, fill_value=0).to_numpy(float)
                          if route_len is None else np.asarray(route_len, dtype=float))
        assert len(self.route_weight) == R and len(self.route_len) == R

        # 후보지 id -> 0..K-1 조밀한 번호. 선택 집합을 길이 K의 bool 배열로 바꿔 행마다 인덱싱하기 위함
        self.cand_ids = np.unique(acc.cand_id.to_numpy() if cand_ids is None else np.asarray(cand_ids))
        row_cid = acc.cand_id.to_numpy()
        self.row_cand = np.searchsorted(self.cand_ids, row_cid)
        if not np.array_equal(self.cand_ids[np.minimum(self.row_cand, len(self.cand_ids) - 1)], row_cid):
            raise ValueError("접근점 표에 cand_ids 목록에 없는 후보지가 있다")
        self.a = acc.a.to_numpy(float)

        # 경로별 행 덩어리 시작 위치 (행이 있는 경로만). hit = 그 경로들의 route_ids 안 위치
        rid = acc.route_id.to_numpy()
        self.starts = np.flatnonzero(np.r_[True, rid[1:] != rid[:-1]]) if len(rid) else np.array([], dtype=np.int64)
        pos = pd.Index(self.route_ids).get_indexer(rid[self.starts])
        if (pos < 0).any():
            raise ValueError("접근점 표에 route_ids 목록에 없는 경로가 있다")
        self.hit = pos
        self.n_rows = len(acc)
        # 반복문 구현용 (검증 전용): 경로 순서대로 (cand_id 배열, a 배열)
        self._acc = acc

    # ---------------------------------------------------------------- 입력 변환
    def to_mask(self, selected):
        """선택된 후보지 id 집합 -> 길이 K bool 배열. 목록에 없는 id가 있으면 오류."""
        sel = np.unique(np.fromiter(selected, dtype=self.cand_ids.dtype) if not isinstance(selected, np.ndarray)
                        else selected)
        idx = np.searchsorted(self.cand_ids, sel)
        if len(sel) and (idx.max() >= len(self.cand_ids) or not np.array_equal(self.cand_ids[idx], sel)):
            raise ValueError("후보지 목록에 없는 id가 선택됐다")
        mask = np.zeros(len(self.cand_ids), dtype=bool)
        mask[idx] = True
        return mask

    # ---------------------------------------------------------------- 벡터화 구현
    def spans(self, mask):
        """
        경로별 혜택(m, 최소 탑승 거리 적용 전)과 선택 후보지 개수. route_ids 순서, 길이 R.
        mask: 길이 K bool (to_mask 결과)
        """
        span = np.zeros(len(self.route_ids))
        count = np.zeros(len(self.route_ids), dtype=np.int64)
        if self.n_rows == 0:
            return span, count
        m = mask[self.row_cand]                                    # 행마다 그 후보지가 선택됐는가
        hi = np.maximum.reduceat(np.where(m, self.a, -np.inf), self.starts)
        lo = np.minimum.reduceat(np.where(m, self.a, np.inf), self.starts)
        cnt = np.add.reduceat(m.astype(np.int64), self.starts)
        span[self.hit] = np.where(cnt >= 2, hi - lo, 0.0)
        count[self.hit] = cnt
        return span, count

    def benefit_from_span(self, span, mode=None):
        """최소 탑승 거리 적용 + 변형. 경로 길이가 0인 경로(도달 불가로 채운 값)는 ratio 혜택 0."""
        mode = self.mode if mode is None else mode
        b = np.where(span >= self.min_ride_m, span, 0.0)
        if mode == "ratio":
            b = np.divide(b, self.route_len, out=np.zeros_like(b), where=self.route_len > 0)
        return b

    def evaluate(self, selected, mode=None):
        """
        selected : 선택된 후보지 id 집합 (set, list, ndarray)
        반환     : (목적함수 값, 경로별 혜택 배열 [route_ids 순서])
        """
        return self.evaluate_mask(self.to_mask(selected), mode)

    def evaluate_mask(self, mask, mode=None):
        """evaluate와 같지만 이미 만든 bool 배열을 받는다 (같은 해를 여러 변형으로 잴 때 변환을 한 번만)."""
        span, _ = self.spans(mask)
        b = self.benefit_from_span(span, mode)
        return float(self.route_weight @ b), b

    # ---------------------------------------------------------------- 반복문 구현 (검증용)
    def evaluate_loop(self, selected, mode=None):
        """정의를 그대로 옮긴 반복문 구현. 벡터화 구현과 결과가 같은지 확인하는 용도로만 쓴다."""
        mode = self.mode if mode is None else mode
        sel = set(int(c) for c in selected)
        unknown = sel - set(int(c) for c in self.cand_ids)
        if unknown:
            raise ValueError("후보지 목록에 없는 id가 선택됐다")
        pos = {r: i for i, r in enumerate(self.route_ids.tolist())}
        b = np.zeros(len(self.route_ids))
        for rid, g in self._acc.groupby("route_id", sort=False):
            hits = [a for c, a in zip(g.cand_id, g.a) if int(c) in sel]
            if len(hits) < 2:
                continue
            span = max(hits) - min(hits)
            if span < self.min_ride_m:
                continue
            i = pos[rid]
            if mode == "ratio":
                span = span / self.route_len[i] if self.route_len[i] > 0 else 0.0
            b[i] = span
        return float(sum(w * x for w, x in zip(self.route_weight, b))), b


# ===========================================================================
# 사전 실험
# ===========================================================================
def load_evaluator(mode=None):
    access = pd.read_parquet(IN_ACCESS, columns=["route_id", "cand_id", "a", "weight", "route_len"])
    routes = pd.read_parquet(IN_ROUTES, columns=["route_id", "weight"])
    cands = pd.read_parquet(IN_CANDS, columns=["cand_id"])
    rl = access.groupby("route_id").route_len.first().reindex(routes.route_id, fill_value=0).to_numpy()
    return Evaluator(access, mode=mode, route_ids=routes.route_id.to_numpy(), route_weight=routes.weight.to_numpy(),
                     route_len=rl, cand_ids=cands.cand_id.to_numpy())


def random_experiment(ev, p_list, n, seed):
    """후보지 p개 무작위 조합 n개씩. 한 해마다 두 변형을 함께 잰다 (span 계산은 한 번)."""
    rng = np.random.default_rng(seed)
    K = len(ev.cand_ids)
    rows = []
    for p in p_list:
        for k in range(n):
            mask = np.zeros(K, dtype=bool)
            mask[rng.choice(K, size=p, replace=False)] = True
            span, cnt = ev.spans(mask)
            b_abs = ev.benefit_from_span(span, "absolute")
            b_rat = ev.benefit_from_span(span, "ratio")
            rows.append({"p": p, "sample": k,
                         "obj_absolute": float(ev.route_weight @ b_abs), "obj_ratio": float(ev.route_weight @ b_rat),
                         "n_routes_benefit": int((b_abs > 0).sum()),
                         "n_routes_below_min": int(((cnt >= 2) & (span < ev.min_ride_m)).sum())})
    return pd.DataFrame(rows)


def check_loop(ev, p_list, n_per_p, seed):
    """벡터화 구현 = 반복문 구현인지 무작위 해로 확인. 두 변형 모두."""
    rng = np.random.default_rng(seed + 1)
    worst = {m: 0.0 for m in MODES}
    n = 0
    for p in p_list:
        for _ in range(n_per_p):
            sel = ev.cand_ids[rng.choice(len(ev.cand_ids), size=p, replace=False)]
            for m in MODES:
                f_v, b_v = ev.evaluate(sel, m)
                f_l, b_l = ev.evaluate_loop(sel, m)
                worst[m] = max(worst[m], abs(f_v - f_l), float(np.abs(b_v - b_l).max()))
            n += 1
    return {"n_solutions": n, "max_abs_diff": worst}


def timing(ev, p_list, repeat, seed):
    """
    평가 1회 소요 시간 (ms). evaluate(id 집합 -> 값)과 evaluate_mask(bool 배열 -> 값)를 나눠 잰다.
    반복문 구현은 느려서 반복 횟수를 줄여 잰다.
    """
    rng = np.random.default_rng(seed + 2)
    out = []
    for p in p_list:
        sel = ev.cand_ids[rng.choice(len(ev.cand_ids), size=p, replace=False)]
        mask = ev.to_mask(sel)
        rec = {"p": p}
        for name, fn, rep in [("evaluate_ms", lambda: ev.evaluate(sel), repeat),
                              ("evaluate_mask_ms", lambda: ev.evaluate_mask(mask), repeat),
                              ("loop_ms", lambda: ev.evaluate_loop(sel), max(repeat // 200, 3))]:
            fn()                                                   # 첫 호출 워밍업
            t = time.perf_counter()
            for _ in range(rep):
                fn()
            rec[name] = round((time.perf_counter() - t) / rep * 1000, 4)
        out.append(rec)
    return out


def _dist(s):
    s = np.asarray(s, dtype=float)
    q = np.percentile(s, [0, 5, 25, 50, 75, 95, 100])
    return {"mean": float(s.mean()), "std": float(s.std()), "min": float(q[0]), "p5": float(q[1]),
            "p25": float(q[2]), "p50": float(q[3]), "p75": float(q[4]), "p95": float(q[5]), "max": float(q[6]),
            "share_zero": float((s == 0).mean())}


def upper_bound(ev):
    """
    후보지를 전부 고른 해. 경로마다 걸린 후보지 중 가장 앞·뒤 사이가 혜택이 되므로, 어떤 p로도 넘을 수 없는 상한이다.
    최소 탑승 거리 때문에 전부 골라도 혜택 0인 경로 = 어떤 해로도 혜택을 줄 수 없는 경로.
    """
    mask = np.ones(len(ev.cand_ids), dtype=bool)
    span, _ = ev.spans(mask)
    f_abs, b_abs = ev.evaluate_mask(mask, "absolute")
    f_rat, _ = ev.evaluate_mask(mask, "ratio")
    never = b_abs == 0
    return {"obj_absolute": f_abs, "obj_ratio": f_rat, "n_routes_never_benefit": int(never.sum()),
            "weight_never_benefit": float(ev.route_weight[never].sum()),
            "never_route_ids": [int(r) for r in ev.route_ids[never]],
            "never_span_m": [round(float(s), 1) for s in span[never]],
            "total_weight": float(ev.route_weight.sum())}


def summarize(df, ub):
    out = []
    for p, g in df.groupby("p"):
        out.append({"p": int(p), "n": int(len(g)),
                    "obj_absolute": _dist(g.obj_absolute), "obj_ratio": _dist(g.obj_ratio),
                    "obj_absolute_over_ub": _dist(g.obj_absolute / ub["obj_absolute"]),
                    "obj_ratio_over_ub": _dist(g.obj_ratio / ub["obj_ratio"]),
                    "n_routes_benefit": _dist(g.n_routes_benefit),
                    "n_routes_below_min": _dist(g.n_routes_below_min)})
    return out


def print_report(s):
    cfg = s["config"]
    print("\n========== 5단계 목적함수·평가 함수 ==========")
    print(f"설정            최소 탑승 거리 {cfg['S5_MIN_RIDE_M']}m, 기본 변형 {cfg['S5_MODE']}")
    print(f"입력            접근점 표 {s['n_rows']:,}행, 경로 {s['n_routes']}개 (가중치 합 {s['upper_bound']['total_weight']:.0f}), "
          f"후보지 {s['n_candidates']:,}개")
    ub = s["upper_bound"]
    print(f"상한 (후보지 전부 선택)   absolute {ub['obj_absolute']:,.1f} m, ratio {ub['obj_ratio']:.3f}")
    print(f"  어떤 해로도 혜택 0인 경로 {ub['n_routes_never_benefit']}개 (가중치 {ub['weight_never_benefit']:.0f}), "
          f"걸린 후보지 최대 간격 m {ub['never_span_m']}")
    lc = s["loop_check"]
    print(f"반복문 구현 대조  해 {lc['n_solutions']}개, 최대 차이 {lc['max_abs_diff']}")
    print("평가 1회 소요 시간 (ms, 평균)")
    print("       p   evaluate   evaluate_mask   반복문")
    for t in s["timing"]:
        print(f"  {t['p']:>6}   {t['evaluate_ms']:>8.4f}   {t['evaluate_mask_ms']:>13.4f}   {t['loop_ms']:>8.2f}")
    print(f"무작위 조합 (p별 {cfg['S5_RANDOM_N']}개, 시드 {cfg['S5_SEED']})")
    for key, unit in [("obj_absolute", "m"), ("obj_ratio", ""), ("obj_absolute_over_ub", "상한 대비"),
                      ("obj_ratio_over_ub", "상한 대비"), ("n_routes_benefit", "경로"), ("n_routes_below_min", "경로")]:
        print(f"  {key} {unit}")
        print("       p        mean         p5        p50        p95        max    0인 비율")
        for r in s["random"]:
            d = r[key]
            print(f"  {r['p']:>6}  {d['mean']:>10.3f} {d['p5']:>10.3f} {d['p50']:>10.3f} {d['p95']:>10.3f} "
                  f"{d['max']:>10.3f}    {d['share_zero']:.1%}")
    print("=============================================\n")


def main(force=False):
    C.setup_environment()
    force = force or C.FORCE_RECOMPUTE
    if OUT_RANDOM.exists() and OUT_STATS.exists() and not force:
        print(f"[s5] 기존 산출물이 있어 건너뜁니다 (--force 로 재계산). {OUT_STATS}")
        print_report(json.loads(OUT_STATS.read_text(encoding="utf-8")))
        return
    t0 = time.time()
    ev = load_evaluator()
    ub = upper_bound(ev)
    loop = check_loop(ev, C.S5_RANDOM_P, n_per_p=20, seed=C.S5_SEED)
    tm = timing(ev, C.S5_RANDOM_P, C.S5_TIMING_REPEAT, C.S5_SEED)
    t1 = time.time()
    df = random_experiment(ev, C.S5_RANDOM_P, C.S5_RANDOM_N, C.S5_SEED)
    t_random = time.time() - t1

    log = {"config": {"S5_MODE": C.S5_MODE, "S5_MIN_RIDE_M": C.S5_MIN_RIDE_M, "S5_RANDOM_P": C.S5_RANDOM_P,
                      "S5_RANDOM_N": C.S5_RANDOM_N, "S5_SEED": C.S5_SEED, "S5_TIMING_REPEAT": C.S5_TIMING_REPEAT},
           "n_rows": ev.n_rows, "n_routes": int(len(ev.route_ids)), "n_candidates": int(len(ev.cand_ids)),
           "upper_bound": ub, "loop_check": loop, "timing": tm,
           "random": summarize(df, ub), "random_elapsed_s": round(t_random, 2),
           "elapsed_s": round(time.time() - t0, 1)}
    df.to_parquet(OUT_RANDOM, index=False)
    OUT_STATS.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    print_report(log)
    print(f"[s5] 저장 완료 ({log['elapsed_s']}초, 무작위 {len(df):,}개 평가 {t_random:.1f}초): "
          f"{OUT_RANDOM.name}, {OUT_STATS.name}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="캐시 무시하고 재계산")
    main(force=ap.parse_args().force)
