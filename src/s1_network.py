"""
1단계 준비: 연구 지역 여정 전체를 덮는 도로망 만들기

처리
  1. 영역: 연구 지역 여정의 궤적(조각 사이는 직선) 주변 S1_BUFFER_M 버퍼 합집합
  2. OSM 잘라내기: 영역에 노드가 하나라도 걸친 highway 도로 전체 -> area.osm.pbf
     (Valhalla, OSRM이 각자의 자전거 프로필로 통행 가능 도로를 고른다)
  3. 노드 좌표표: area.osm.pbf의 노드 -> nodes.parquet [node_id, lon, lat, x, y]
     OSRM이 돌려주는 OSM 노드 id를 링크(노드 쌍)로 바꿀 때 쓴다.
  4. 엔진 데이터: OSRM (bicycle.lua, MLD), Valhalla 타일

입력  : s0_points.parquet, s0_journey_pieces.parquet, s0_journeys.parquet, C.OSM_PBF_PATH
출력  : data/interim/s1/area.geojson, area.osm.pbf, nodes.parquet, osrm/, valhalla_tiles/

실행
  python s1_network.py            # 있으면 건너뜀
  python s1_network.py --force
"""
import argparse
import shutil
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
import s0_join as J
import s1_bench_match as M

D = C.S1_DIR
OUT_AREA = D / "area.geojson"
OUT_PBF = D / "area.osm.pbf"
OUT_NODES = D / "nodes.parquet"
OSRM_DIR = D / "osrm"
VALHALLA_TILES = D / "valhalla_tiles"


def journey_points():
    """연구 지역 여정의 점: [journey_id, trip_id, piece_no, ts, lon, lat, x, y] (여정·시간순)"""
    pts = pd.read_parquet(S0.OUT_POINTS, columns=["trip_id", "ts", "lon", "lat", "x", "y"])
    pieces = pd.read_parquet(J.OUT_PIECES, columns=["trip_id", "journey_id", "piece_no"])
    jr = pd.read_parquet(J.OUT_JOURNEYS, columns=["journey_id", "in_study_region"])
    keep = set(jr.journey_id[jr.in_study_region])
    p = pts.merge(pieces, on="trip_id")
    return p[p.journey_id.isin(keep)].sort_values(["journey_id", "ts"], kind="mergesort").reset_index(drop=True)


def build_area(jp):
    """여정 궤적(조각 사이 직선 포함) 주변 버퍼 합집합 (EPSG:4326)."""
    lines = [shapely.LineString(g[["x", "y"]].to_numpy()) for _, g in jp.groupby("journey_id", sort=False)]
    area = shapely.union_all(shapely.buffer(np.array(lines, dtype=object), C.S1_BUFFER_M))
    tf = Transformer.from_crs(C.CRS_WORK, C.CRS_SRC, always_xy=True)
    return shapely.ops.transform(tf.transform, area), area.area / 1e6


def clip_osm(area, out_pbf):
    """전체 PBF에서 영역에 노드가 하나라도 걸친 highway 도로를 잘라낸다 (s1_bench_prep.clip_osm과 같은 규칙)."""
    shapely.prepare(area)
    minx, miny, maxx, maxy = area.bounds
    n = 0
    with osmium.BackReferenceWriter(str(out_pbf), ref_src=str(C.OSM_PBF_PATH), overwrite=True) as w:
        fp = (osmium.FileProcessor(str(C.OSM_PBF_PATH), osmium.osm.NODE | osmium.osm.WAY)
              .with_locations()
              .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY))
              .with_filter(osmium.filter.KeyFilter("highway")))
        for way in fp:
            lon = np.fromiter((nd.lon for nd in way.nodes if nd.location.valid()), float)
            lat = np.fromiter((nd.lat for nd in way.nodes if nd.location.valid()), float)
            if len(lon) == 0 or lon.max() < minx or lon.min() > maxx or lat.max() < miny or lat.min() > maxy:
                continue
            if shapely.contains_xy(area, lon, lat).any():
                w.add_way(way)
                n += 1
    return n


def node_table(pbf):
    """잘라낸 PBF의 모든 노드 좌표 (EPSG:4326 -> 5179)."""
    ids, lon, lat = [], [], []
    for nd in osmium.FileProcessor(str(pbf), osmium.osm.NODE):
        ids.append(nd.id)
        lon.append(nd.location.lon)
        lat.append(nd.location.lat)
    x, y = Transformer.from_crs(C.CRS_SRC, C.CRS_WORK, always_xy=True).transform(lon, lat)
    return pd.DataFrame({"node_id": np.array(ids, np.int64), "lon": lon, "lat": lat, "x": x, "y": y})


def main(force=False):
    C.setup_environment()
    D.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    if not (OUT_PBF.exists() and OUT_NODES.exists()) or force:
        jp = journey_points()
        area, km2 = build_area(jp)
        gpd.GeoSeries([area], crs=C.CRS_SRC).to_file(OUT_AREA, driver="GeoJSON")
        print(f"[s1_network] 여정 {jp.journey_id.nunique()}개, 도로망 영역 {km2:.0f} km² (버퍼 {C.S1_BUFFER_M}m)")
        n = clip_osm(area, OUT_PBF)
        print(f"[s1_network] OSM 잘라내기: highway {n:,}개 ({time.time() - t0:.0f}초)")
        nodes = node_table(OUT_PBF)
        nodes.to_parquet(OUT_NODES, index=False)
        print(f"[s1_network] 노드 {len(nodes):,}개 ({time.time() - t0:.0f}초)")
    if not (OSRM_DIR / "area.osrm.mldgr").exists() or force:
        OSRM_DIR.mkdir(exist_ok=True)
        shutil.copy(OUT_PBF, OSRM_DIR / "area.osm.pbf")
        M.OsrmMatcher.build(OSRM_DIR, "area.osm.pbf")
        print(f"[s1_network] OSRM 데이터 ({time.time() - t0:.0f}초)")
    if not VALHALLA_TILES.exists() or force:
        if VALHALLA_TILES.exists():
            shutil.rmtree(VALHALLA_TILES)
        M.ValhallaMatcher.build_tiles(OUT_PBF, VALHALLA_TILES)
        print(f"[s1_network] Valhalla 타일 ({time.time() - t0:.0f}초)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    main(force=ap.parse_args().force)
