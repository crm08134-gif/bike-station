"""
0단계 육안 확인: 정제 규칙이 실제 데이터에서 의도대로 작동했는지 그림으로 본다.

입력  : 원본 덤프 (s0_extract와 같은 설정), data/interim/s0_trips.parquet, s0_stats.json
출력  : data/output/s0_check.png  통행 6개 패널 (EPSG:5179 x/y 좌표 그대로)
          - 튐이 제거된 통행 중 제거 수가 가장 많은 2개
          - 시간 공백으로 분할된 세션 1개 (조각마다 다른 색)
          - too_short 또는 too_slow로 제외된 통행 1개
          - 정상(유지된) 통행 무작위 2개
        표시: 정제 전 포인트 = 회색 점, 남은 포인트 = 파란 선, 튐으로 제거된 포인트 = 빨간 X

왜 원본부터 다시 계산하는가
  s0_points.parquet에는 '유지된 통행의 튐 제거 후 포인트'만 있다.
  어떤 점이 튐으로 지워졌는지, 제외된 통행의 점이 어땠는지는 저장돼 있지 않으므로
  s0_extract의 함수를 같은 순서로 다시 돌려 재현한다 (수 초 걸림).
  재현 결과가 s0_stats.json과 다르면 멈춘다 (설정이 바뀐 채로 그림을 그리는 것을 막기 위함).

실행
  python s0_inspect.py
"""
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import config as C  # noqa: E402
import s0_extract as S0  # noqa: E402

OUT_PNG = C.DATA_OUTPUT / "s0_check.png"

# 색 (정제 전 = 회색, 남은 경로 = 파랑, 튐 제거 = 빨강). 분할 조각은 범주형 순서대로.
GRAY, BLUE, RED = "#b0aea5", "#2a78d6", "#d0312d"
PIECE_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]

plt.rcParams["font.family"] = "Malgun Gothic"      # 한글 제목 (윈도우 기본 글꼴)
plt.rcParams["axes.unicode_minus"] = False


def rebuild_points():
    """
    s0_extract.clean()의 튐 제거까지를 같은 순서로 재현한다.

    반환: DataFrame [trip_id, session_id, ts, lon, lat, x, y, model_version, spike_removed(bool)]
          튐 제거 '전' 모든 포인트. spike_removed=True인 점이 튐으로 지워진 점이다.
    """
    log = {}
    df = S0.filter_scope(S0.load_raw(), log)
    df = S0.sort_dedup(df, log)
    df = S0.project(df)
    df = S0.split_trips(df, log)
    pre = df.sort_values(["trip_id", "ts"], kind="mergesort").reset_index(drop=True)
    post = S0.remove_spikes(pre, log)

    # (trip_id, ts)는 통행 안에서 유일하므로 이 키로 남은 점을 표시한다
    kept_key = pd.MultiIndex.from_frame(post[["trip_id", "ts"]])
    pre["spike_removed"] = ~pd.MultiIndex.from_frame(pre[["trip_id", "ts"]]).isin(kept_key)

    # 저장된 통계와 재현 결과가 같은지 확인
    stats = json.loads(S0.OUT_STATS.read_text(encoding="utf-8"))
    for k in ["n_points_after_scope", "n_gap_splits", "n_trips_after_split", "n_spike_points_removed"]:
        if log[k] != stats[k]:
            raise RuntimeError(f"재현 결과가 s0_stats.json과 다릅니다: {k} {log[k]} != {stats[k]}. "
                               "s0_extract.py --force를 다시 실행하세요.")
    return pre


def pick_panels(pre, trips, rng):
    """
    그릴 대상 6개를 고른다.

    입력: pre (rebuild_points 결과), trips (s0_trips.parquet), rng (시드 고정 Generator)
    반환: [(제목, trip_id 리스트, 비고)] 6개. 대상이 없으면 trip_id 리스트가 빈 패널.
    """
    panels = []
    used = set()

    # 1-2. 튐 제거 수가 가장 많은 통행 2개 (동률이면 trip_id 순)
    n_spk = pre.groupby("trip_id").spike_removed.sum()
    n_spk = n_spk[n_spk > 0].sort_values(ascending=False, kind="mergesort")
    for i in range(2):
        if i < len(n_spk):
            tid = n_spk.index[i]
            panels.append((f"튐 제거 {i + 1}위", [tid], ""))
            used.add(tid)
        else:
            panels.append((f"튐 제거 {i + 1}위", [], "해당 통행 없음"))

    # 3. 시간 공백으로 분할된 세션
    per_sess = trips.groupby("session_id").trip_id.apply(sorted)
    split = per_sess[per_sess.str.len() > 1]
    if len(split):
        sid = split.index[rng.integers(len(split))]
        panels.append(("공백 분할 세션", split[sid], f"session {sid[:8]}"))
    else:
        # 분할이 한 건도 없으면, 세션 안 최대 시간 공백이 가장 큰 세션을 참고로 보여준다
        dt = pre.groupby("session_id").ts.apply(lambda s: np.diff(s.to_numpy()).max(initial=0) / 1000)
        sid = dt.idxmax()
        panels.append(("공백 분할 세션 없음 (참고: 최대 공백 세션)", per_sess[sid],
                       f"최대 공백 {dt[sid]:.0f}초 ≤ SPLIT_GAP_S {C.SPLIT_GAP_S}초"))
    used.update(panels[-1][1])

    # 4. too_short 또는 too_slow로 제외된 통행
    cand = trips[trips.drop_reason.isin(["too_short", "too_slow"]) & ~trips.trip_id.isin(used)].trip_id
    if len(cand):
        tid = cand.iloc[rng.integers(len(cand))]
        panels.append(("제외된 통행", [tid], ""))
        used.add(tid)
    else:
        panels.append(("제외된 통행", [], "해당 통행 없음"))

    # 5-6. 정상 통행 무작위 2개
    cand = trips[trips.keep & ~trips.trip_id.isin(used)].trip_id.to_numpy()
    for i, tid in enumerate(rng.choice(cand, size=min(2, len(cand)), replace=False)):
        panels.append((f"정상 통행 {i + 1}", [tid], ""))
    return panels


def trip_label(t):
    """통행 요약 한 줄: 포인트 수, 보정 거리, 시간, 평균속도, 판정."""
    verdict = "유지" if t.keep else f"제외: {t.drop_reason}"
    return (f"{t.trip_id[:8]}…{t.trip_id[-4:]}  {t.n_points}점  {t.dist_m:,.0f}m  "
            f"{t.duration_s / 60:.1f}분  {t.avg_kmh:.1f}km/h  [{verdict}]")


def draw_panel(ax, title, trip_ids, note, pre, trips):
    """
    패널 하나를 그린다.
    회색 점 = 정제 전 모든 포인트, 선 = 튐 제거 후 남은 포인트(조각별 색), 빨간 X = 튐으로 제거된 포인트.
    """
    ax.set_title(title, fontsize=11, loc="left", fontweight="bold")
    if not trip_ids:
        ax.text(0.5, 0.5, note, ha="center", va="center", transform=ax.transAxes, color="#555")
        ax.set_xticks([]), ax.set_yticks([])
        return

    lines = []
    for k, tid in enumerate(trip_ids):
        p = pre[pre.trip_id == tid]
        ok, bad = p[~p.spike_removed], p[p.spike_removed]
        color = PIECE_COLORS[k % len(PIECE_COLORS)] if len(trip_ids) > 1 else BLUE
        ax.scatter(p.x, p.y, s=6, color=GRAY, zorder=1, linewidths=0)
        ax.plot(ok.x, ok.y, color=color, lw=1.5, zorder=2)
        ax.plot(ok.x.iloc[0], ok.y.iloc[0], "o", ms=5, color=color, zorder=3)          # 시작점 표시
        if len(bad):
            ax.scatter(bad.x, bad.y, marker="x", s=60, color=RED, linewidths=2, zorder=4)
        t = trips.set_index("trip_id").loc[tid]
        t["trip_id"] = tid
        lines.append(trip_label(t) + (f"  튐 제거 {len(bad)}" if len(bad) else ""))
    if note:
        lines.append(note)
    ax.text(0.01, 0.99, "\n".join(lines), transform=ax.transAxes, va="top", fontsize=7.5,
            bbox=dict(facecolor="white", alpha=0.85, edgecolor="none", pad=2))
    ax.set_aspect("equal", adjustable="datalim")
    ax.tick_params(labelsize=7)
    ax.ticklabel_format(useOffset=False, style="plain")
    ax.grid(color="#e6e5e0", lw=0.5)


def describe_spikes(pre):
    """
    튐으로 제거된 점마다 직전·직후 '남은 점'과의 거리·속도를 출력한다 (제거가 타당했는지 수치로 확인).
    반환: DataFrame (제거된 점 하나당 한 행)
    """
    rows = []
    for tid, p in pre.groupby("trip_id"):
        if not p.spike_removed.any():
            continue
        p = p.reset_index(drop=True)
        ok_idx = np.flatnonzero(~p.spike_removed.to_numpy())
        for i in np.flatnonzero(p.spike_removed.to_numpy()):
            row = {"trip_id": tid[:8], "idx": int(i), "n": len(p)}
            for side, j in (("prev", ok_idx[ok_idx < i][-1:]), ("next", ok_idx[ok_idx > i][:1])):
                if len(j):
                    j = j[0]
                    d = float(np.hypot(p.x[i] - p.x[j], p.y[i] - p.y[j]))
                    dt = abs(int(p.ts[i] - p.ts[j])) / 1000
                    row[f"{side}_m"], row[f"{side}_s"], row[f"{side}_kmh"] = round(d, 1), dt, round(d / dt * 3.6, 1)
            rows.append(row)
    return pd.DataFrame(rows)


def main():
    C.setup_environment()
    pre = rebuild_points()
    trips = pd.read_parquet(S0.OUT_TRIPS)
    rng = np.random.default_rng(C.S0_INSPECT_SEED)
    panels = pick_panels(pre, trips, rng)

    fig, axes = plt.subplots(2, 3, figsize=(18, 11.5))
    for ax, (title, tids, note) in zip(axes.flat, panels):
        draw_panel(ax, title, tids, note, pre, trips)
    handles = [plt.Line2D([], [], ls="", marker="o", ms=4, color=GRAY, label="정제 전 포인트"),
               plt.Line2D([], [], color=BLUE, lw=1.5, label="남은 포인트 (● 시작점)"),
               plt.Line2D([], [], ls="", marker="x", ms=8, mew=2, color=RED, label="튐으로 제거")]
    fig.legend(handles=handles, loc="upper right", ncol=3, fontsize=9, frameon=False)
    fig.suptitle("0단계 정제 육안 확인 (EPSG:5179, m)", x=0.01, ha="left", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(OUT_PNG, dpi=130)
    print(f"[s0_inspect] 저장: {OUT_PNG}")

    for title, tids, note in panels:
        print(f"  {title}: {', '.join(tids) or '-'} {note}")
    spk = describe_spikes(pre)
    if len(spk):
        print("\n튐으로 제거된 점과 직전/직후 남은 점 사이 거리·속도")
        print(spk.to_string(index=False))


if __name__ == "__main__":
    main()
