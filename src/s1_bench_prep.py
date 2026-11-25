"""
1단계 준비: 맵매칭 라이브러리 비교용 표본과 도로망 만들기

처리
  1. 표본: 유지된 통행을 '세션 안 공백 있음/없음' 두 층으로 나눠 층마다 S1B_N_PER_STRATUM개 (시드 고정)
  2. 솎아낸 입력: 표본 통행을 S1B_RESAMPLE_S 간격(초)으로 솎는다 (1초 = 원본 그대로)
  3. 도로망 영역: 표본 포인트 주변 S1B_BUFFER_M 버퍼의 합집합
  4. OSM 잘라내기 (한 스냅샷에서 두 가지 형태로)
       area_all.osm.pbf  : 영역에 걸친 모든 highway 도로 -> Valhalla, OSRM (각자 자전거 프로필 적용)
       area_bike.osm     : 자전거 통행 가능 도로만 (osmnx 'bike' 규칙) -> osmnx 그래프
     도로 일부만 영역에 걸쳐도 도로 전체를 가져온다 (BackReferenceWriter가 참조 노드를 채운다).
  5. 그래프: area_bike.osm -> osmnx 그래프 (단순화 안 함: 간선이 직선이어야 leuven 경로 모양이 정확)
             -> EPSG:5179 투영 -> graphml 저장, FMM용 간선 shapefile 저장

입력  : data/interim/s0_points.parquet, s0_trips.parquet, C.OSM_PBF_PATH
출력  : data/interim/s1_bench/
          sample_trips.parquet   표본 통행 목록과 층
          sample_points.parquet  [trip_id, resample_s, ts, lon, lat, x, y]
          area.geojson           도로망 영역 (EPSG:4326)
          area_all.osm.pbf, area_bike.osm
          graph_5179.graphml     그래프 기반 라이브러리 공통 도로망
          fmm/edges.shp          FMM 도로망 (id, source, target; EPSG:5179)

실행
  python s1_bench_prep.py
"""
import time

import geopandas as gpd
import numpy as np
import osmium
import pandas as pd
import shapely
import shapely.ops
from pyproj import Transformer

import config as C
import s0_extract as S0

D = C.S1B_DIR
OUT_TRIPS = D / "sample_trips.parquet"
OUT_POINTS = D / "sample_points.parquet"
OUT_AREA = D / "area.geojson"
OUT_PBF_ALL = D / "area_all.osm.pbf"
OUT_OSM_BIKE = D / "area_bike.osm"
OUT_GRAPH = D / "graph_5179.graphml"
OUT_FMM_EDGES = D / "fmm" / "edges.shp"


# ---------------------------------------------------------------------------
# 1-2. 표본과 솎아낸 입력
# ---------------------------------------------------------------------------
def sample_trips(points, trips, rng):
    """
    유지된 통행을 공백 유무로 층을 나눠 층마다 같은 수를 뽑는다.

    반환: DataFrame [trip_id, stratum('gap'|'nogap'), max_dt_s, n_points, dist_m]
    """
    p = points.sort_values(["trip_id", "ts"])
    max_dt = p.groupby("trip_id").ts.apply(lambda s: np.diff(s.to_numpy()).max(initial=0) / 1000)
    t = trips[trips.keep].set_index("trip_id")[["n_points", "dist_m"]].join(max_dt.rename("max_dt_s"))
    t["stratum"] = np.where(t.max_dt_s > C.S1B_GAP_MIN_S, "gap", "nogap")
    picked = []
    for name in ["gap", "nogap"]:
        ids = np.sort(t.index[t.stratum == name].to_numpy())
        picked += list(rng.choice(ids, size=min(C.S1B_N_PER_STRATUM, len(ids)), replace=False))
    return t.loc[picked].reset_index()


def resample(ts, step_s):
    """
    시간순 ts(ms)에서 직전에 남긴 점보다 step_s초 이상 지난 점만 남긴다. 첫 점과 마지막 점은 항상 남긴다.
    반환: 남길 점이면 True인 bool 배열
    """
    keep = np.zeros(len(ts), dtype=bool)
    last = None
    for i, t in enumerate(ts):
        if last is None or t - last >= step_s * 1000:
            keep[i] = True
            last = t
    keep[-1] = True
    return keep


def build_sample_points(points, sample):
    """표본 통행의 포인트를 S1B_RESAMPLE_S 간격별로 쌓는다. 반환: [trip_id, resample_s, ts, lon, lat, x, y]"""
    p = points[points.trip_id.isin(sample.trip_id)].sort_values(["trip_id", "ts"])
    out = []
    for step in C.S1B_RESAMPLE_S:
        for _, g in p.groupby("trip_id", sort=False):
            k = resample(g.ts.to_numpy(), step)
            out.append(g[k].assign(resample_s=step))
    cols = ["trip_id", "resample_s", "ts", "lon", "lat", "x", "y"]
    return pd.concat(out, ignore_index=True)[cols]


# ---------------------------------------------------------------------------
# 3-4. 영역과 OSM 잘라내기
# ---------------------------------------------------------------------------
def build_area(sample_points):
    """표본 포인트(1초) 주변 S1B_BUFFER_M 버퍼 합집합. 반환: shapely Polygon/MultiPolygon (EPSG:4326)"""
    p = sample_points[sample_points.resample_s == 1]
    area_5179 = shapely.union_all(shapely.buffer(shapely.points(p.x.to_numpy(), p.y.to_numpy()), C.S1B_BUFFER_M))
    tf = Transformer.from_crs(C.CRS_WORK, C.CRS_SRC, always_xy=True)
    return shapely.ops.transform(tf.transform, area_5179)


def is_bikeable(tags):
    """osmnx 'bike' 필터와 같은 규칙으로 자전거 통행 가능 도로인지 판정한다. 입력: osmium 태그"""
    hw = tags.get("highway")
    return (hw is not None and hw not in C.BIKE_EXCLUDE_HIGHWAY
            and tags.get("area") != "yes" and tags.get("bicycle") != "no"
            and tags.get("service") != "private")


def clip_osm(area):
    """
    전체 PBF에서 영역에 노드가 하나라도 걸친 highway 도로를 두 파일로 잘라낸다.
    반환: (모든 도로 수, 자전거 도로 수)
    """
    shapely.prepare(area)   # 반복 포함 판정을 빠르게
    minx, miny, maxx, maxy = area.bounds
    n_all = n_bike = 0
    with osmium.BackReferenceWriter(str(OUT_PBF_ALL), ref_src=str(C.OSM_PBF_PATH), overwrite=True) as w_all, \
            osmium.BackReferenceWriter(str(OUT_OSM_BIKE), ref_src=str(C.OSM_PBF_PATH), overwrite=True) as w_bike:
        # 웨이의 노드 좌표를 얻으려면 노드도 읽어 위치 캐시를 채워야 한다. 노드 자체는 건너뛴다.
        fp = (osmium.FileProcessor(str(C.OSM_PBF_PATH), osmium.osm.NODE | osmium.osm.WAY)
              .with_locations()
              .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY))
              .with_filter(osmium.filter.KeyFilter("highway")))
        for way in fp:
            lon = np.fromiter((n.lon for n in way.nodes if n.location.valid()), float)
            lat = np.fromiter((n.lat for n in way.nodes if n.location.valid()), float)
            if len(lon) == 0:
                continue
            # 빠른 사전 판정: 영역 bbox와 겹치지 않으면 건너뜀
            if lon.max() < minx or lon.min() > maxx or lat.max() < miny or lat.min() > maxy:
                continue
            if not shapely.contains_xy(area, lon, lat).any():
                continue
            w_all.add_way(way)
            n_all += 1
            if is_bikeable(way.tags):
                w_bike.add_way(way)
                n_bike += 1
    return n_all, n_bike


# ---------------------------------------------------------------------------
# 5. 그래프
# ---------------------------------------------------------------------------
def build_graph():
    """
    area_bike.osm으로 osmnx 그래프를 만들어 EPSG:5179로 투영하고 저장한다.
    FMM용 간선 shapefile도 함께 저장한다 (노드·간선 id를 0부터 다시 매김: FMM은 작은 정수 id가 안전).
    반환: (노드 수, 간선 수)
    """
    import osmnx as ox

    g = ox.graph_from_xml(OUT_OSM_BIKE, bidirectional=C.S1B_BIDIRECTIONAL, simplify=False, retain_all=True)
    g = ox.project_graph(g, to_crs=C.CRS_WORK)
    ox.save_graphml(g, OUT_GRAPH)

    # FMM에는 단순화한 그래프를 넘긴다. 차수 2인 중간 노드만 합치므로 간선 모양과 연결 구조는 같고,
    # 노드 수가 줄어 UBODT(노드 쌍별 최단경로 표) 크기가 크게 준다.
    nodes, edges = ox.graph_to_gdfs(ox.simplify_graph(g), nodes=True, edges=True)
    nid = {n: i for i, n in enumerate(nodes.index)}
    e = edges.reset_index()[["u", "v", "geometry"]]
    fmm = gpd.GeoDataFrame({
        "id": np.arange(len(e), dtype=np.int64),
        "source": e.u.map(nid).astype(np.int64),
        "target": e.v.map(nid).astype(np.int64),
    }, geometry=e.geometry.to_numpy(), crs=C.CRS_WORK)
    OUT_FMM_EDGES.parent.mkdir(parents=True, exist_ok=True)
    fmm.to_file(OUT_FMM_EDGES)
    return g.number_of_nodes(), g.number_of_edges()


def main():
    C.setup_environment()
    D.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    points = pd.read_parquet(S0.OUT_POINTS)
    trips = pd.read_parquet(S0.OUT_TRIPS)

    sample = sample_trips(points, trips, np.random.default_rng(C.S1B_SEED))
    sample.to_parquet(OUT_TRIPS, index=False)
    sp = build_sample_points(points, sample)
    sp.to_parquet(OUT_POINTS, index=False)
    print(f"[s1_prep] 표본 {len(sample)}개 {sample.stratum.value_counts().to_dict()}, "
          f"간격별 포인트 {sp.groupby('resample_s').size().to_dict()}")

    area = build_area(sp)
    gpd.GeoSeries([area], crs=C.CRS_SRC).to_file(OUT_AREA, driver="GeoJSON")
    area_km2 = gpd.GeoSeries([area], crs=C.CRS_SRC).to_crs(C.CRS_WORK).area.iloc[0] / 1e6
    print(f"[s1_prep] 도로망 영역 {area_km2:.1f} km² (버퍼 {C.S1B_BUFFER_M}m)")

    n_all, n_bike = clip_osm(area)
    print(f"[s1_prep] OSM 잘라내기: highway {n_all:,}개, 자전거 가능 {n_bike:,}개 ({time.time() - t0:.0f}초)")

    n_nodes, n_edges = build_graph()
    print(f"[s1_prep] 그래프: 노드 {n_nodes:,}, 간선 {n_edges:,} -> {OUT_GRAPH.name}, {OUT_FMM_EDGES.name} "
          f"({time.time() - t0:.0f}초)")


if __name__ == "__main__":
    main()
