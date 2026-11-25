"""
6단계: MILP 최적화 (압축형)

대여소 p개를 골라 5단계 목적함수 sum(weight_f * 혜택(f))를 최대화한다. 휴리스틱 없이 MILP만 쓴다 (2026-09-28 결정).

왜 압축형인가
  직관적인 형태는 '경로 f에서 i에서 빌려 j에 반납' 쌍 변수 y[f][i][j]를 쓰지만, 변수가 경로당 (후보지 수)^2이다
  (경로당 최대 141개 -> 약 2만). 대신 경로별로 '선택된 것 중 가장 뒤·가장 앞'만 다루면 변수가 경로당 후보지 수에 비례한다.

변수 (접근점 표 한 행 n = (경로 f, 후보지 k, 위치 a_n))
  x[k]   후보지 k 설치 여부                         이진
  z[f]   경로 f가 혜택을 받는가                      이진   (최소 탑승 거리 때문에 필요, 아래 참고)
  s[n]   행 n이 경로 f의 '뒤쪽 끝'으로 뽑혔는가       연속 [0, 1]
  t[n]   행 n이 경로 f의 '앞쪽 끝'으로 뽑혔는가       연속 [0, 1]
  u[f]   경로 f에서 커버되는 뒤쪽 끝 위치 (m)         연속
  v[f]   경로 f에서 커버되는 앞쪽 끝 위치 (m)         연속
  g[f]   혜택 (m)                                     연속 >= 0

최대화  sum_f c_f * g[f]        c_f = weight_f (absolute) 또는 weight_f / 경로 길이 (ratio)

제약
  (1) s[n] <= x[k(n)]                   설치된 후보지만 끝으로 뽑을 수 있다
  (2) t[n] <= x[k(n)]
  (3) sum_{n in f} s[n] = z[f]          혜택을 받는 경로는 뒤쪽 끝을 정확히 1개(분량) 뽑는다
  (4) sum_{n in f} t[n] = z[f]
  (5) u[f] <= sum_{n in f} a_n s[n]     u <= 선택된 후보지 중 최대 a
  (6) v[f] >= sum_{n in f} a_n t[n]     v >= 선택된 후보지 중 최소 a
  (7) g[f] <= u[f] - v[f]
  (8) u[f] - v[f] >= 최소탑승 * z[f]    혜택을 받는다면 커버 구간이 최소 탑승 거리 이상
  (9) sum_k x[k] <= p

왜 맞는가
  - (1)(3)(5): s는 설치된 행들 위의 가중치 합 z인 분포다. a의 가중평균은 최댓값을 넘을 수 없으므로 u <= z * (선택 중 최대 a).
    최대화 문제라 솔버는 s를 최대 a 행에 몰아 u = 최대 a로 만든다. (4)(6)도 같은 이유로 v = 최소 a.
    s, t가 연속이어도 u - v는 실제 간격을 넘지 못하므로 이진으로 둘 필요가 없다 (z와 x가 이진이면 충분).
  - 선택 후보지가 1개인 경로: s와 t가 같은 행에 몰려 u = v, g = 0 (별도 처리 불필요).
  - z[f] = 0이면 s = t = 0 -> u <= 0, v >= 0 -> g <= 0 -> g = 0.
  - 최소 탑승 거리 (5단계: 간격 < 500m이면 혜택 0)
    원래 제시한 모형에는 없는 조건이라 z[f]를 더했다. 간격이 500m 미만인데 g > 0이 되는 것을 (8)이 막는다.
    u - v는 실제 간격 이하이므로 (8)을 만족하면 실제 간격도 최소 탑승 거리 이상이다. 최소 탑승 거리가 0이면 z는 무의미해진다.
  - 규모: 이진 x (후보지 수) + z (경로 수), 연속 s, t (접근점 행 수 x 2) + u, v, g (경로 수 x 3). 행 수에 선형이다.

전처리 (결과를 바꾸지 않는 축소)
  - 후보지를 전부 골라도 간격이 최소 탑승 거리 미만인 경로는 어떤 해로도 혜택 0 -> 모형에서 뺀다.
  - 남은 경로 어디에도 안 걸리는 후보지는 골라도 값이 변하지 않는다 -> 모형에서 뺀다 (x = 0으로 둔 것과 같다).

검증
  MILP가 보고한 목적값 = 5단계 Evaluator로 '선택된 x'를 다시 잰 값이어야 선형화가 맞다 (허용오차 S6_CHECK_REL_TOL).
  시간 제한으로 최적이 아니게 끝나도 해 자체는 유효하므로 평가 함수 값을 공식 값으로 쓴다.

입력  : data/interim/s4_access.parquet, s2_routes.parquet, s3_candidates.parquet (5단계 load_evaluator와 같음)
        data/interim/s5_random_eval.parquet (있으면 같은 p의 무작위 조합과 비교)
출력  : data/interim/s6_selected.parquet  mode, p, cand_id, x, y, is_existing, used (어떤 혜택 경로의 끝으로 쓰였나)
        data/interim/s6_routes.parquet    mode, p, route_id, weight, route_len, u, v, g (MILP), benefit_m (평가 함수, m),
                                          benefit (평가 함수, 변형 적용), start_cand, end_cand
        data/interim/s6_stats.json        실행마다: 상태, 목적값(MILP / 평가 함수), 상계, 갭, 시간, 모형 규모, 무작위 대비

실행
  python s6_milp.py                        # 캐시가 있으면 건너뜀
  python s6_milp.py --force
  python s6_milp.py --p 100 --mode ratio   # 한 조합만 (저장하지 않고 출력만)
"""
import argparse
import json
import time

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

import config as C
from s5_objective import Evaluator, load_evaluator

IN_CANDS = C.DATA_INTERIM / "s3_candidates.parquet"
IN_RANDOM = C.DATA_INTERIM / "s5_random_eval.parquet"
OUT_SELECTED = C.DATA_INTERIM / "s6_selected.parquet"
OUT_ROUTES = C.DATA_INTERIM / "s6_routes.parquet"
OUT_STATS = C.DATA_INTERIM / "s6_stats.json"

# scipy milp 상태 코드
STATUS = {0: "optimal", 1: "limit", 2: "infeasible", 3: "unbounded", 4: "other"}


class MilpModel:
    """
    Evaluator 하나(접근점 표 + 경로 가중치·길이 + 후보지 목록)로 MILP 행렬을 만든다.
    p와 변형이 바뀌어도 행렬 구조는 같으므로 한 번 만들어 두고 solve(p, mode)로 여러 번 푼다
    (바뀌는 것은 목적계수 c와 제약 (9)의 우변뿐).
    """

    def __init__(self, ev: Evaluator):
        self.ev = ev
        mr = ev.min_ride_m
        acc = ev._acc                                               # route_id, a 순 정렬된 접근점 표
        # ---- 전처리: 전부 골라도 혜택 0인 경로 제외
        full_span, _ = ev.spans(np.ones(len(ev.cand_ids), dtype=bool))
        route_pos = pd.Index(ev.route_ids).get_indexer(acc.route_id.to_numpy())
        keep_route = full_span >= mr                                 # route_ids 순서
        keep_route &= full_span > 0                                  # 최소 탑승 0이어도 간격 0 경로는 뺀다
        row_keep = keep_route[route_pos]
        self.n_routes_dropped = int((~keep_route).sum())
        # ---- 남은 경로에 안 걸리는 후보지 제외
        used_cand = np.unique(ev.row_cand[row_keep])                 # ev.cand_ids 안 위치
        self.n_cands_dropped = int(len(ev.cand_ids) - len(used_cand))

        self.route_idx = np.flatnonzero(keep_route)                  # 모형 경로 j -> ev.route_ids 위치
        self.cand_idx = used_cand                                    # 모형 후보지 k -> ev.cand_ids 위치
        R, K = len(self.route_idx), len(self.cand_idx)
        # 행 n -> 모형 경로 j, 모형 후보지 k
        rows = np.flatnonzero(row_keep)
        self.row_src = rows                                          # 정렬된 접근점 표 안 행 번호
        rj = np.searchsorted(self.route_idx, route_pos[rows])
        rk = np.searchsorted(self.cand_idx, ev.row_cand[rows])
        a = ev.a[rows]
        N = len(rows)
        self.R, self.K, self.N = R, K, N
        self.rj, self.rk, self.ra = rj, rk, a

        # ---- 변수 배치: [x (K) | z (R) | s (N) | t (N) | u (R) | v (R) | g (R)]
        o = {}
        off = 0
        for name, size in [("x", K), ("z", R), ("s", N), ("t", N), ("u", R), ("v", R), ("g", R)]:
            o[name] = off
            off += size
        self.off, self.n_var = o, off

        amax = np.zeros(R)
        np.maximum.at(amax, rj, a)                                   # u, v 상한 = 경로에서 가장 큰 a
        lb = np.zeros(off)
        ub = np.ones(off)
        ub[o["u"]:o["u"] + R] = amax
        ub[o["v"]:o["v"] + R] = amax
        ub[o["g"]:o["g"] + R] = amax
        self.bounds = Bounds(lb, ub)
        self.integrality = np.zeros(off)
        self.integrality[:K + R] = 1                                 # x, z 이진

        # ---- 제약 행렬 (COO로 쌓는다). 각 블록: (행 번호, 열 번호, 값), 행 범위 lb/ub
        I, J, V, clb, cub = [], [], [], [], []
        r0 = 0

        def add(ri, ci, vals, lo, hi, n_rows):
            nonlocal r0
            I.append(ri + r0)
            J.append(ci)
            V.append(vals)
            clb.append(lo)
            cub.append(hi)
            r0 += n_rows

        n_idx = np.arange(N)
        j_idx = np.arange(R)
        one_N, one_R = np.ones(N), np.ones(R)
        # (1) s_n - x_k <= 0,  (2) t_n - x_k <= 0
        for var in ("s", "t"):
            add(np.r_[n_idx, n_idx], np.r_[o[var] + n_idx, o["x"] + rk], np.r_[one_N, -one_N],
                np.full(N, -np.inf), np.zeros(N), N)
        # (3) sum s - z = 0,  (4) sum t - z = 0
        for var in ("s", "t"):
            add(np.r_[rj, j_idx], np.r_[o[var] + n_idx, o["z"] + j_idx], np.r_[one_N, -one_R],
                np.zeros(R), np.zeros(R), R)
        # (5) u - sum a s <= 0
        add(np.r_[j_idx, rj], np.r_[o["u"] + j_idx, o["s"] + n_idx], np.r_[one_R, -a],
            np.full(R, -np.inf), np.zeros(R), R)
        # (6) v - sum a t >= 0
        add(np.r_[j_idx, rj], np.r_[o["v"] + j_idx, o["t"] + n_idx], np.r_[one_R, -a],
            np.zeros(R), np.full(R, np.inf), R)
        # (7) g - u + v <= 0
        add(np.r_[j_idx, j_idx, j_idx], np.r_[o["g"] + j_idx, o["u"] + j_idx, o["v"] + j_idx],
            np.r_[one_R, -one_R, one_R], np.full(R, -np.inf), np.zeros(R), R)
        # (8) u - v - 최소탑승 * z >= 0
        add(np.r_[j_idx, j_idx, j_idx], np.r_[o["u"] + j_idx, o["v"] + j_idx, o["z"] + j_idx],
            np.r_[one_R, -one_R, np.full(R, -float(mr))], np.zeros(R), np.full(R, np.inf), R)
        # (9) sum x <= p   (우변은 solve에서 채운다: 마지막 행)
        add(np.zeros(K, dtype=np.int64), o["x"] + np.arange(K), np.ones(K), np.array([0.0]), np.array([np.nan]), 1)
        self.p_row = r0 - 1

        self.A = coo_matrix((np.concatenate(V), (np.concatenate(I), np.concatenate(J))), shape=(r0, off)).tocsr()
        self.con_lb = np.concatenate(clb)
        self.con_ub = np.concatenate(cub)
        self.n_con = r0

        # 목적계수용 경로 가중치·길이 (모형 경로 순서)
        self.w = ev.route_weight[self.route_idx]
        self.L = ev.route_len[self.route_idx]

    def size(self):
        return {"n_var": self.n_var, "n_binary": int(self.integrality.sum()), "n_con": self.n_con,
                "nnz": int(self.A.nnz), "n_routes": self.R, "n_cands": self.K, "n_rows": self.N,
                "n_routes_dropped": self.n_routes_dropped, "n_cands_dropped": self.n_cands_dropped}

    def objective(self, mode):
        c = np.zeros(self.n_var)
        if mode == "absolute":
            cg = self.w
        elif mode == "ratio":
            cg = np.divide(self.w, self.L, out=np.zeros_like(self.w), where=self.L > 0)
        else:
            raise ValueError(mode)
        c[self.off["g"]:self.off["g"] + self.R] = cg
        return c

    def solve(self, p, mode, time_limit=None, mip_rel_gap=None, disp=False):
        """
        반환: dict (상태, 목적값, 상계, 갭, 시간, 선택 후보지 id, 경로별 u/v/g)
        scipy milp는 최소화만 하므로 -c를 넣고 부호를 되돌린다.
        """
        c = self.objective(mode)
        cub = self.con_ub.copy()
        cub[self.p_row] = p
        opts = {"disp": disp, "presolve": True,
                "time_limit": C.S6_TIME_LIMIT_S if time_limit is None else time_limit,
                "mip_rel_gap": C.S6_MIP_REL_GAP if mip_rel_gap is None else mip_rel_gap}
        t0 = time.perf_counter()
        res = milp(-c, integrality=self.integrality, bounds=self.bounds,
                   constraints=LinearConstraint(self.A, self.con_lb, cub), options=opts)
        elapsed = time.perf_counter() - t0
        out = {"mode": mode, "p": int(p), "status": STATUS.get(res.status, str(res.status)),
               "message": res.message, "time_s": round(elapsed, 3),
               "obj_milp": None, "dual_bound": None, "mip_gap": None, "node_count": None}
        if res.x is None:
            return out
        xv = res.x
        o = self.off
        out["obj_milp"] = float(-res.fun)
        # 최대화 기준 상계 (scipy는 최소화 문제의 하계를 준다)
        db = getattr(res, "mip_dual_bound", None)
        out["dual_bound"] = None if db is None else float(-db)
        gap = getattr(res, "mip_gap", None)
        out["mip_gap"] = None if gap is None else float(gap)
        nc = getattr(res, "mip_node_count", None)
        out["node_count"] = None if nc is None else int(nc)
        x = xv[o["x"]:o["x"] + self.K] > 0.5
        out["selected"] = self.ev.cand_ids[self.cand_idx[x]]
        out["u"] = xv[o["u"]:o["u"] + self.R]
        out["v"] = xv[o["v"]:o["v"] + self.R]
        out["g"] = xv[o["g"]:o["g"] + self.R]
        out["z"] = xv[o["z"]:o["z"] + self.R] > 0.5
        return out


# ===========================================================================
# 해 검증·정리
# ===========================================================================
def route_ends(ev, mask):
    """경로마다 선택 후보지 중 가장 앞(a 최소)·가장 뒤(a 최대) 후보지 id. 선택이 없으면 -1."""
    acc = ev._acc
    m = mask[ev.row_cand]
    sel = acc.loc[m, ["route_id", "cand_id", "a"]]
    first = sel.loc[sel.groupby("route_id").a.idxmin()].set_index("route_id").cand_id
    last = sel.loc[sel.groupby("route_id").a.idxmax()].set_index("route_id").cand_id
    rid = pd.Index(ev.route_ids)
    return (first.reindex(rid, fill_value=-1).to_numpy(), last.reindex(rid, fill_value=-1).to_numpy())


def check_solution(model, sol):
    """
    MILP 해를 5단계 평가 함수로 다시 잰다.
    - obj_eval: 선택된 x만으로 잰 목적값 (공식 값)
    - 두 값의 상대 차이가 허용오차 안이면 선형화가 맞다.
    - used: 선택 후보지 중 혜택 경로의 앞·뒤 끝으로 실제로 쓰인 것 (p가 필요보다 크면 안 쓰이는 선택이 생길 수 있다)
    """
    ev = model.ev
    mask = ev.to_mask(sol["selected"])
    obj_eval, b = ev.evaluate_mask(mask, sol["mode"])
    span, cnt = ev.spans(mask)
    b_m = ev.benefit_from_span(span, "absolute")
    first, last = route_ends(ev, mask)
    benefit = b_m > 0
    used = np.unique(np.r_[first[benefit], last[benefit]])
    rel = abs(sol["obj_milp"] - obj_eval) / max(abs(obj_eval), 1.0)
    # 경로별 대조: MILP의 g와 평가 함수 혜택(m)
    g_full = np.zeros(len(ev.route_ids))
    g_full[model.route_idx] = sol["g"]
    return {"obj_eval": obj_eval, "rel_diff": rel, "ok": rel <= C.S6_CHECK_REL_TOL,
            "route_g_max_abs_diff": float(np.abs(g_full - b_m).max()) if len(b_m) else 0.0,
            "n_selected": int(mask.sum()), "n_used": int(len(used)), "used": used,
            "n_routes_benefit": int(benefit.sum()), "benefit_m": b_m, "benefit": b,
            "g_full": g_full, "first": first, "last": last}


def random_compare(p, mode, obj):
    """같은 p의 5단계 무작위 조합 분포와 비교 (최댓값, 중앙값 대비 배수)."""
    if not IN_RANDOM.exists():
        return None
    df = pd.read_parquet(IN_RANDOM)
    col = f"obj_{mode}"
    g = df.loc[df.p == p, col]
    if g.empty:
        return None
    med = float(g.median())
    return {"random_n": int(len(g)), "random_max": float(g.max()), "random_p50": med,
            "milp_over_random_max": obj / float(g.max()) if g.max() > 0 else None,
            "milp_over_random_p50": obj / med if med > 0 else None}


# ===========================================================================
# 실행
# ===========================================================================
def print_report(s):
    cfg = s["config"]
    m = s["model"]
    print("\n========== 6단계 MILP (압축형) ==========")
    print(f"설정       p {cfg['S6_P_LIST']}, 변형 {cfg['S6_MODES']}, 최소 탑승 {cfg['S5_MIN_RIDE_M']}m, "
          f"상대 갭 {cfg['S6_MIP_REL_GAP']}, 시간 제한 {cfg['S6_TIME_LIMIT_S']}초")
    print(f"모형 규모  변수 {m['n_var']:,} (이진 {m['n_binary']:,}), 제약 {m['n_con']:,}, 비영 {m['nnz']:,}, "
          f"만들기 {s['build_s']}초")
    print(f"           경로 {m['n_routes']} (전부 골라도 혜택 0이라 뺀 경로 {m['n_routes_dropped']}), "
          f"후보지 {m['n_cands']:,} (뺀 후보지 {m['n_cands_dropped']}), 접근점 행 {m['n_rows']:,}")
    ub = s["upper_bound"]
    print(f"상한 (후보지 전부)  absolute {ub['absolute']:,.1f} m, ratio {ub['ratio']:.3f}")
    for mode in cfg["S6_MODES"]:
        print(f"\n[{mode}]")
        print("      p  상태       MILP 목적값    평가 함수 값   상대차     상계         갭      상한 대비  "
              "혜택 경로  선택/쓰임   무작위 최대 대비  시간(초)")
        for r in s["runs"]:
            if r["mode"] != mode:
                continue
            if r["obj_milp"] is None:
                print(f"  {r['p']:>5}  {r['status']:<8}  해 없음 ({r['message']})")
                continue
            rc = r.get("random") or {}
            rm = rc.get("milp_over_random_max")
            print(f"  {r['p']:>5}  {r['status']:<8} {r['obj_milp']:>13,.3f} {r['obj_eval']:>14,.3f}  {r['rel_diff']:.1e}"
                  f"  {r['dual_bound'] if r['dual_bound'] is not None else float('nan'):>11,.3f}"
                  f"  {r['mip_gap'] if r['mip_gap'] is not None else float('nan'):>8.1e}"
                  f"  {r['obj_eval'] / ub[mode]:>8.3f}  {r['n_routes_benefit']:>8}  {r['n_selected']:>5}/{r['n_used']:<5}"
                  f"  {'-' if rm is None else f'{rm:.2f}배':>14}  {r['time_s']:>8.1f}")
    bad = [f"{r['mode']} p={r['p']}" for r in s["runs"] if r.get("ok") is False]
    print(f"\n목적값 대조 (MILP = 평가 함수, 상대 허용 {cfg['S6_CHECK_REL_TOL']}): "
          f"{'모두 일치' if not bad else '불일치 ' + ', '.join(bad)}")
    print("=========================================\n")


def run_all(p_list, modes, save=True):
    t0 = time.perf_counter()
    ev = load_evaluator()
    model = MilpModel(ev)
    build_s = round(time.perf_counter() - t0, 2)
    ub = {md: ev.evaluate_mask(np.ones(len(ev.cand_ids), dtype=bool), md)[0] for md in ("absolute", "ratio")}
    cands = pd.read_parquet(IN_CANDS)
    has_existing = "is_existing" in cands.columns
    if has_existing and cands.is_existing.any():
        print(f"[s6] 주의: 기존 대여소 {int(cands.is_existing.sum())}개가 있지만 이 모형은 모두 새로 고르는 후보지로 다룬다.")

    runs, sel_rows, route_rows = [], [], []
    for mode in modes:
        for p in p_list:
            sol = model.solve(p, mode)
            rec = {k: v for k, v in sol.items() if k not in ("selected", "u", "v", "g", "z")}
            if sol["obj_milp"] is not None:
                chk = check_solution(model, sol)
                rec.update({k: chk[k] for k in ("obj_eval", "rel_diff", "ok", "route_g_max_abs_diff",
                                                "n_selected", "n_used", "n_routes_benefit")})
                rec["ok"] = bool(rec["ok"])
                rec["random"] = random_compare(p, mode, chk["obj_eval"])
                used = set(chk["used"].tolist())
                for cid in sol["selected"]:
                    sel_rows.append({"mode": mode, "p": int(p), "cand_id": int(cid), "used": int(cid) in used})
                u_full = np.zeros(len(ev.route_ids))
                v_full = np.zeros(len(ev.route_ids))
                u_full[model.route_idx] = sol["u"]
                v_full[model.route_idx] = sol["v"]
                route_rows.append(pd.DataFrame({
                    "mode": mode, "p": int(p), "route_id": ev.route_ids, "weight": ev.route_weight,
                    "route_len": ev.route_len, "u": u_full, "v": v_full, "g": chk["g_full"],
                    "benefit_m": chk["benefit_m"], "benefit": chk["benefit"],
                    "start_cand": chk["first"], "end_cand": chk["last"]}))
            runs.append(rec)
            print(f"[s6] {mode:<8} p={p:<5} {rec['status']:<8} {rec['time_s']:>7.1f}초  "
                  f"목적값 {rec.get('obj_eval', float('nan')):,.3f}  갭 {rec['mip_gap']}")

    log = {"config": {"S6_P_LIST": list(p_list), "S6_MODES": list(modes), "S5_MIN_RIDE_M": ev.min_ride_m,
                      "S6_MIP_REL_GAP": C.S6_MIP_REL_GAP, "S6_TIME_LIMIT_S": C.S6_TIME_LIMIT_S,
                      "S6_CHECK_REL_TOL": C.S6_CHECK_REL_TOL, "solver": "scipy.optimize.milp (HiGHS)"},
           "model": model.size(), "build_s": build_s, "upper_bound": ub, "runs": runs,
           "elapsed_s": round(time.perf_counter() - t0, 1)}
    if save:
        sel = pd.DataFrame(sel_rows, columns=["mode", "p", "cand_id", "used"])
        cols = ["cand_id", "x", "y"] + [c for c in ("is_existing", "station_id") if c in cands.columns]
        sel = sel.merge(cands[cols], on="cand_id", how="left")
        sel.to_parquet(OUT_SELECTED, index=False)
        (pd.concat(route_rows, ignore_index=True) if route_rows else pd.DataFrame()).to_parquet(OUT_ROUTES, index=False)
        OUT_STATS.write_text(json.dumps(log, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print_report(log)
    return log


def main(force=False, p=None, mode=None):
    C.setup_environment()
    force = force or C.FORCE_RECOMPUTE
    if p is not None or mode is not None:           # 한 조합만 시험: 저장하지 않는다
        run_all([p] if p is not None else C.S6_P_LIST, [mode] if mode else C.S6_MODES, save=False)
        return
    if OUT_SELECTED.exists() and OUT_ROUTES.exists() and OUT_STATS.exists() and not force:
        print(f"[s6] 기존 산출물이 있어 건너뜁니다 (--force 로 재계산). {OUT_STATS}")
        print_report(json.loads(OUT_STATS.read_text(encoding="utf-8")))
        return
    log = run_all(C.S6_P_LIST, C.S6_MODES, save=True)
    print(f"[s6] 저장 완료 ({log['elapsed_s']}초): {OUT_SELECTED.name}, {OUT_ROUTES.name}, {OUT_STATS.name}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="캐시 무시하고 재계산")
    ap.add_argument("--p", type=int, default=None, help="이 p 하나만 풀고 저장하지 않음")
    ap.add_argument("--mode", choices=["absolute", "ratio"], default=None, help="이 변형 하나만 풀고 저장하지 않음")
    a = ap.parse_args()
    main(force=a.force, p=a.p, mode=a.mode)
