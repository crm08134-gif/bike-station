"""
1단계: 맵매칭 본 실행 (여정 -> 링크 시퀀스)

방식
  여정 = 0단계 통행 조각들 (각 조각은 1초 관측, 세션 안 61초 구멍만 있음), 조각 사이 공백은 최대 JOIN_MAX_GAP_S.
  1. 조각마다 맵매칭한다. 매칭이 도로망에 없는 구간 등으로 끊기면 여러 조각(segment)이 나온다.
  2. 연속한 두 segment 사이를 잇는다 (bridge).
       inner bridge: 한 조각 안에서 매칭이 끊긴 곳 (보통 수십 m). 엔진 도로망에 없는 길(공원길 등)을 지난 곳이다.
                     GPS로 관측된 구간이므로 관측 궤적 그대로 잇고 링크는 넣지 않는다.
                     (최단경로로 이으면 도로망에 없는 30m를 돌아가느라 수 km 우회로가 생긴다:
                      2차 비교 첫 실행에서 OSRM 우회율 p90 65배, 최대 367배)
                     Valhalla는 매칭 토막의 시각을 주지 않아 직선으로 잇는다.
       gap bridge  : 조각 사이 공백 (5~15분, 관측이 없는 구간). 엔진의 자전거 최단경로로 잇는다.
     공백 구간 경로를 '최단경로로 가정'하는 것을 명시적 규칙으로 둔다. 실제 경로는 알 수 없다.
  3. 경로 좌표 중 OSM 노드 위에 놓인 점(S1_NODE_TOL_M 이내)을 순서대로 노드 id로 바꾼다.
     이웃한 연속 노드 쌍 = 링크 (방향 없음: (작은 id, 큰 id)). 링크 모양은 두 노드를 잇는 직선
     (OSM 웨이는 노드 사이가 직선이므로 정확하다). OSRM 경로로만 검증했다.

입력  : s1_network 산출물 (OSRM 데이터, 노드 좌표), s0 여정·조각·포인트
출력  : data/interim/s1_routes.parquet  (C.S1_ROUTES_PATH) 여정 1행: trip_id(=journey_id), link_seq,
                                          start_x, start_y, end_x, end_y + 진단 칼럼
        data/interim/s1_links.parquet   (C.S1_LINKS_PATH)  링크 1행: link_id, u, v, length_m, geometry(WKB)
        data/interim/s1/bridges.parquet  bridge 1행: 종류, 직선거리, 경로거리, 공백 시간, 필요 속도
        data/interim/s1/s1_stats.json

엔진 어댑터 (OsrmEngine, ValhallaEngine)는 s1_compare.py의 2차 비교에서도 쓴다.
Valhalla는 OSM 노드 id를 돌려주지 않아 경로 모양만 만든다 (비교용).

실행
  python s1_match.py            # 캐시가 있으면 건너뜀
  python s1_match.py --force
"""
import argparse
import json
import time
from dataclasses import dataclass

import numpy as np
import pandas as pd
import shapely
from pyproj import Transformer

import config as C
import s1_bench_match as M
import s1_network as N

TO_5179 = Transformer.from_crs(C.CRS_SRC, C.CRS_WORK, always_xy=True)
OUT_BRIDGES = C.S1_DIR / "bridges.parquet"
OUT_STATS = C.S1_DIR / "s1_stats.json"


@dataclass
class Seg:
    """경로 한 토막: 좌표(lon, lat 목록), 첫·끝 입력점 시각(ms, OSRM 매칭만)"""
    coords: list
    t0: float = np.nan
    t1: float = np.nan


# ===========================================================================
# 엔진 어댑터
# ===========================================================================
class OsrmEngine:
    name = "osrm"

    def __init__(self):
        import requests
        self.s = requests.Session()
        self.base = f"http://localhost:{C.OSRM_PORT}"

    @staticmethod
    def start():
        M.OsrmMatcher.start(N.OSRM_DIR, "area.osm.pbf")

    @staticmethod
    def stop():
        M.OsrmMatcher.stop()

    def match(self, pts):
        """조각 하나를 매칭한다. 반환: Seg 목록 (매칭이 끊기면 여러 개), 실패면 빈 목록"""
        coords = ";".join(f"{o:.7f},{a:.7f}" for o, a in zip(pts.lon, pts.lat))
        params = {"timestamps": ";".join(str(int(s)) for s in pts.ts // 1000),
                  "radiuses": ";".join([str(C.S1B_GPS_SIGMA_M)] * len(pts)),
                  "geometries": "geojson", "overview": "full", "gaps": "ignore", "tidy": "false"}
        res = self.s.get(f"{self.base}/match/v1/bike/{coords}", params=params, timeout=120).json()
        if res.get("code") != "Ok":
            return []
        # 매칭마다 속한 입력점의 첫·끝 시각
        ts = pts.ts.to_numpy()
        span = {}
        for i, tp in enumerate(res["tracepoints"]):
            if tp is not None:
                k = tp["matchings_index"]
                a, b = span.get(k, (ts[i], ts[i]))
                span[k] = (min(a, ts[i]), max(b, ts[i]))
        out = []
        for k, m in enumerate(res["matchings"]):
            t0, t1 = span.get(k, (np.nan, np.nan))
            out.append(Seg(m["geometry"]["coordinates"], t0, t1))
        return sorted(out, key=lambda s: s.t0)

    def route(self, a, b):
        """두 점 (lon, lat) 사이 자전거 최단경로. 반환: Seg 또는 None"""
        res = self.s.get(f"{self.base}/route/v1/bike/{a[0]:.7f},{a[1]:.7f};{b[0]:.7f},{b[1]:.7f}",
                         params={"overview": "full", "geometries": "geojson"}, timeout=60).json()
        if res.get("code") != "Ok":
            return None
        return Seg(res["routes"][0]["geometry"]["coordinates"])


class ValhallaEngine:
    name = "valhalla"

    def __init__(self):
        from valhalla import Actor, get_config
        self.actor = Actor(get_config(tile_extract="", tile_dir=str(N.VALHALLA_TILES)))

    @staticmethod
    def start():
        pass

    @staticmethod
    def stop():
        pass

    def match(self, pts):
        req = {"shape": [{"lat": float(a), "lon": float(o), "time": float(s)}
                         for a, o, s in zip(pts.lat, pts.lon, pts.ts / 1000.0)],
               "costing": "bicycle", "shape_match": "map_snap", "use_timestamps": True,
               "trace_options": {"gps_accuracy": C.S1B_GPS_SIGMA_M, "search_radius": C.S1B_SEARCH_RADIUS_M}}
        try:
            res = json.loads(self.actor.trace_route(json.dumps(req)))
        except Exception:
            return []
        # 끊기면 첫 조각은 trip, 나머지는 alternates에 순서대로 담긴다
        trips = [res["trip"]] + [a["trip"] for a in res.get("alternates", [])]
        return [Seg(M.decode_polyline6(leg["shape"])) for tr in trips for leg in tr["legs"]]

    def route(self, a, b):
        req = {"locations": [{"lon": a[0], "lat": a[1]}, {"lon": b[0], "lat": b[1]}], "costing": "bicycle"}
        try:
            res = json.loads(self.actor.route(json.dumps(req)))
        except Exception:
            return None
        coords = [c for leg in res["trip"]["legs"] for c in M.decode_polyline6(leg["shape"])]
        return Seg(coords)


ENGINES = {"osrm": OsrmEngine, "valhalla": ValhallaEngine}


# ===========================================================================
# 여정 하나 매칭
# ===========================================================================
def _xy(lonlat):
    x, y = TO_5179.transform(*lonlat)
    return np.array([x, y])


def match_journey(eng, jp):
    """
    여정 하나. jp: 그 여정의 점 [trip_id, piece_no, ts, lon, lat, x, y] (시간순)
    반환: (Seg 목록 (매칭과 bridge가 순서대로), bridge 기록 목록, 실패 사유 또는 "")
    """
    parts, bridges = [], []
    for piece_no, p in jp.groupby("piece_no", sort=True):
        segs = eng.match(p)
        if not segs:
            continue                      # 이 조각은 매칭 실패: 앞뒤 조각을 gap bridge로 잇는다
        for s in segs:
            s.piece = piece_no
        parts.extend(segs)
    if not parts:
        return [], [], "no_match"
    out = [parts[0]]
    for prev, nxt in zip(parts[:-1], parts[1:]):
        a, b = prev.coords[-1], nxt.coords[0]
        chord = float(np.linalg.norm(_xy(a) - _xy(b)))
        kind = "inner" if prev.piece == nxt.piece else "gap"
        if chord >= 1.0:
            if kind == "inner":
                br = observed_filler(jp, prev, nxt)
                dt = (nxt.t0 - prev.t1) / 1000
            else:
                br = eng.route(a, b)
                if br is None:
                    return [], [], "bridge_fail"
                dt = (jp.ts[jp.piece_no == nxt.piece].min() - jp.ts[jp.piece_no == prev.piece].max()) / 1000
            geom = line_5179(br.coords)
            bridges.append({"kind": kind, "chord_m": chord, "route_m": geom.length if geom is not None else 0.0,
                            "dt_s": dt})
            out.append(br)
        out.append(nxt)
    return out, bridges, ""


def observed_filler(jp, prev, nxt):
    """
    매칭이 끊긴 곳을 관측 GPS 궤적으로 잇는다 (앞 토막 끝 -> 사이 GPS 점들 -> 뒤 토막 시작). 노드는 없다.
    토막 시각을 모르면(Valhalla) 직선.
    """
    mid = []
    if np.isfinite(prev.t1) and np.isfinite(nxt.t0):
        g = jp[(jp.ts > prev.t1) & (jp.ts < nxt.t0)]
        mid = list(zip(g.lon, g.lat))
    return Seg([prev.coords[-1], *mid, nxt.coords[0]])


def line_5179(coords):
    c = np.asarray(coords, dtype=float)
    if len(c) < 2:
        return None
    x, y = TO_5179.transform(c[:, 0], c[:, 1])
    return shapely.LineString(np.column_stack([x, y]))


def journey_geometry(segs):
    """토막 좌표를 순서대로 이은 선 (EPSG:5179)"""
    return line_5179([c for s in segs for c in s.coords])


# ===========================================================================
# 노드 -> 링크
# ===========================================================================
def way_adjacency(pbf):
    """OSM 웨이에서 이웃한 노드 쌍 집합 (방향 없음). 링크가 실제 도로 구간인지 확인용."""
    import osmium
    adj = set()
    for w in osmium.FileProcessor(str(pbf), osmium.osm.WAY):
        ids = [n.ref for n in w.nodes]
        for a, b in zip(ids[:-1], ids[1:]):
            adj.add((a, b) if a < b else (b, a))
    return adj


def nodes_from_coords(xy, tree, node_ids, tol_m):
    """
    경로 좌표(EPSG:5179)에서 OSM 노드 위에 놓인 점만 골라 노드 id 순서로 바꾼다 (연속 중복 제거).
    OSRM 경로 좌표는 지나간 노드 좌표와, 1초 입력점마다 도로 위로 옮긴 점(노드가 아님)이 섞여 있다.
    노드 id를 OSRM 응답의 annotation에서 받지 않는 이유:
      - OSRM JSON은 숫자를 유효숫자 10자리로 찍어 11자리 노드 id(2022년 이후 생긴 노드)의 끝자리가 반올림된다
      - tidy=false 매칭은 구간(leg) 경계마다 노드가 겹치거나 되돌아가게 나와 링크가 중복·누락된다
    """
    d, i = tree.query(xy)
    ids = node_ids[i[d <= tol_m]]
    if len(ids) == 0:
        return []
    keep = np.r_[True, ids[1:] != ids[:-1]]
    return ids[keep].tolist()


def neighbors(adj):
    """링크 집합 -> 노드별 이웃 노드 집합"""
    nb = {}
    for a, b in adj:
        nb.setdefault(a, set()).add(b)
        nb.setdefault(b, set()).add(a)
    return nb


def end_edge(pt, node, nb, xy, tol_m):
    """
    경로 시작(끝)점 pt가 놓인 반쪽 링크의 반대편 노드. 경로는 링크 중간에서 시작·끝나므로
    첫(마지막) 노드와 그 이웃 중 pt를 지나는 선분을 찾는다. 없으면 None.
    """
    best, best_d = None, tol_m
    a = xy[node]
    for m in nb.get(node, ()):
        b = xy[m]
        ab = b - a
        t = np.clip(np.dot(pt - a, ab) / max(np.dot(ab, ab), 1e-9), 0, 1)
        d = np.linalg.norm(pt - (a + t * ab))
        if d <= best_d and t > 0:
            best, best_d = m, d
    return best


def nodes_to_pairs(nodes, adj):
    """
    노드 목록 -> 방향 없는 링크 (작은 id, 큰 id) 목록.
    도로망에 없는 쌍(토막 경계에서 생기는 건너뜀)은 빼고 그 수를 센다.
    """
    pairs, n_jump = [], 0
    for a, b in zip(nodes[:-1], nodes[1:]):
        if a == b:
            continue
        key = (a, b) if a < b else (b, a)
        if key in adj:
            pairs.append(key)
        else:
            n_jump += 1
    return pairs, n_jump


# ===========================================================================
def run(engine_name, jp, trips, progress=True):
    """
    모든 여정을 매칭한다.
    반환: (여정별 결과 DataFrame, bridge DataFrame)
      결과 칼럼: journey_id, error, geom(5179), n_segs, n_inner, n_gap, runtime_s
    """
    eng = ENGINES[engine_name]()
    rows, brs = [], []
    groups = list(jp.groupby("journey_id", sort=True))
    t_all = time.time()
    for i, (jid, g) in enumerate(groups):
        t = time.perf_counter()
        segs, bridges, err = match_journey(eng, g)
        rows.append({"journey_id": jid, "error": err, "geom": journey_geometry(segs) if segs else None,
                     "n_segs": len(segs), "n_inner": sum(b["kind"] == "inner" for b in bridges),
                     "n_gap": sum(b["kind"] == "gap" for b in bridges), "runtime_s": time.perf_counter() - t})
        brs.extend({"journey_id": jid, **b} for b in bridges)
        if progress and (i + 1) % 100 == 0:
            print(f"  [{engine_name}] {i + 1}/{len(groups)} ({time.time() - t_all:.0f}초)", flush=True)
    return pd.DataFrame(rows), pd.DataFrame(brs, columns=["journey_id", "kind", "chord_m", "route_m", "dt_s"])


def quality(res, br, jp, trips):
    """
    엔진 결과 품질 지표 (여정별)
      len_ratio : (경로 길이 - gap bridge 길이) / 관측 조각 보정 거리 합. 1 근처가 정상
                  (gap bridge는 관측 없는 구간이라 관측 거리와 비교할 대상이 없어 뺀다)
      offpath   : 1초 GPS 점 중 경로에서 S1B_OFFPATH_M보다 먼 점 비율
    """
    obs = jp[["journey_id", "trip_id"]].drop_duplicates().merge(trips[["trip_id", "dist_m"]], on="trip_id") \
        .groupby("journey_id").dist_m.sum()
    pts = {j: g for j, g in jp.groupby("journey_id")}
    out = []
    for r in res.itertuples():
        if r.geom is None:
            out.append((np.nan, np.nan))
            continue
        g = pts[r.journey_id]
        d = shapely.distance(r.geom, shapely.points(g.x.to_numpy(), g.y.to_numpy()))
        out.append((r.geom.length, float(np.mean(d > C.S1B_OFFPATH_M))))
    q = pd.DataFrame(out, columns=["route_len_m", "offpath"], index=res.index)
    gap_len = br[br.kind == "gap"].groupby("journey_id").route_m.sum()
    res = res.assign(route_len_m=q.route_len_m, offpath=q.offpath, obs_dist_m=res.journey_id.map(obs),
                     gap_len_m=res.journey_id.map(gap_len).fillna(0.0))
    return res.assign(len_ratio=(res.route_len_m - res.gap_len_m) / res.obs_dist_m)


def build_outputs(res, adj, nodes_xy):
    """
    경로 모양 -> 노드 순서 -> 링크 순서 (s1_routes, s1_links)
    GPS로 이은 끊김 구간 좌표는 노드 위에 있지 않아 저절로 빠지고, 그 앞뒤 노드는 이웃이 아니므로 링크가 생기지 않는다.
    """
    from scipy.spatial import cKDTree
    tree = cKDTree(nodes_xy[["x", "y"]].to_numpy())
    node_ids = nodes_xy.index.to_numpy()
    nb = neighbors(adj)
    xy = dict(zip(node_ids, nodes_xy[["x", "y"]].to_numpy()))
    link_ids = {}
    rows = []
    for r in res.itertuples():
        if r.error or r.geom is None:
            continue
        c = shapely.get_coordinates(r.geom)
        nodes = nodes_from_coords(c, tree, node_ids, C.S1_NODE_TOL_M)
        if nodes:
            # 시작·끝의 반쪽 링크도 경로에 넣는다 (지나간 도로이므로)
            n0 = end_edge(c[0], nodes[0], nb, xy, C.S1_END_EDGE_TOL_M)
            n1 = end_edge(c[-1], nodes[-1], nb, xy, C.S1_END_EDGE_TOL_M)
            nodes = ([n0] if n0 is not None and (len(nodes) < 2 or n0 != nodes[1]) else []) + nodes +                     ([n1] if n1 is not None and (len(nodes) < 2 or n1 != nodes[-2]) else [])
        pairs, n_jump = nodes_to_pairs(nodes, adj)
        if not pairs:
            continue
        seq = [link_ids.setdefault(p, len(link_ids)) for p in pairs]
        c = shapely.get_coordinates(r.geom)
        rows.append({"trip_id": r.journey_id, "link_seq": seq,
                     "start_x": c[0, 0], "start_y": c[0, 1], "end_x": c[-1, 0], "end_y": c[-1, 1],
                     "n_links": len(seq), "n_jump": n_jump, "route_len_m": r.route_len_m, "len_ratio": r.len_ratio,
                     "obs_dist_m": r.obs_dist_m, "offpath": r.offpath, "n_inner": r.n_inner, "n_gap": r.n_gap})
    routes = pd.DataFrame(rows)
    uv = np.array(list(link_ids.keys()), dtype=np.int64).reshape(-1, 2)
    xy = nodes_xy.loc[uv.ravel(), ["x", "y"]].to_numpy().reshape(-1, 2, 2)
    geoms = shapely.linestrings(xy)
    links = pd.DataFrame({"link_id": np.arange(len(uv), dtype=np.int64), "u": uv[:, 0], "v": uv[:, 1],
                          "length_m": shapely.length(geoms), "geometry": shapely.to_wkb(geoms)})
    return routes, links


def main(force=False):
    C.setup_environment()
    if C.S1_ROUTES_PATH.exists() and C.S1_LINKS_PATH.exists() and OUT_STATS.exists() and not force:
        print(f"[s1] 기존 산출물이 있어 건너뜁니다 (--force 로 재계산). {OUT_STATS}")
        print(OUT_STATS.read_text(encoding="utf-8"))
        return
    if C.S1_ENGINE != "osrm":
        raise NotImplementedError("링크 시퀀스는 OSM 노드 id를 돌려주는 OSRM에서만 만든다")
    import s0_extract as S0
    t0 = time.time()
    jp = N.journey_points()
    trips = pd.read_parquet(S0.OUT_TRIPS)
    OsrmEngine.start()
    try:
        res, br = run("osrm", jp, trips)
    finally:
        OsrmEngine.stop()
    res = quality(res, br, jp, trips)
    adj = way_adjacency(N.OUT_PBF)
    nodes_xy = pd.read_parquet(N.OUT_NODES).set_index("node_id")
    routes, links = build_outputs(res, adj, nodes_xy)

    br["route_over_chord"] = br.route_m / br.chord_m
    br["need_kmh"] = br.route_m / br.dt_s * 3.6
    gap = br[br.kind == "gap"]
    q = lambda s: {f"p{p}": round(float(np.nanpercentile(s, p)), 2) for p in (10, 50, 90, 100)} if len(s) else {}
    log = {
        "engine": "osrm", "n_journeys": int(res.journey_id.nunique()),
        "errors": res.error[res.error != ""].value_counts().to_dict(),
        "n_routes": int(len(routes)), "n_links": int(len(links)),
        "n_inner_bridges": int((br.kind == "inner").sum()), "n_gap_bridges": int(len(gap)),
        "len_ratio": q(routes.len_ratio),
        "offpath": q(routes.offpath),
        "gap_bridge_route_over_chord": q(gap.route_over_chord),
        "gap_bridge_need_kmh": q(gap.need_kmh),
        "n_jump_pairs": int(routes.n_jump.sum()),
        "n_journeys_no_links": int(res.journey_id.nunique() - len(routes)),
        "elapsed_s": round(time.time() - t0, 1),
    }
    out = routes.copy()
    out.to_parquet(C.S1_ROUTES_PATH, index=False)
    links.to_parquet(C.S1_LINKS_PATH, index=False)
    br.to_parquet(OUT_BRIDGES, index=False)
    OUT_STATS.write_text(json.dumps(log, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(log, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    main(force=ap.parse_args().force)
