"""
1단계 준비: 맵매칭 라이브러리 어댑터

모든 라이브러리를 같은 입력·출력으로 감싼다.
  입력 : 통행 하나의 포인트 DataFrame [ts(ms), lon, lat, x, y]  (시간순, EPSG:5179 x/y 포함)
  출력 : MatchOut(geom, n_parts, matched_frac, runtime_s, error)
           geom         : 매칭 경로 (shapely LineString/MultiLineString, EPSG:5179). 실패면 None
           n_parts      : 경로가 끊겨 여러 조각으로 나온 수 (1이면 한 줄로 이어짐)
           matched_frac : 매칭에 쓰인 입력 점 비율 (중간에 포기한 라이브러리 진단용)

라이브러리와 공통 파라미터 대응 (S1B_GPS_SIGMA_M = σ, S1B_SEARCH_RADIUS_M = R)
  Valhalla Meili (pyvalhalla, 네이티브) : gps_accuracy=σ, search_radius=R, costing=bicycle
  OSRM match (Docker)                   : radiuses=σ (OSRM은 탐색 반경을 σ에서 스스로 정한다), bicycle.lua
  FMM (Docker, 일괄 실행)               : gps_error=σ, radius=R, k=8. 시간 정보를 쓰지 않는다
  leuvenmapmatching (순수 파이썬)       : obs_noise=σ, max_dist=R, 비방출 상태 허용
  mappymatch LCSS (순수 파이썬)         : distance_epsilon=R. 시간 정보를 쓰지 않는다
Valhalla와 OSRM은 각자의 자전거 프로필로 도로를 고르고,
FMM·leuven·mappymatch는 s1_bench_prep이 만든 같은 그래프(osmnx 'bike' 규칙, 양방향)를 쓴다.
"""
import json
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np
import shapely
from pyproj import Transformer

import config as C

TO_5179 = Transformer.from_crs(C.CRS_SRC, C.CRS_WORK, always_xy=True)
DOCKER = "docker"


@dataclass
class MatchOut:
    geom: Optional[object]
    n_parts: int
    matched_frac: float
    runtime_s: float
    error: str = ""


def _lonlat_lines_to_5179(lines):
    """[(lon, lat) 좌표 리스트, ...] -> EPSG:5179 LineString 또는 MultiLineString (점 2개 미만 조각은 버림)"""
    parts = []
    for coords in lines:
        c = np.asarray(coords, dtype=float)
        if len(c) < 2:
            continue
        x, y = TO_5179.transform(c[:, 0], c[:, 1])
        parts.append(shapely.LineString(np.column_stack([x, y])))
    if not parts:
        return None, 0
    return (parts[0] if len(parts) == 1 else shapely.MultiLineString(parts)), len(parts)


def decode_polyline6(s):
    """Valhalla 인코딩 폴리라인(정밀도 1e-6)을 [(lon, lat), ...]로 푼다."""
    coords, idx, lat, lon = [], 0, 0, 0
    while idx < len(s):
        for which in (0, 1):
            shift = result = 0
            while True:
                b = ord(s[idx]) - 63
                idx += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            d = ~(result >> 1) if result & 1 else result >> 1
            if which == 0:
                lat += d
            else:
                lon += d
        coords.append((lon / 1e6, lat / 1e6))
    return coords


# ===========================================================================
# Valhalla (pyvalhalla)
# ===========================================================================
class ValhallaMatcher:
    """pyvalhalla Actor로 trace_route를 호출한다. 타일은 build_tiles()로 한 번 만든다."""

    def __init__(self, tile_dir):
        from valhalla import Actor, get_config
        self.config = get_config(tile_extract="", tile_dir=str(tile_dir))
        self.actor = Actor(self.config)

    @staticmethod
    def build_tiles(pbf, tile_dir):
        """
        PBF로 Valhalla 타일을 만든다 (valhalla_build_tiles 실행 파일은 pyvalhalla에 들어 있다).
        반환: 설정 파일 경로
        """
        from valhalla import get_config
        tile_dir.mkdir(parents=True, exist_ok=True)
        cfg = get_config(tile_extract="", tile_dir=str(tile_dir))
        cfg_path = tile_dir.parent / "valhalla.json"
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        # 파이썬 Scripts 폴더가 PATH에 없어도 돌도록 그 폴더를 PATH 앞에 붙인다
        # (pyvalhalla의 실행 래퍼가 PATH에서 자기 이름을 다시 찾기 때문에 전체 경로 호출만으로는 부족하다)
        import os
        import sys
        from pathlib import Path
        scripts = Path(sys.executable).parent / "Scripts"
        env = {**os.environ, "PATH": str(scripts) + os.pathsep + os.environ.get("PATH", "")}
        subprocess.run([str(scripts / "valhalla_build_tiles"), "-c", str(cfg_path), str(pbf)], check=True, env=env,
                       capture_output=True, text=True)
        return cfg_path

    def match(self, pts):
        t = time.perf_counter()
        req = {
            "shape": [{"lat": float(a), "lon": float(o), "time": float(s)}
                      for a, o, s in zip(pts.lat, pts.lon, pts.ts / 1000.0)],
            "costing": "bicycle",
            "shape_match": "map_snap",
            "use_timestamps": True,
            "trace_options": {"gps_accuracy": C.S1B_GPS_SIGMA_M, "search_radius": C.S1B_SEARCH_RADIUS_M},
        }
        try:
            res = json.loads(self.actor.trace_route(json.dumps(req)))
        except Exception as e:  # Valhalla는 매칭 불가를 예외로 알린다
            return MatchOut(None, 0, 0.0, time.perf_counter() - t, str(e)[:200])
        # 매칭이 끊기면 첫 조각만 'trip'에, 나머지 조각은 'alternates'에 담겨 온다. 모두 모은다.
        trips = [res["trip"]] + [a["trip"] for a in res.get("alternates", [])]
        geom, n = _lonlat_lines_to_5179([decode_polyline6(leg["shape"]) for tr in trips for leg in tr["legs"]])
        frac = self._matched_frac(req)
        return MatchOut(geom, n, frac, time.perf_counter() - t)

    def _matched_frac(self, req):
        """trace_attributes로 점별 매칭 결과를 받아 'unmatched'가 아닌 점의 비율을 구한다."""
        try:
            ta = json.loads(self.actor.trace_attributes(json.dumps(
                {**req, "filters": {"attributes": ["matched.type"], "action": "include"}})))
            return float(np.mean([m["type"] != "unmatched" for m in ta["matched_points"]]))
        except Exception:
            return float("nan")


# ===========================================================================
# OSRM (Docker)
# ===========================================================================
class OsrmMatcher:
    """osrm-routed 컨테이너의 /match API를 호출한다."""

    CONTAINER = "bike-osrm"

    @staticmethod
    def build(osrm_dir, pbf_name):
        """
        bicycle.lua 프로필로 extract -> partition -> customize (MLD).
        osrm_dir: PBF가 들어 있는 폴더 (컨테이너 /data로 마운트)
        """
        vol = f"{osrm_dir.as_posix()}:/data"
        base = pbf_name.replace(".osm.pbf", ".osrm")
        for cmd in (["osrm-extract", "-p", "/opt/bicycle.lua", f"/data/{pbf_name}"],
                    ["osrm-partition", f"/data/{base}"],
                    ["osrm-customize", f"/data/{base}"]):
            subprocess.run([DOCKER, "run", "--rm", "-v", vol, C.OSRM_IMAGE, *cmd], check=True,
                           capture_output=True, text=True)

    @classmethod
    def start(cls, osrm_dir, pbf_name):
        """서버 컨테이너를 띄운다. 매칭 점 수 제한(기본 100)을 넉넉히 올린다."""
        subprocess.run([DOCKER, "rm", "-f", cls.CONTAINER], capture_output=True)
        base = pbf_name.replace(".osm.pbf", ".osrm")
        subprocess.run([DOCKER, "run", "-d", "--name", cls.CONTAINER, "-p", f"{C.OSRM_PORT}:5000",
                        "-v", f"{osrm_dir.as_posix()}:/data", C.OSRM_IMAGE,
                        "osrm-routed", "--algorithm", "mld", "--max-matching-size", "5000", f"/data/{base}"],
                       check=True, capture_output=True, text=True)
        import requests
        for _ in range(60):
            try:
                requests.get(f"http://localhost:{C.OSRM_PORT}/", timeout=1)
                return
            except requests.RequestException:
                time.sleep(1)
        raise RuntimeError("OSRM 서버가 뜨지 않았습니다")

    @classmethod
    def stop(cls):
        subprocess.run([DOCKER, "rm", "-f", cls.CONTAINER], capture_output=True)

    def match(self, pts):
        import requests
        t = time.perf_counter()
        coords = ";".join(f"{o:.7f},{a:.7f}" for o, a in zip(pts.lon, pts.lat))
        params = {
            "timestamps": ";".join(str(int(s)) for s in pts.ts // 1000),
            "radiuses": ";".join([str(C.S1B_GPS_SIGMA_M)] * len(pts)),
            "geometries": "geojson", "overview": "full",
            # 기본값 gaps=split은 60초 넘는 공백에서 매칭을 끊는다. 61초 결측 블록을 잇는 능력을 보려면 ignore.
            "gaps": "ignore", "tidy": "false",
        }
        try:
            res = requests.get(f"http://localhost:{C.OSRM_PORT}/match/v1/bike/{coords}", params=params, timeout=60).json()
        except Exception as e:
            return MatchOut(None, 0, 0.0, time.perf_counter() - t, str(e)[:200])
        if res.get("code") != "Ok":
            return MatchOut(None, 0, 0.0, time.perf_counter() - t, res.get("code", "?"))
        geom, n = _lonlat_lines_to_5179([m["geometry"]["coordinates"] for m in res["matchings"]])
        frac = float(np.mean([tp is not None for tp in res["tracepoints"]]))
        return MatchOut(geom, n, frac, time.perf_counter() - t)


# ===========================================================================
# leuvenmapmatching
# ===========================================================================
class LeuvenMatcher:
    """
    그래프(EPSG:5179, 단순화 안 함)로 InMemMap을 만들고 DistanceMatcher로 매칭한다.
    간선이 직선이므로 매칭된 노드 순서를 이으면 경로 모양이 된다.
    """

    def __init__(self, graph):
        from leuvenmapmatching.map.inmem import InMemMap
        self.g = graph
        m = InMemMap("bench", use_latlon=False, use_rtree=True, index_edges=True)
        for n, d in graph.nodes(data=True):
            m.add_node(n, (d["y"], d["x"]))            # leuven은 (y, x) 순서
        for u, v in graph.edges():
            m.add_edge(u, v)
        self.map = m

    def match(self, pts):
        """
        leuven은 도로에서 max_dist보다 먼 점을 만나면 거기서 매칭을 멈춘다 (끊고 다시 시작하지 않는다).
        Valhalla·OSRM은 같은 상황에서 끊고 이어가므로, 공정하게 비교하려고
        멈춘 지점 다음 점부터 다시 매칭해 조각을 모은다. 시작점조차 후보가 없으면 그 점을 건너뛴다.
        """
        from leuvenmapmatching.matcher.distance import DistanceMatcher
        t = time.perf_counter()
        path = list(zip(pts.y.to_numpy(), pts.x.to_numpy()))
        parts, n_used, start = [], 0, 0
        while start < len(path) - 1:
            mm = DistanceMatcher(self.map, max_dist=C.S1B_SEARCH_RADIUS_M, max_dist_init=C.S1B_SEARCH_RADIUS_M,
                                 obs_noise=C.S1B_GPS_SIGMA_M, obs_noise_ne=C.S1B_GPS_SIGMA_M * 2,
                                 non_emitting_states=True, only_edges=True)
            try:
                _, last = mm.match(path[start:])
                nodes = mm.path_pred_onlynodes
            except Exception:
                last, nodes = -1, []
            if last < 1 or len(nodes) < 2:      # 진전이 없으면 한 점 건너뛰고 다시
                start += 1
                continue
            parts.append([(self.g.nodes[n]["x"], self.g.nodes[n]["y"]) for n in nodes])
            n_used += last + 1
            start += last + 1
        if not parts:
            return MatchOut(None, 0, 0.0, time.perf_counter() - t, "매칭된 조각 없음")
        geom = shapely.LineString(parts[0]) if len(parts) == 1 else shapely.MultiLineString(parts)
        return MatchOut(geom, len(parts), n_used / len(path), time.perf_counter() - t)


# ===========================================================================
# mappymatch LCSS
# ===========================================================================
class MappyMatcher:
    """
    mappymatch의 LCSS 매처. 그래프는 EPSG:5179 그대로 넘긴다.
    기본 parse_osmnx_graph는 3857로 투영하고 가장 큰 연결요소만 남기는데,
    표본 영역은 서로 떨어진 버퍼들이라 연결요소가 여럿이므로 그 필터를 끈다.
    """

    def __init__(self, graph):
        from mappymatch.maps.nx.nx_map import NxMap
        from mappymatch.maps.nx.readers.osm_readers import NetworkType, parse_osmnx_graph
        from pyproj import CRS
        # parse_osmnx_graph는 xy=False일 때 입력 그래프를 제자리에서 바꾼다 (다른 매처와 공유하므로 복사본을 넘김)
        g = parse_osmnx_graph(graph.copy(), NetworkType.BIKE, xy=False, filter_to_largest_component=False)
        g.graph["crs"] = CRS(C.CRS_WORK)
        self.map = NxMap(g)

    def match(self, pts):
        import pandas as pd
        from mappymatch.constructs.trace import Trace
        from mappymatch.matchers.lcss.lcss import LCSSMatcher
        from pyproj import CRS
        t = time.perf_counter()
        try:
            tr = Trace.from_dataframe(pd.DataFrame({"latitude": pts.lat.to_numpy(), "longitude": pts.lon.to_numpy()}),
                                      xy=False).to_crs(CRS(C.CRS_WORK))
            res = LCSSMatcher(self.map, distance_epsilon=C.S1B_SEARCH_RADIUS_M).match_trace(tr)
        except Exception as e:
            return MatchOut(None, 0, 0.0, time.perf_counter() - t, str(e)[:200])
        if not res.path:
            return MatchOut(None, 0, 0.0, time.perf_counter() - t, "경로 없음")
        # 도로를 경로 순서대로 이어 붙인다. 같은 도로를 되밟는 경우도 그대로 둔다 (길이 지표에 반영되도록).
        # 앞 도로 끝과 다음 도로 시작이 1m 넘게 떨어진 곳에서만 조각을 나눈다.
        parts, cur = [], list(res.path[0].geom.coords)
        for r in res.path[1:]:
            c = list(r.geom.coords)
            if np.hypot(c[0][0] - cur[-1][0], c[0][1] - cur[-1][1]) > 1.0:
                parts.append(cur)
                cur = c
            else:
                cur += c[1:]
        parts.append(cur)
        geom = shapely.LineString(parts[0]) if len(parts) == 1 else shapely.MultiLineString(parts)
        frac = float(np.mean([m.road is not None for m in res.matches]))
        return MatchOut(geom, len(parts), frac, time.perf_counter() - t)
