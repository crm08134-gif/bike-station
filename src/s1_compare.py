"""
1단계 2차 비교: Valhalla vs OSRM (연구 지역 여정 전체, 본 실행과 같은 방식)

1차 비교(s1_bench_*, 표본 60개)에서 Valhalla와 OSRM이 앞섰다. 2차는 두 엔진을 본 실행 방식 그대로 돌려 고른다.
  조각별 매칭 + 끊긴 곳과 조각 사이 공백은 엔진의 자전거 최단경로로 잇기 (s1_match.match_journey)

지표 (여정별, EPSG:5179)
  실패         : 경로를 하나도 못 만든 여정
  매칭 끊김    : 한 조각 안에서 매칭이 끊겨 inner bridge로 이은 수
  길이비       : (경로 길이 - gap bridge 길이) / 관측 조각 보정 거리. 1 근처가 정상
  벗어난 점    : 1초 GPS 점 중 경로에서 S1B_OFFPATH_M보다 먼 점의 비율
  gap bridge   : 경로 거리 / 직선거리 (우회율), 필요 속도 = 경로 거리 / 공백 시간.
                 필요 속도가 자전거로 불가능하게 높으면 공백을 잇는 경로가 너무 멀리 돈 것
  엔진 간 일치 : 두 엔진 경로가 서로 S1B_AGREE_BUFFER_M 안에 드는 길이 비율 (양방향 중 작은 값)
  처리 시간    : 여정당 초

출력  : data/interim/s1/compare.parquet, data/output/s1_compare_examples.png

실행
  python s1_compare.py
"""
import numpy as np
import pandas as pd
import shapely

import config as C
import s0_extract as S0
import s1_bench_run as BR
import s1_match as SM
import s1_network as N

OUT = C.S1_DIR / "compare.parquet"
OUT_FIG = C.DATA_OUTPUT / "s1_compare_examples.png"


def q(s, ps=(50, 90)):
    s = pd.Series(s).dropna()
    return " / ".join(f"{np.percentile(s, p):.2f}" for p in ps) if len(s) else "-"


def summarize(name, res, br):
    ok = res[res.error == ""]
    gap = br[br.kind == "gap"]
    need = gap.route_m / gap.dt_s * 3.6
    return {
        "엔진": name,
        "실패": f"{(res.error != '').sum()} / {len(res)}",
        "매칭 끊김 있는 여정": f"{(ok.n_inner > 0).mean():.0%}",
        "길이비 p50 / p90": q(ok.len_ratio),
        "벗어난 점 p50 / p90": q(ok.offpath),
        "gap 연결": f"{len(gap)} / {int(res.n_gap_expected.sum())}",
        "gap 우회율 p50 / p90": q(gap.route_m / gap.chord_m),
        "gap 필요속도 km/h p50 / p90 / 최대": q(need, (50, 90, 100)),
        "gap 필요속도 > 25km/h": int((need > 25).sum()),
        "여정당 초": f"{ok.runtime_s.mean():.3f}",
    }


def plot(results, jp, n=4):
    """gap bridge가 있는 여정 중 두 엔진 일치가 낮은 순으로 n개: GPS 점, 두 엔진 경로"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    a, b = results["osrm"], results["valhalla"]
    m = a[["journey_id", "geom", "n_gap", "agree"]].merge(b[["journey_id", "geom"]], on="journey_id", suffixes=("_o", "_v"))
    m = m[m.n_gap > 0].sort_values("agree").head(n)
    fig, axes = plt.subplots(1, len(m), figsize=(5 * len(m), 5))
    for ax, r in zip(np.atleast_1d(axes), m.itertuples()):
        g = jp[jp.journey_id == r.journey_id]
        for pno, gp in g.groupby("piece_no"):
            ax.plot(gp.x, gp.y, ".", ms=2, color="0.6")
        for geom, col, lab in [(r.geom_o, "tab:blue", "OSRM"), (r.geom_v, "tab:orange", "Valhalla")]:
            if geom is not None:
                c = shapely.get_coordinates(geom)
                ax.plot(c[:, 0], c[:, 1], "-", color=col, lw=1.5, alpha=0.8, label=lab)
        ax.set_title(f"{r.journey_id[:8]} gap {r.n_gap}", fontsize=9)
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])
    np.atleast_1d(axes)[0].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT_FIG, dpi=120)


def main():
    C.setup_environment()
    jp = N.journey_points()
    trips = pd.read_parquet(S0.OUT_TRIPS)
    n_gap_expected = jp.groupby("journey_id").piece_no.nunique() - 1
    results, bridges = {}, {}
    for name in ["osrm", "valhalla"]:
        eng = SM.ENGINES[name]
        eng.start()
        try:
            res, br = SM.run(name, jp, trips)
        finally:
            eng.stop()
        res = SM.quality(res, br, jp, trips)
        res["n_gap_expected"] = res.journey_id.map(n_gap_expected)
        results[name], bridges[name] = res, br

    a = results["osrm"].set_index("journey_id").geom
    b = results["valhalla"].set_index("journey_id").geom
    agree = pd.Series({j: BR.agreement(a[j], b.get(j)) for j in a.index})
    results["osrm"]["agree"] = results["osrm"].journey_id.map(agree)

    rows = [summarize(n, results[n], bridges[n]) for n in results]
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 20)
    print(pd.DataFrame(rows).set_index("엔진").T.to_string())
    print(f"\n엔진 간 일치 (여정별) p10 / p50: {q(agree, (10, 50))}, 0.9 미만 여정 {(agree < 0.9).sum()}개")

    out = []
    for n, r in results.items():
        out.append(r.drop(columns=["geom"]).assign(engine=n,
                                                           geom_wkb=[None if g is None else shapely.to_wkb(g) for g in r.geom]))
    pd.concat(out, ignore_index=True).to_parquet(OUT, index=False)
    pd.concat([bridges[n].assign(engine=n) for n in bridges], ignore_index=True) \
        .to_parquet(C.S1_DIR / "compare_bridges.parquet", index=False)
    plot(results, jp)
    print(f"[s1_compare] 저장: {OUT.name}, {OUT_FIG}")


if __name__ == "__main__":
    main()
