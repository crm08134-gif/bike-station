"""
1단계 준비: 맵매칭 라이브러리 비교 실행과 평가

경로 정답이 없으므로 아래 지표로 비교한다 (모두 EPSG:5179에서 계산)
  실패율          : 경로를 하나도 못 만든 비율
  끊김            : 경로가 2조각 이상으로 나온 비율 (n_parts > 1)
  길이비          : 매칭 경로 길이 / 0단계 보정 거리(dist_m). 1 근처가 정상.
                    크게 넘으면 헤매거나 되밟는 경로, 작으면 일부만 매칭
  벗어난 점 비율  : 1초 원본 GPS 점 중 매칭 경로에서 S1B_OFFPATH_M보다 먼 점의 비율
  공백 연결       : 세션 안 공백(>S1B_GAP_MIN_S초) 양쪽 점이 같은 경로 조각에 붙었는지,
                    붙었다면 경로상 거리 / 직선거리 (우회율)
  간격 견고성     : 5초·15초로 솎은 입력의 결과가 같은 라이브러리의 1초 결과와 얼마나 겹치는지
                    (서로 S1B_AGREE_BUFFER_M 안에 드는 길이 비율, 두 방향 중 작은 값)
  라이브러리 간   : 1초 입력에서 두 라이브러리 결과의 겹침 (위와 같은 방식)
  처리 시간       : 통행 하나당 초. FMM은 컨테이너 안 측정값 (UBODT 생성 시간 별도)

입력  : s1_bench_prep 산출물, Valhalla 타일, OSRM 데이터, bike-fmm 이미지
출력  : data/interim/s1_bench/results.parquet   (통행 x 간격 x 라이브러리) 매칭 결과와 지표
        data/output/s1_bench_summary.csv 대신 화면 출력 (중간 산출물은 parquet만)
        data/output/s1_bench_examples.png      예시 통행의 라이브러리별 경로

실행
  python s1_bench_run.py            # 저장된 결과가 있으면 매칭은 건너뛰고 평가만
  python s1_bench_run.py --force    # 매칭부터 다시
"""
import argparse
import itertools
import json
import logging
import shutil
import subprocess
import warnings

import numpy as np
import pandas as pd
import shapely

import config as C
import s1_bench_match as M

D = C.S1B_DIR
OUT_RESULTS = D / "results.parquet"
OUT_PY = D / "results_py.parquet"       # Valhalla, OSRM, leuven, mappymatch
OUT_FMM = D / "results_fmm.parquet"
OUT_FIG = C.DATA_OUTPUT / "s1_bench_examples.png"
LIBS = ["valhalla", "osrm", "fmm", "leuven", "mappymatch"]


# ===========================================================================
# 1. 매칭 실행
# ===========================================================================
def run_python_libs(sp):
    """Valhalla, OSRM, leuven, mappymatch를 통행 x 간격마다 실행한다. 반환: 결과 행 리스트"""
    import osmnx as ox
    graph = ox.load_graphml(D / "graph_5179.graphml")
    M.OsrmMatcher.start(D / "osrm", "area_all.osm.pbf")
    matchers = {
        "valhalla": M.ValhallaMatcher(D / "valhalla_tiles"),
        "osrm": M.OsrmMatcher(),
        "leuven": M.LeuvenMatcher(graph),
        "mappymatch": M.MappyMatcher(graph),
    }
    rows = []
    try:
        for (tid, rs), p in sp.groupby(["trip_id", "resample_s"], sort=True):
            for name, m in matchers.items():
                o = m.match(p)
                rows.append(_row(tid, rs, name, o.geom, o.n_parts, o.matched_frac, o.runtime_s, o.error))
            print(f"\r[s1_bench] {tid[:8]} {rs}s", end="", flush=True)
    finally:
        M.OsrmMatcher.stop()
    print()
    return rows


def run_fmm(sp):
    """
    FMM은 컨테이너 안에서 일괄 실행한다 (s1_bench_fmm_job.py).
    궤적은 EPSG:5179 WKT로 넘긴다 (FMM은 도로망과 같은 좌표계의 미터 단위로 계산).
    반환: 결과 행 리스트
    """
    w = D / "fmm"
    lines = ["id;wkt"]
    for (tid, rs), p in sp.groupby(["trip_id", "resample_s"], sort=True):
        wkt = shapely.LineString(np.column_stack([p.x, p.y])).wkt
        lines.append(f"{tid}|{rs};{wkt}")
    (w / "traces.csv").write_text("\n".join(lines), encoding="utf-8")
    (w / "params.json").write_text(json.dumps({
        "k": 8, "radius": C.S1B_SEARCH_RADIUS_M, "gps_error": C.S1B_GPS_SIGMA_M,
        "ubodt_delta": C.FMM_UBODT_DELTA_M}), encoding="utf-8")
    shutil.copy(C.BASE_DIR / "src" / "s1_bench_fmm_job.py", D / "s1_bench_fmm_job.py")
    r = subprocess.run([M.DOCKER, "run", "--rm", "-v", f"{D.as_posix()}:/work", C.FMM_IMAGE,
                        "python3", "/work/s1_bench_fmm_job.py"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(r.stdout.strip()[-500:], r.stderr.strip()[-500:])
    res = pd.read_csv(w / "result.csv", sep=";", keep_default_na=False)
    rows = []
    for _, x in res.iterrows():
        tid, rs = x["id"].rsplit("|", 1)
        geom = shapely.from_wkt(x["wkt"]) if x["ok"] == 1 and x["wkt"] else None
        if geom is not None and geom.is_empty:
            geom = None
        rows.append(_row(tid, int(rs), "fmm", geom, 1 if geom is not None else 0,
                         1.0 if geom is not None else 0.0, float(x["runtime_s"]), x["error"]))
    return rows


def _row(tid, rs, lib, geom, n_parts, frac, runtime, err):
    return {"trip_id": tid, "resample_s": int(rs), "lib": lib,
            "geom_wkb": None if geom is None else shapely.to_wkb(geom),
            "n_parts": int(n_parts), "matched_frac": float(frac), "runtime_s": float(runtime), "error": err}


# ===========================================================================
# 2. 평가
# ===========================================================================
def overlap(a, b):
    """a의 길이 중 b의 S1B_AGREE_BUFFER_M 버퍼 안에 드는 비율."""
    if a is None or b is None or a.length == 0:
        return np.nan
    return a.intersection(b.buffer(C.S1B_AGREE_BUFFER_M)).length / a.length


def agreement(a, b):
    """두 경로의 겹침: 양방향 overlap 중 작은 값 (한쪽이 다른 쪽의 일부만 덮어도 낮게 나오도록)."""
    return np.nanmin([overlap(a, b), overlap(b, a)]) if a is not None and b is not None else np.nan


def gap_metrics(geom, p1):
    """
    1초 원본 점 p1에서 세션 안 공백(> S1B_GAP_MIN_S초)마다 앞뒤 점이 경로로 이어졌는지 본다.
    반환: [(bridged, detour), ...]
      bridged: 앞뒤 점이 모두 경로에서 S1B_OFFPATH_M 안이고 같은 조각에 붙었으면 True
      detour : bridged일 때 경로상 거리 / 직선거리
    """
    ts, x, y = p1.ts.to_numpy(), p1.x.to_numpy(), p1.y.to_numpy()
    out = []
    parts = list(geom.geoms) if geom is not None and geom.geom_type == "MultiLineString" else [geom]
    for i in np.flatnonzero(np.diff(ts) > C.S1B_GAP_MIN_S * 1000):
        a, b = shapely.Point(x[i], y[i]), shapely.Point(x[i + 1], y[i + 1])
        chord = a.distance(b)
        if geom is None or chord == 0:
            out.append((False, np.nan))
            continue
        da = [pt.distance(a) for pt in parts]
        db = [pt.distance(b) for pt in parts]
        ka, kb = int(np.argmin(da)), int(np.argmin(db))
        if ka != kb or da[ka] > C.S1B_OFFPATH_M or db[kb] > C.S1B_OFFPATH_M:
            out.append((False, np.nan))
            continue
        line = parts[ka]
        out.append((True, abs(line.project(b) - line.project(a)) / chord))
    return out


def evaluate(res, sp, trips):
    """결과 행마다 지표를 붙이고, 간격 견고성(1초 대비)을 계산한다. 반환: res + 지표 칼럼"""
    geoms = [None if w is None else shapely.from_wkb(w) for w in res.geom_wkb]
    res = res.assign(geom=geoms)
    p1 = {tid: g for tid, g in sp[sp.resample_s == 1].groupby("trip_id")}
    dist = trips.set_index("trip_id").dist_m
    rows = []
    for r in res.itertuples():
        p = p1[r.trip_id]
        g = r.geom
        m = {"ok": g is not None, "len_m": g.length if g is not None else 0.0}
        m["len_ratio"] = m["len_m"] / dist[r.trip_id] if g is not None else np.nan
        if g is not None:
            d = shapely.distance(g, shapely.points(p.x.to_numpy(), p.y.to_numpy()))
            m["offpath_share"] = float((d > C.S1B_OFFPATH_M).mean())
            m["dist_p95_m"] = float(np.percentile(d, 95))
        gm = gap_metrics(g, p)
        m["n_gaps"] = len(gm)
        m["gaps_bridged"] = sum(b for b, _ in gm)
        m["gap_detours"] = [d for b, d in gm if b]
        rows.append(m)
    res = pd.concat([res.reset_index(drop=True), pd.DataFrame(rows)], axis=1)

    base = res[res.resample_s == 1].set_index(["trip_id", "lib"]).geom
    res["agree_vs_1s"] = [agreement(g, base.get((t, lib))) if rs != 1 else 1.0
                          for g, t, lib, rs in zip(res.geom, res.trip_id, res.lib, res.resample_s)]
    return res


def summarize(res, trips):
    """라이브러리 x 간격 요약표, 공백 연결 요약, 라이브러리 간 일치 행렬을 출력한다."""
    strat = trips.set_index("trip_id").stratum
    res = res.assign(stratum=res.trip_id.map(strat))

    def q(s, v):
        s = pd.Series(s, dtype=float).dropna()
        return np.percentile(s, v) if len(s) else np.nan

    g = res.groupby(["lib", "resample_s"])
    summ = pd.DataFrame({
        "n": g.size(),
        "실패%": g.ok.apply(lambda s: 100 * (~s).mean()),
        "끊김%": g.n_parts.apply(lambda s: 100 * (s > 1).mean()),
        "매칭점비율_p50": g.matched_frac.median(),
        "길이비_p50": g.len_ratio.median(),
        "길이비_p90": g.len_ratio.apply(lambda s: q(s, 90)),
        "벗어난점%_p50": g.offpath_share.apply(lambda s: 100 * q(s, 50)),
        "벗어난점%_p90": g.offpath_share.apply(lambda s: 100 * q(s, 90)),
        "1초대비일치_p50": g.agree_vs_1s.median(),
        "1초대비일치_p10": g.agree_vs_1s.apply(lambda s: q(s, 10)),
        "시간_p50_s": g.runtime_s.median(),
        "시간_합_s": g.runtime_s.sum(),
    }).round(3)
    print("\n[라이브러리 x 샘플링 간격]")
    print(summ.to_string())

    gp = res[(res.stratum == "gap")].groupby(["lib", "resample_s"])
    gap = pd.DataFrame({
        "공백수": gp.n_gaps.sum(),
        "연결%": 100 * gp.gaps_bridged.sum() / gp.n_gaps.sum(),
        "우회율_p50": gp.gap_detours.apply(lambda s: q(np.concatenate([np.asarray(v, float) for v in s] or [[]]), 50)),
        "우회율_p90": gp.gap_detours.apply(lambda s: q(np.concatenate([np.asarray(v, float) for v in s] or [[]]), 90)),
    }).round(2)
    print("\n[세션 안 공백 연결] (공백 있음 층)")
    print(gap.to_string())

    one = res[res.resample_s == 1].set_index(["trip_id", "lib"]).geom
    tids = sorted(res.trip_id.unique())
    mat = pd.DataFrame(index=LIBS, columns=LIBS, dtype=float)
    for a, b in itertools.product(LIBS, LIBS):
        mat.loc[a, b] = np.nanmedian([agreement(one.get((t, a)), one.get((t, b))) for t in tids]) if a != b else 1.0
    print("\n[라이브러리 간 일치 (1초 입력, 통행별 겹침의 중앙값)]")
    print(mat.round(3).to_string())
    return summ, gap, mat


def plot_examples(res, sp, trips, n=3):
    """
    예시 통행 n개: 공백 통행 1개, 무공백 통행 1개, 라이브러리 간 불일치가 가장 큰 통행 1개.
    회색 점 = 1초 GPS, 선 = 라이브러리별 매칭 경로 (범주형 색 고정 순서)
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.family"] = "Malgun Gothic"
    plt.rcParams["axes.unicode_minus"] = False
    colors = dict(zip(LIBS, ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]))

    one = res[res.resample_s == 1]
    spread = one.groupby("trip_id").len_ratio.agg(lambda s: np.nanmax(s) - np.nanmin(s))
    strat = trips.set_index("trip_id").stratum
    rng = np.random.default_rng(C.S1B_SEED)
    pick = [rng.choice(sorted(strat.index[strat == "gap"])), rng.choice(sorted(strat.index[strat == "nogap"]))]
    pick.append(spread.drop(pick, errors="ignore").idxmax())
    titles = ["공백 있는 통행 (무작위)", "공백 없는 통행 (무작위)", "라이브러리 간 길이 차가 가장 큰 통행"]

    fig, axes = plt.subplots(1, n, figsize=(6.2 * n, 6.4))
    for ax, tid, title in zip(axes, pick, titles):
        p = sp[(sp.trip_id == tid) & (sp.resample_s == 1)]
        ax.scatter(p.x, p.y, s=5, color="#b0aea5", zorder=1, linewidths=0)
        lines = []
        for lib in LIBS:
            r = one[(one.trip_id == tid) & (one.lib == lib)].iloc[0]
            if r.geom is not None:
                for part in (r.geom.geoms if r.geom.geom_type == "MultiLineString" else [r.geom]):
                    xs, ys = part.xy
                    ax.plot(xs, ys, color=colors[lib], lw=1.6, alpha=0.85, zorder=2)
            lr = f"{r.len_ratio:.2f}" if r.ok else "실패"
            lines.append(f"{lib}: 길이비 {lr}, 조각 {r.n_parts}")
        ax.set_title(f"{title}\n{tid[:8]}  보정거리 {trips.set_index('trip_id').dist_m[tid]:,.0f}m",
                     fontsize=10, loc="left")
        ax.text(0.01, 0.01, "\n".join(lines), transform=ax.transAxes, fontsize=7.5, va="bottom",
                bbox=dict(facecolor="white", alpha=0.85, edgecolor="none", pad=2))
        ax.set_aspect("equal", adjustable="datalim")
        ax.tick_params(labelsize=7)
        ax.ticklabel_format(useOffset=False, style="plain")
        ax.grid(color="#e6e5e0", lw=0.5)
    handles = [plt.Line2D([], [], color=colors[k], lw=2, label=k) for k in LIBS]
    handles.insert(0, plt.Line2D([], [], ls="", marker="o", ms=4, color="#b0aea5", label="GPS (1초)"))
    fig.legend(handles=handles, loc="upper right", ncol=6, fontsize=9, frameon=False)
    fig.suptitle("맵매칭 라이브러리 비교 예시 (EPSG:5179, m)", x=0.01, ha="left", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(OUT_FIG, dpi=120)
    print(f"[s1_bench] 그림 저장: {OUT_FIG}")


def main(force=False):
    warnings.filterwarnings("ignore")
    logging.getLogger("be.kuleuven.cs.dtai.mapmatching").setLevel(logging.WARNING)
    C.setup_environment()
    sp = pd.read_parquet(D / "sample_points.parquet")
    trips = pd.read_parquet(D / "sample_trips.parquet")

    # 엔진 묶음별로 따로 저장한다. 한쪽이 실패해도 다른 쪽 결과를 다시 돌리지 않도록.
    parts = {OUT_PY: run_python_libs, OUT_FMM: run_fmm}
    for path, fn in parts.items():
        if force or not path.exists():
            pd.DataFrame(fn(sp)).to_parquet(path, index=False)
    res = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    res.to_parquet(OUT_RESULTS, index=False)
    res = evaluate(res, sp, trips)
    summarize(res, trips)
    plot_examples(res, sp, trips)
    res.drop(columns=["geom"]).to_parquet(D / "results_metrics.parquet", index=False)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="매칭부터 다시")
    main(force=ap.parse_args().force)
