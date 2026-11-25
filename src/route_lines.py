"""
대표경로 선 만들기 (3단계 후보지, 4단계 접근점 표 공통)

1단계는 매칭 선 모양을 저장하지 않고 링크 시퀀스(link_seq)만 남긴다. 여기서 링크를 순서대로 이어
방향이 있는 LineString 하나로 만든다. 3단계와 4단계가 같은 선을 써야 3단계 후보지 반경 필터와
4단계 접근점 표가 어긋나지 않는다 (2026-09-28 결정: 선 끝 자르기를 3단계에도 적용).

  - 링크 = 경로가 지나간 이웃한 OSM 노드 쌍을 잇는 직선. 방향 없이 저장돼 있으므로(u < v)
    앞뒤 링크와 공유하는 노드를 보고 방향을 정한다.
  - 연속 링크가 노드를 공유하지 않는 곳(1단계에서 매칭이 끊겨 관측 궤적으로 이은 곳, 도로에 없는 노드 쌍)은
    직선으로 잇는다. 1단계는 그 구간의 모양을 저장하지 않기 때문이다.
  - 선 끝 자르기 (ROUTE_TRIM_TO_ENDPOINTS): 1단계는 경로 시작·끝의 반쪽 링크를 링크 전체로 넣으므로
    선이 실제 시종점보다 더 나가 있다. 2단계 대표 시점·종점에서 자른다.

입력  : data/interim/s2_routes.parquet (route_id, link_seq, weight, start_x, start_y, end_x, end_y)
        data/interim/s1_links.parquet  (link_id, geometry)
"""
import numpy as np
import pandas as pd
import shapely
import shapely.ops

import config as C

S2_ROUTES = C.DATA_INTERIM / "s2_routes.parquet"
ROUTE_COLUMNS = ["route_id", "link_seq", "weight", "start_x", "start_y", "end_x", "end_y"]


def _node_key(xy):
    """노드 좌표 -> 비교용 키. 링크 모양은 같은 노드 좌표 표에서 만들어졌으므로 1mm 반올림이면 같은 노드는 같은 키."""
    return (round(float(xy[0]), 3), round(float(xy[1]), 3))


def link_ends(links):
    """링크 id -> (시작 노드 좌표, 끝 노드 좌표). 저장된 방향(u -> v) 그대로."""
    geoms = shapely.from_wkb(links.geometry.to_numpy())
    out = {}
    for lid, g in zip(links.link_id.astype(np.int64), geoms):
        c = shapely.get_coordinates(g)
        out[int(lid)] = (c[0], c[-1])
    return out


def ordered_line(seq, ends, start_xy=None):
    """
    링크 시퀀스 -> 방향 있는 LineString.
    반환: (LineString, 끊긴 곳 수, 끊긴 곳을 이은 직선 길이 합 m)

    링크 i의 시작 노드 정하기
      ① 앞 링크의 끝 노드를 공유하면 거기서 시작 (정상적으로 이어진 경우)
      ② 아니면 다음 링크와 공유하는 노드 하나가 있으면 그쪽이 끝
      ③ 둘 다 아니면 기준점(앞 링크 끝, 첫 링크면 start_xy)에 가까운 노드에서 시작
    """
    n = len(seq)
    pts = []
    n_breaks, break_m = 0, 0.0
    prev_end = None
    for i, lid in enumerate(seq):
        p, q = ends[int(lid)]
        kp, kq = _node_key(p), _node_key(q)
        if prev_end is not None and _node_key(prev_end) in (kp, kq):
            s = p if _node_key(prev_end) == kp else q
        else:
            shared = set()
            if i + 1 < n:
                np_, nq = ends[int(seq[i + 1])]
                shared = {kp, kq} & {_node_key(np_), _node_key(nq)}
            if len(shared) == 1:
                s = q if kq not in shared else p          # 공유 노드가 끝이 되도록
            else:
                ref = prev_end if prev_end is not None else (start_xy if start_xy is not None else p)
                s = p if np.hypot(*(p - ref)) <= np.hypot(*(q - ref)) else q
            if prev_end is not None:
                n_breaks += 1
                break_m += float(np.hypot(*(s - prev_end)))
        e = q if s is p else p
        if not pts or _node_key(pts[-1]) != _node_key(s):
            pts.append(s)
        pts.append(e)
        prev_end = e
    return shapely.LineString(np.asarray(pts)), n_breaks, break_m


def repeat_ratio(seq):
    """
    링크 반복 비율 = (링크 수 - 서로 다른 링크 수) / 링크 수. 0이면 같은 링크를 두 번 지나지 않는다.
    되돌아오거나 고리를 도는 경로에서 커진다. 그런 경로는 a(가장 가까운 한 지점)가 후보지를 쓸 수 있는
    위치 하나만 나타내므로, 민감도 분석에서 이 값으로 경로를 나눠 결과를 다시 확인한다 (2026-09-28 결정).
    """
    return (len(seq) - len(set(seq))) / len(seq)


def trim_line(line, start_xy, end_xy):
    """
    선 양 끝의 돌출을 잘라 2단계 대표 시점·종점 사이만 남긴다.
    반환: (잘린 선, 시작 쪽 자른 길이 m, 끝 쪽 자른 길이 m)

    시점은 첫 링크(선의 첫 선분)에만, 종점은 마지막 링크(선의 마지막 선분)에만 투영한다.
    선 전체에 투영하면 출발지로 되돌아오는 경로에서 종점이 선의 시작 부분으로 투영돼 선이 사라진다.
    링크는 두 노드를 잇는 직선이므로 첫(마지막) 선분 = 첫(마지막) 링크다.
    시종점이 끝 링크 위에 없으면(1단계에서 반쪽 링크를 못 찾은 경우) 투영이 링크 끝에 걸려 자르지 않는 것과 같다.
    자른 뒤 길이가 0 이하가 되면 자르지 않는다.
    """
    c = shapely.get_coordinates(line)
    first = shapely.LineString(c[:2])
    last = shapely.LineString(c[-2:][::-1])        # 끝에서 거꾸로 잰다
    cut_s = first.project(shapely.Point(start_xy))
    cut_e = last.project(shapely.Point(end_xy))
    if line.length - cut_s - cut_e <= 0:
        return line, 0.0, 0.0
    return shapely.ops.substring(line, cut_s, line.length - cut_e), cut_s, cut_e


def build_route_lines(routes, ends, trim=None):
    """
    대표경로마다 ordered_line (+ trim이면 trim_line).
    반환: DataFrame [route_id, route_len, n_breaks, break_m, repeat_ratio, trim_start_m, trim_end_m, geom]
    """
    trim = C.ROUTE_TRIM_TO_ENDPOINTS if trim is None else trim
    rows = []
    for r in routes.itertuples():
        seq = [int(v) for v in r.link_seq]
        start, end = np.array([r.start_x, r.start_y]), np.array([r.end_x, r.end_y])
        line, nb, bm = ordered_line(seq, ends, start)
        cs, ce = 0.0, 0.0
        if trim:
            line, cs, ce = trim_line(line, start, end)
        rows.append((r.route_id, line.length, nb, bm, repeat_ratio(seq), cs, ce, line))
    return pd.DataFrame(rows, columns=["route_id", "route_len", "n_breaks", "break_m", "repeat_ratio",
                                       "trim_start_m", "trim_end_m", "geom"])


def load_route_lines(trim=None):
    """
    2단계 대표경로 + 1단계 링크 -> (대표경로 DataFrame, 선 DataFrame). 두 표는 같은 순서다.
    """
    routes = pd.read_parquet(S2_ROUTES, columns=ROUTE_COLUMNS)
    links = pd.read_parquet(C.S1_LINKS_PATH, columns=["link_id", "geometry"])
    return routes, build_route_lines(routes, link_ends(links), trim=trim)
