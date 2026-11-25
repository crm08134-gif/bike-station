"""
연구 대상 지역 후보 비교 (REGION_CANDIDATES, 예: 서울 vs 서울+고양)

전제
  후보 지역 = 대여소 후보지를 둘 범위. 수요 경로는 지역 밖 구간도 잘라내지 않고 유지한다고 보고,
  '지역 안에서 REGION_MIN_TRIP_M 이상 관측된 통행'을 그 지역의 통행으로 센다.
  거리는 region_density와 같이 관측 구간(간격 REGION_OBS_MAX_DT_S 이하)만 쓴다. 모두 EPSG:5179.

비교 항목 (후보마다)
  데이터 양    : 통행, 세션, 제공처 레코드, 날짜 수, 지역 안 관측 km, 그 통행들의 전체 관측 km
  사용자       : 인원, 1인당 통행(중앙값·최대), 통행 5개 이상 사용자, 상위 1/3/5명의 km 비중,
                 유효 사용자 수 = 1 / Σ(사용자별 km 비중²)  (모두 똑같이 탔다면 인원수와 같다)
  공간 분포    : 통행이 지나간 REGION_GRID_M 격자 수, 2명/3명 이상 지나간 격자 비율
  경계         : 지역 밖으로 REGION_MIN_TRIP_M 이상 이어지는 통행 수와 그 통행의 지역 밖 거리 비율
  계산 규모    : 통행 주변 S1B_BUFFER_M 버퍼 합집합 넓이 (맵매칭 도로망 영역)
추가로, 두 번째 후보가 첫 번째에 더해 주는 몫을 시군구별로 따로 본다.

입력  : s0_points.parquet, s0_trips.parquet, C.SESSIONS_META_PATH, C.ADMIN_BOUNDARY_PATH
출력  : data/output/region_compare.parquet  후보당 한 행
        data/output/region_compare.png      격자별 사용자 수 지도 + 상위 사용자 누적 비중

실행
  python region_compare.py
"""
import warnings

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

import config as C
import region_density as R
import s0_extract as S0

OUT = C.DATA_OUTPUT / "region_compare.parquet"
OUT_FIG = C.DATA_OUTPUT / "region_compare.png"


def load_segments():
    """
    관측 구간을 시군구에 붙인다.
    반환: (seg, sgg, meta)
      seg : [trip_id, user, mx, my, len_m, sido, sgg_name]  (시군구 밖 구간은 sido가 NaN)
      sgg : 시군구 경계 (EPSG:5179)
      meta: 세션 메타 (session_id, user, source_records)
    """
    pts = pd.read_parquet(S0.OUT_POINTS)
    meta = pd.read_csv(C.SESSIONS_META_PATH, dtype=str)
    seg = R.observed_segments(pts, meta)
    sgg = R.load_sgg()
    g = gpd.GeoDataFrame(seg, geometry=gpd.points_from_xy(seg.mx, seg.my), crs=C.CRS_WORK)
    j = gpd.sjoin(g, sgg[["sido", "sgg_name", "geometry"]], how="left", predicate="within")
    return pd.DataFrame(j.drop(columns=["geometry", "index_right"])), sgg, meta


def in_region(df, rules):
    """(시도, 시군구 접두어) 규칙 중 하나라도 맞으면 True. df에는 sido, sgg_name 칼럼이 있어야 한다."""
    m = pd.Series(False, index=df.index)
    for sido, prefix in rules:
        hit = df.sido == sido
        if prefix is not None:
            hit &= df.sgg_name.str.startswith(prefix, na=False)
        m |= hit.fillna(False)
    return m


def region_metrics(seg, trips, meta, sgg, rules):
    """
    후보 지역 하나의 지표를 계산한다.
    반환: dict (지표 이름 -> 값), 지역 통행 id 집합, 사용자별 km(Series)
    """
    inside = in_region(seg, rules)
    s = seg[inside]
    per_trip_in = s.groupby("trip_id").len_m.sum()
    tids = per_trip_in.index[per_trip_in >= C.REGION_MIN_TRIP_M]
    s = s[s.trip_id.isin(tids)]
    all_of_trips = seg[seg.trip_id.isin(tids)]

    t = trips.set_index("trip_id").loc[tids]
    m = meta.set_index("session_id")
    user_of_trip = m.user.reindex(t.session_id).to_numpy()
    rec_of_trip = m.source_records.reindex(t.session_id).to_numpy()
    days = pd.to_datetime(t.start_ts, unit="ms", utc=True).dt.tz_convert(C.TIMEZONE).dt.date

    km_user = s.groupby("user").len_m.sum().sort_values(ascending=False) / 1000
    share = km_user / km_user.sum()
    trips_per_user = pd.Series(user_of_trip).value_counts()

    cx, cy = (s.mx // C.REGION_GRID_M).astype(int), (s.my // C.REGION_GRID_M).astype(int)
    cell = s.assign(cx=cx, cy=cy).groupby(["cx", "cy"]).user.nunique()

    out_km = all_of_trips[~in_region(all_of_trips, rules)].groupby("trip_id").len_m.sum()
    crossing = out_km[out_km >= C.REGION_MIN_TRIP_M]
    tot_km = all_of_trips.groupby("trip_id").len_m.sum()

    admin = sgg[in_region(sgg, rules)].area.sum() / 1e6
    buf = shapely.union_all(shapely.buffer(shapely.points(all_of_trips.mx.to_numpy()[::5],
                                                          all_of_trips.my.to_numpy()[::5]), C.S1B_BUFFER_M))
    res = {
        "행정면적_km2": admin,
        "통행": len(tids),
        "세션": t.session_id.nunique(),
        "제공처레코드": pd.Series(rec_of_trip).nunique(),
        "날짜수": days.nunique(),
        "지역안_관측km": s.len_m.sum() / 1000,
        "통행전체_관측km": all_of_trips.len_m.sum() / 1000,
        "밀도_km_per_km2": s.len_m.sum() / 1000 / admin,
        "사용자": len(km_user),
        "1인당통행_중앙값": trips_per_user.median(),
        "1인당통행_최대": trips_per_user.max(),
        "통행5개이상_사용자": int((trips_per_user >= 5).sum()),
        "상위1명_km비중": share.iloc[:1].sum(),
        "상위3명_km비중": share.iloc[:3].sum(),
        "상위5명_km비중": share.iloc[:5].sum(),
        "유효사용자수": 1 / (share ** 2).sum(),
        "격자수": len(cell),
        "격자_2명이상_비율": (cell >= 2).mean(),
        "격자_3명이상_비율": (cell >= 3).mean(),
        "경계넘는통행": len(crossing),
        "경계넘는통행_밖거리비율_중앙값": (crossing / tot_km[crossing.index]).median() if len(crossing) else np.nan,
        "도로망영역_km2": buf.area / 1e6,
    }
    return res, set(tids), km_user


def increment(seg, trips, meta, base_rules, full_rules):
    """
    full 후보가 base 후보에 더해 주는 몫을 시군구별로 본다.
    반환: DataFrame (추가 시군구당 한 행: 관측 km, 통행, 사용자, 그중 base에도 있는 사용자, 최다 사용자 비중)
    """
    base_users = set(seg[in_region(seg, base_rules)].user)
    extra = seg[in_region(seg, full_rules) & ~in_region(seg, base_rules)]
    rows = []
    for name, s in extra.groupby("sgg_name"):
        per_trip = s.groupby("trip_id").len_m.sum()
        tids = per_trip.index[per_trip >= C.REGION_MIN_TRIP_M]
        s = s[s.trip_id.isin(tids)]
        ku = s.groupby("user").len_m.sum()
        only_here = [tid for tid in tids if not seg[(seg.trip_id == tid) & in_region(seg, base_rules)].len_m.sum()
                     >= C.REGION_MIN_TRIP_M]
        rows.append({"시군구": name, "관측km": s.len_m.sum() / 1000, "통행": len(tids),
                     "그중_기존지역안거치는통행": len(tids) - len(only_here),
                     "사용자": s.user.nunique(), "그중_기존지역사용자": len(set(s.user) & base_users),
                     "최다사용자_km비중": ku.max() / ku.sum() if len(ku) else np.nan})
    return pd.DataFrame(rows)


def plot(seg, sgg, results, km_users):
    """
    왼쪽: 두 번째 후보 범위의 격자별 서로 다른 사용자 수 (순차 단색 4단계) + 후보 경계
    오른쪽: 상위 k명 사용자의 누적 km 비중 (후보별 선)
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    plt.rcParams["font.family"] = "Malgun Gothic"
    plt.rcParams["axes.unicode_minus"] = False
    names = list(C.REGION_CANDIDATES)
    line_colors = ["#2a78d6", "#eb6834"]
    ramp = ["#86b6ef", "#3987e5", "#1c5cab", "#0d366b"]          # 1명, 2명, 3~4명, 5명 이상
    bins, labels = [0, 1, 2, 4, np.inf], ["1명", "2명", "3~4명", "5명 이상"]

    full = C.REGION_CANDIDATES[names[-1]]
    s = seg[in_region(seg, full)]
    G = C.REGION_GRID_M
    cell = s.assign(cx=(s.mx // G).astype(int), cy=(s.my // G).astype(int)).groupby(["cx", "cy"]).user.nunique()
    cls = pd.cut(cell, bins, labels=False)

    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(16, 7.6), gridspec_kw={"width_ratios": [1.35, 1]})
    area = sgg[in_region(sgg, full)]
    area.boundary.plot(ax=ax, color="#d9d8d2", lw=0.5)
    for (cx, cy), k in cls.items():
        ax.add_patch(plt.Rectangle((cx * G, cy * G), G, G, color=ramp[int(k)], lw=0))
    for name, color in zip(names, line_colors):
        sgg[in_region(sgg, C.REGION_CANDIDATES[name])].dissolve().boundary.plot(ax=ax, color=color, lw=1.4)
    ax.set_aspect("equal")
    ax.set_title(f"{G}m 격자별 지나간 사용자 수 ({names[-1]} 범위)", loc="left", fontsize=11)
    ax.tick_params(labelsize=7)
    ax.ticklabel_format(useOffset=False, style="plain")
    handles = [Patch(color=c, label=l) for c, l in zip(ramp, labels)]
    handles += [plt.Line2D([], [], color=c, lw=1.6, label=f"{n} 경계") for n, c in zip(names, line_colors)]
    ax.legend(handles=handles, loc="lower left", fontsize=8, frameon=True, framealpha=0.9)

    for name, color in zip(names, line_colors):
        ku = km_users[name]
        cum = np.cumsum(ku.to_numpy()) / ku.sum()
        ax2.plot(np.arange(1, len(cum) + 1), cum * 100, color=color, lw=2, label=f"{name} (사용자 {len(ku)}명)")
    for k in (1, 5, 10):
        v = {n: np.cumsum(km_users[n].to_numpy())[k - 1] / km_users[n].sum() * 100 for n in names}
        ax2.annotate(" / ".join(f"{v[n]:.0f}%" for n in names), (k, v[names[0]]), textcoords="offset points",
                     xytext=(6, -12), fontsize=8, color="#555")
    ax2.set_xlabel("상위 k명 (지역 안 관측 km 순)")
    ax2.set_ylabel("누적 km 비중 (%)")
    ax2.set_ylim(0, 100)
    ax2.grid(color="#e6e5e0", lw=0.5)
    ax2.legend(loc="lower right", fontsize=9, frameon=False)
    ax2.set_title("상위 사용자 쏠림", loc="left", fontsize=11)
    fig.tight_layout()
    fig.savefig(OUT_FIG, dpi=120)
    print(f"[region_compare] 그림 저장: {OUT_FIG}")


def main():
    warnings.filterwarnings("ignore")
    C.setup_environment()
    seg, sgg, meta = load_segments()
    trips = pd.read_parquet(S0.OUT_TRIPS)

    rows, km_users = {}, {}
    for name, rules in C.REGION_CANDIDATES.items():
        rows[name], _, km_users[name] = region_metrics(seg, trips, meta, sgg, rules)
    table = pd.DataFrame(rows)
    names = list(C.REGION_CANDIDATES)
    if len(names) == 2:
        table["차이"] = table[names[1]] - table[names[0]]
    table.T.reset_index(names="후보").to_parquet(OUT, index=False)
    with pd.option_context("display.float_format", "{:,.3f}".format):
        print(table.to_string())

    if len(names) == 2:
        inc = increment(seg, trips, meta, C.REGION_CANDIDATES[names[0]], C.REGION_CANDIDATES[names[1]])
        print(f"\n[{names[1]}이 {names[0]}에 더하는 몫, 시군구별]")
        print(inc.round(3).to_string(index=False))
    plot(seg, sgg, rows, km_users)


if __name__ == "__main__":
    main()
