// Spring API 클라이언트. 웹은 계산하지 않고 이 응답만 보여 준다. 좌표는 전부 EPSG:4326.
import type { FeatureCollection, LineString, MultiPolygon, Point } from 'geojson';

export const API_BASE: string = (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://localhost:8080';

export interface Scenario {
  scenarioId: string;
  condId: string;
  mode: 'absolute' | 'ratio';
  buildType: 'new' | 'expand' | 'existing';
  walkM: number;
  tripUnit: string;
  p: number;
  objective: number;
  upperBound: number | null;
  objectiveRatio: number | null;
  nSelected: number;
  nRoutesBenefit: number;
  mipGap: number | null;
  status: string;
}

export interface CoverageCurve {
  condId: string;
  mode: string;
  buildType: string;
  walkM: number;
  tripUnit: string;
  upperBound: number | null;
  scenarioP: number;
  points: { p: number; objective: number; objectiveRatio: number | null }[];
}

export interface StationProps {
  candId: number;
  used: boolean;
  sggCode: string | null;
  nRoutes: number;
  nTrips: number;
  existing: boolean;
  stationId: string | null;
  bootFreq: number | null;
}

export interface RouteProps {
  routeId: number;
  weight: number;
  routeLenM: number;
  obsRatio: number | null;
  repeatRatio: number | null;
  straightRatio: number | null;
  nBreaks: number;
  benefitM: number | null;
  benefit: number | null;
  startCand: number | null;
  endCand: number | null;
}

export interface DistrictProps {
  sggCode: string;
  sggName: string;
  areaKm2: number;
  nCandidates: number;
  nRoutes: number;
  routeKm: number;
}

/** 기존 따릉이 대여소 (지도 표시 전용, 후보지와 연결 없음). guName은 엑셀 자치구 이름 그대로. */
export interface ExistingStationProps {
  stationId: string;
  stationName: string | null;
  guName: string | null;
}

export interface StationCoverage {
  candId: number;
  radiusM: number;
  nRoutes: number;
  nTrips: number;
  routes: { routeId: number; weight: number; distanceM: number }[];
}

export interface DistrictStats {
  sggCode: string;
  sggName: string;
  scenarioId: string;
  areaKm2: number;
  nCandidates: number;
  nSelected: number;
  nRoutes: number;
  nTrips: number;
  nRoutesBenefit: number;
  nTripsBenefit: number;
  routeCoverageRate: number | null;
  tripCoverageRate: number | null;
}

export type Stations = FeatureCollection<Point, StationProps> & { scenarioId: string };
export type Routes = FeatureCollection<LineString, RouteProps> & { scenarioId: string };
export type Districts = FeatureCollection<MultiPolygon, DistrictProps> & { simplifyToleranceM: number };
export type ExistingStations = FeatureCollection<Point, ExistingStationProps>;

/** API 오류 (ProblemDetail의 detail을 담는다). status 0 = 서버에 연결하지 못함. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function get<T>(path: string, params: Record<string, string | number> = {}): Promise<T> {
  const url = new URL(path, API_BASE);
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, String(v));
  let res: Response;
  try {
    res = await fetch(url);
  } catch {
    throw new ApiError(0, `API 서버(${API_BASE})에 연결할 수 없습니다.`);
  }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* 본문이 JSON이 아니면 상태 코드만 보여 준다 */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

// 같은 시나리오를 다시 고르면 다시 받지 않는다 (통행 선은 약 600KB)
const cache = new Map<string, Promise<unknown>>();
function cached<T>(key: string, load: () => Promise<T>): Promise<T> {
  if (!cache.has(key)) {
    const p = load();
    cache.set(key, p);
    p.catch(() => cache.delete(key));
  }
  return cache.get(key) as Promise<T>;
}

export const api = {
  scenarios: () => cached('scenarios', () => get<Scenario[]>('/api/scenarios')),
  coverageCurve: (scenario: string) =>
    cached(`curve:${scenario}`, () => get<CoverageCurve>('/api/coverage-curve', { scenario })),
  stations: (scenario: string) => cached(`stations:${scenario}`, () => get<Stations>('/api/stations', { scenario })),
  routes: (scenario: string) => cached(`routes:${scenario}`, () => get<Routes>('/api/routes', { scenario })),
  districts: () => cached('districts', () => get<Districts>('/api/districts')),
  existingStations: () => cached('existing', () => get<ExistingStations>('/api/existing-stations')),
  stationCoverage: (candId: number) => get<StationCoverage>(`/api/stations/${candId}/coverage`),
  districtStats: (gu: string, scenario: string) =>
    get<DistrictStats>(`/api/districts/${encodeURIComponent(gu)}/stats`, { scenario }),
};
