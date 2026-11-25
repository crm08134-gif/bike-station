"""
연구 대상 지역 선정용: 시군구별 자전거 통행 밀도

무엇을 세는가 (0단계에서 유지된 통행만)
  - 관측 주행거리 km : 연속 포인트 구간 중 간격이 REGION_OBS_MAX_DT_S 이하인 것만,
                       구간 중점이 속한 시군구에 길이를 더한다.
                       (61초 결측 블록을 잇는 직선은 실제 경로가 아니므로 뺀다)
  - 밀도 km/km²     : 관측 주행거리 / 시군구 면적
  - 통행 수          : 그 시군구 안에서 REGION_MIN_TRIP_M 이상 관측된 통행 수
  - 사용자 수        : 위 통행의 서로 다른 사용자 수
  - 최다 사용자 비중 : 관측 주행거리 중 가장 많이 탄 한 사람의 몫
                       (사용자가 115명뿐이라 한 사람의 통근 경로가 밀도를 좌우할 수 있음)
시군구는 일반구 단위다 (예: 수원시 장안구). 거리와 면적은 모두 EPSG:5179에서 계산한다.

입력  : data/interim/s0_points.parquet, C.SESSIONS_META_PATH (session_id -> user), C.ADMIN_BOUNDARY_PATH
출력  : data/output/region_density.parquet  시군구당 한 행

실행
  python region_density.py
"""
import geopandas as gpd
import numpy as np
import pandas as pd

import config as C
import s0_extract as S0

OUT = C.DATA_OUTPUT / "region_density.parquet"


def load_sgg():
    """
    행정동 경계를 시군구로 합친다.
    반환: GeoDataFrame [sgg(코드), sido, sgg_name, area_km2, geometry] (EPSG:5179)
    """
    dong = gpd.read_file(C.ADMIN_BOUNDARY_PATH).to_crs(C.CRS_WORK)
    sgg = dong.dissolve(by="sgg", aggfunc={"sidonm": "first", "sggnm": "first"}).reset_index()
    sgg = sgg.rename(columns={"sidonm": "sido", "sggnm": "sgg_name"})
    sgg["area_km2"] = sgg.area / 1e6
    return sgg[["sgg", "sido", "sgg_name", "area_km2", "geometry"]]


def observed_segments(points, meta):
    """
    통행 안의 연속 포인트 구간을 만든다.

    입력: s0_points (trip_id, ts 정렬), 세션 메타 (session_id, user)
    반환: DataFrame [trip_id, user, mx, my(구간 중점), len_m] -- 간격 REGION_OBS_MAX_DT_S 이하 구간만
    """
    p = points.sort_values(["trip_id", "ts"]).merge(meta[["session_id", "user"]], on="session_id", how="left")
    same = p.trip_id.to_numpy()[1:] == p.trip_id.to_numpy()[:-1]
    dt = np.diff(p.ts.to_numpy()) / 1000
    x, y = p.x.to_numpy(), p.y.to_numpy()
    ok = same & (dt <= C.REGION_OBS_MAX_DT_S)
    return pd.DataFrame({
        "trip_id": p.trip_id.to_numpy()[1:][ok],
        "user": p.user.to_numpy()[1:][ok],
        "mx": ((x[1:] + x[:-1]) / 2)[ok],
        "my": ((y[1:] + y[:-1]) / 2)[ok],
        "len_m": np.hypot(np.diff(x), np.diff(y))[ok],
    })


def aggregate(seg, sgg):
    """
    구간을 시군구에 공간조인해 시군구별 지표를 계산한다.
    반환: DataFrame (시군구당 한 행, 관측 주행거리 내림차순). 통행이 없는 시군구는 뺀다.
    """
    g = gpd.GeoDataFrame(seg, geometry=gpd.points_from_xy(seg.mx, seg.my), crs=C.CRS_WORK)
    j = gpd.sjoin(g, sgg[["sgg", "geometry"]], how="inner", predicate="within")

    per_trip = j.groupby(["sgg", "trip_id", "user"]).len_m.sum().reset_index()
    per_trip = per_trip[per_trip.len_m >= C.REGION_MIN_TRIP_M]
    per_user = j.groupby(["sgg", "user"]).len_m.sum()

    out = pd.DataFrame({
        "km": j.groupby("sgg").len_m.sum() / 1000,
        "trips": per_trip.groupby("sgg").trip_id.nunique(),
        "users": per_trip.groupby("sgg").user.nunique(),
        "top_user_share": per_user.groupby("sgg").max() / per_user.groupby("sgg").sum(),
    }).fillna({"trips": 0, "users": 0})
    out = sgg.drop(columns="geometry").set_index("sgg").join(out, how="inner")
    out["km_per_km2"] = out.km / out.area_km2
    out["share_km"] = out.km / out.km.sum()
    return out.sort_values("km", ascending=False).reset_index()


def main():
    C.setup_environment()
    points = pd.read_parquet(S0.OUT_POINTS)
    meta = pd.read_csv(C.SESSIONS_META_PATH, dtype=str)
    seg = observed_segments(points, meta)
    res = aggregate(seg, load_sgg())
    res.to_parquet(OUT, index=False)

    total_km = seg.len_m.sum() / 1000
    print(f"관측 주행거리 합계 {total_km:,.1f} km (시군구에 배정 {res.km.sum():,.1f} km), "
          f"통행이 지나간 시군구 {len(res)}개")
    by_sido = res.groupby("sido").agg(km=("km", "sum"), sgg=("sgg", "size"))
    by_sido["share"] = by_sido.km / res.km.sum()
    print(by_sido.sort_values("km", ascending=False).round(3).to_string())
    cols = ["sido", "sgg_name", "km", "share_km", "km_per_km2", "trips", "users", "top_user_share", "area_km2"]
    print("\n시군구 상위 25 (관측 주행거리 순)")
    print(res[cols].head(25).round(3).to_string(index=False))
    print(f"\n[region_density] 저장: {OUT}")


if __name__ == "__main__":
    main()
