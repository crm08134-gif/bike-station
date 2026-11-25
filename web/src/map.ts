// 지도 (MapLibre GL). 데이터는 API가 준 GeoJSON(EPSG:4326)을 그대로 소스에 넣는다. 도형 계산은 하지 않는다.
import {
  Map as MapLibreMap,
  NavigationControl,
  Popup,
  ScaleControl,
  setWorkerUrl,
  type ExpressionSpecification,
  type GeoJSONSource,
  type LayerSpecification,
  type MapLayerMouseEvent,
  type SourceSpecification,
} from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
import type { FeatureCollection } from 'geojson';
import type {
  Districts,
  DistrictProps,
  ExistingStationProps,
  ExistingStations,
  Routes,
  StationProps,
  Stations,
} from './api';

// MapLibre 6은 워커 경로를 실행 중에 계산하므로 번들러가 묶은 워커 주소를 직접 알려 준다
setWorkerUrl(workerUrl);

const BASEMAP = {
  light: 'https://tiles.openfreemap.org/styles/positron',
  dark: 'https://tiles.openfreemap.org/styles/dark',
};
const SEOUL_CENTER: [number, number] = [126.98, 37.56];
const PREFIX = 'bs-';
const EMPTY: FeatureCollection = { type: 'FeatureCollection', features: [] };
// 켜고 끄는 레이어 묶음
const LAYER_GROUPS = {
  routes: ['routes-none', 'routes-benefit', 'routes-highlight'],
  districts: ['districts-fill', 'districts-line'],
  existing: ['existing'],
} as const;
export type LayerGroup = keyof typeof LAYER_GROUPS;

export interface MapCallbacks {
  onStationClick: (props: StationProps) => void;
  onDistrictClick: (props: DistrictProps) => void;
  onClear: () => void;
}

/** CSS 변수(--map-*)에서 현재 테마의 색을 읽는다. 색은 style.css 한 곳에서 정한다. */
function palette() {
  const css = getComputedStyle(document.documentElement);
  const v = (name: string) => css.getPropertyValue(name).trim();
  return {
    station: v('--map-station'),
    stationIdle: v('--map-station-idle'),
    existing: v('--map-existing'),
    ring: v('--map-ring'),
    benefit: v('--map-benefit'),
    noBenefit: v('--map-no-benefit'),
    highlight: v('--map-highlight'),
    district: v('--map-district'),
    districtFill: v('--map-district-fill'),
  };
}

function isDark(): boolean {
  const forced = document.documentElement.dataset.theme;
  if (forced) return forced === 'dark';
  return matchMedia('(prefers-color-scheme: dark)').matches;
}

function ourSources(): Record<string, SourceSpecification> {
  return {
    [`${PREFIX}districts`]: { type: 'geojson', data: EMPTY, promoteId: 'sggCode' },
    [`${PREFIX}routes`]: { type: 'geojson', data: EMPTY, promoteId: 'routeId' },
    [`${PREFIX}existing`]: { type: 'geojson', data: EMPTY, promoteId: 'stationId' },
    [`${PREFIX}stations`]: { type: 'geojson', data: EMPTY, promoteId: 'candId' },
  };
}

function ourLayers(): LayerSpecification[] {
  const c = palette();
  const selected: ExpressionSpecification = ['boolean', ['feature-state', 'selected'], false];
  return [
    {
      id: `${PREFIX}districts-fill`,
      type: 'fill',
      source: `${PREFIX}districts`,
      paint: { 'fill-color': c.districtFill, 'fill-opacity': ['case', selected, 0.14, 0] },
    },
    {
      id: `${PREFIX}districts-line`,
      type: 'line',
      source: `${PREFIX}districts`,
      paint: { 'line-color': c.district, 'line-width': ['case', selected, 2, 1] },
    },
    {
      id: `${PREFIX}routes-none`,
      type: 'line',
      source: `${PREFIX}routes`,
      filter: ['==', ['get', 'benefitM'], null],
      layout: { 'line-join': 'round', 'line-cap': 'round' },
      paint: { 'line-color': c.noBenefit, 'line-width': 1.2, 'line-opacity': 0.8 },
    },
    {
      id: `${PREFIX}routes-benefit`,
      type: 'line',
      source: `${PREFIX}routes`,
      filter: ['!=', ['get', 'benefitM'], null],
      layout: { 'line-join': 'round', 'line-cap': 'round' },
      paint: { 'line-color': c.benefit, 'line-width': 2, 'line-opacity': 0.85 },
    },
    {
      id: `${PREFIX}routes-highlight`,
      type: 'line',
      source: `${PREFIX}routes`,
      // 필터에서는 feature-state를 쓸 수 없어 투명도로 켜고 끈다
      layout: { 'line-join': 'round', 'line-cap': 'round' },
      paint: {
        'line-color': c.highlight,
        'line-width': 3.5,
        'line-opacity': ['case', ['boolean', ['feature-state', 'highlight'], false], 1, 0],
      },
    },
    // 기존 따릉이 대여소: 선택한 대여소(파란 점) 바로 앞에 넣어 그 아래에 그린다. 크기·테두리는 선택 안 된 파란 점과 같다.
    // 기본은 숨김 (체크박스로 켠다)
    {
      id: `${PREFIX}existing`,
      type: 'circle',
      source: `${PREFIX}existing`,
      layout: { visibility: 'none' },
      paint: {
        'circle-color': c.existing,
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 9, 3.5, 14, 7],
        'circle-stroke-color': c.ring,
        'circle-stroke-width': 2,
      },
    },
    {
      id: `${PREFIX}stations`,
      type: 'circle',
      source: `${PREFIX}stations`,
      paint: {
        'circle-color': ['case', ['get', 'used'], c.station, c.stationIdle],
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 9, ['case', selected, 6, 3.5], 14, ['case', selected, 10, 7]],
        'circle-stroke-color': ['case', selected, c.highlight, c.ring],
        'circle-stroke-width': 2,
      },
    },
  ];
}

export class StationMap {
  private readonly map: MapLibreMap;
  private readonly hover = new Popup({ closeButton: false, closeOnClick: false, offset: 10, className: 'bs-tip' });
  private readonly existingTip = new Popup({ closeButton: false, closeOnClick: false, offset: 10, className: 'bs-tip' });
  private selectedStation: number | null = null;
  private selectedDistrict: string | null = null;
  private highlighted: number[] = [];
  private data: { districts?: Districts; routes?: Routes; stations?: Stations; existing?: ExistingStations } = {};
  // 테마를 바꾸면 레이어를 다시 얹으므로 켜기/끄기 상태를 기억해 두었다가 다시 입힌다
  private readonly visible: Record<LayerGroup, boolean> = { routes: true, districts: true, existing: false };
  readonly ready: Promise<void>;

  constructor(container: HTMLElement, private readonly cb: MapCallbacks) {
    this.map = new MapLibreMap({
      container,
      style: isDark() ? BASEMAP.dark : BASEMAP.light,
      center: SEOUL_CENTER,
      zoom: 10.4,
      minZoom: 8,
      maxZoom: 17,
      attributionControl: { compact: true },
    });
    // 개발 서버에서만: 브라우저 자동 확인 스크립트가 지도 상태를 읽을 수 있게 한다 (배포 빌드에는 없음)
    if (import.meta.env.DEV) (window as unknown as { __bsMap: MapLibreMap }).__bsMap = this.map;
    this.map.addControl(new NavigationControl({ showCompass: false }), 'top-right');
    this.map.addControl(new ScaleControl({ unit: 'metric' }), 'bottom-left');
    this.ready = new Promise((resolve) => {
      this.map.once('load', () => {
        this.install();
        resolve();
      });
    });
    this.bindEvents();
    matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => this.retheme());
  }

  /** 배경지도 위에 우리 소스·레이어를 얹고, 지명은 한글(name:ko)만 보이게 한다. */
  private install() {
    for (const [id, src] of Object.entries(ourSources())) {
      if (!this.map.getSource(id)) this.map.addSource(id, src);
    }
    for (const layer of ourLayers()) {
      if (!this.map.getLayer(layer.id)) this.map.addLayer(layer);
    }
    for (const layer of this.map.getStyle().layers) {
      if (layer.type === 'symbol' && !layer.id.startsWith(PREFIX) && this.map.getLayoutProperty(layer.id, 'text-field')) {
        const tf = JSON.stringify(this.map.getLayoutProperty(layer.id, 'text-field'));
        if (tf.includes('name')) {
          this.map.setLayoutProperty(layer.id, 'text-field', ['coalesce', ['get', 'name:ko'], ['get', 'name']]);
        }
      }
    }
    this.restore();
  }

  /** 테마가 바뀌면 배경지도를 바꾸고 우리 레이어를 새 색으로 다시 얹는다. */
  private retheme() {
    this.map.setStyle(isDark() ? BASEMAP.dark : BASEMAP.light);
    this.map.once('style.load', () => this.install());
  }

  private restore() {
    for (const group of Object.keys(LAYER_GROUPS) as LayerGroup[]) this.setLayerVisible(group, this.visible[group]);
    if (this.data.existing) this.setExistingStations(this.data.existing);
    if (this.data.districts) this.setDistricts(this.data.districts);
    if (this.data.routes) this.setRoutes(this.data.routes);
    if (this.data.stations) this.setStations(this.data.stations);
  }

  private source(name: string) {
    return this.map.getSource(`${PREFIX}${name}`) as GeoJSONSource | undefined;
  }

  setDistricts(fc: Districts) {
    this.data.districts = fc;
    this.source('districts')?.setData(fc);
    this.applyStates();
  }

  setRoutes(fc: Routes) {
    this.data.routes = fc;
    this.source('routes')?.setData(fc);
    this.applyStates();
  }

  setStations(fc: Stations) {
    this.data.stations = fc;
    this.source('stations')?.setData(fc);
    // 시나리오가 바뀌어 선택했던 대여소가 없어지면 선택을 풀지 않고 표시만 사라진다
    this.applyStates();
  }

  setExistingStations(fc: ExistingStations) {
    this.data.existing = fc;
    this.source('existing')?.setData(fc);
  }

  setLayerVisible(group: LayerGroup, visible: boolean) {
    this.visible[group] = visible;
    for (const id of LAYER_GROUPS[group]) {
      if (this.map.getLayer(`${PREFIX}${id}`)) {
        this.map.setLayoutProperty(`${PREFIX}${id}`, 'visibility', visible ? 'visible' : 'none');
      }
    }
    if (group === 'existing' && !visible) this.existingTip.remove();
  }

  selectStation(candId: number | null, routeIds: number[] = []) {
    this.selectedStation = candId;
    this.highlighted = routeIds;
    this.applyStates();
  }

  selectDistrict(sggCode: string | null) {
    this.selectedDistrict = sggCode;
    this.applyStates();
  }

  /** 선택·강조 상태는 feature-state로 둔다 (setData 뒤에도 다시 입힌다). */
  private applyStates() {
    if (!this.map.isStyleLoaded() && !this.map.loaded()) return;
    for (const name of ['districts', 'routes', 'stations']) {
      if (this.source(name)) this.map.removeFeatureState({ source: `${PREFIX}${name}` });
    }
    if (this.selectedDistrict && this.source('districts')) {
      this.map.setFeatureState({ source: `${PREFIX}districts`, id: this.selectedDistrict }, { selected: true });
    }
    if (this.selectedStation !== null && this.source('stations')) {
      this.map.setFeatureState({ source: `${PREFIX}stations`, id: this.selectedStation }, { selected: true });
    }
    if (this.source('routes')) {
      for (const id of this.highlighted) this.map.setFeatureState({ source: `${PREFIX}routes`, id }, { highlight: true });
    }
  }

  private bindEvents() {
    const stations = `${PREFIX}stations`;
    const existing = `${PREFIX}existing`;
    const districts = `${PREFIX}districts-fill`;
    this.map.on('mousemove', stations, (e: MapLayerMouseEvent) => {
      const f = e.features?.[0];
      if (!f) return;
      this.map.getCanvas().style.cursor = 'pointer';
      const p = f.properties as unknown as StationProps;
      const el = document.createElement('div');
      const strong = document.createElement('strong');
      strong.textContent = `대여소 #${p.candId}`;
      const span = document.createElement('span');
      span.textContent = p.used ? '혜택 구간 끝으로 쓰임' : '혜택 구간 끝으로 안 쓰임';
      el.append(strong, span);
      this.hover.setLngLat(e.lngLat).setDOMContent(el).addTo(this.map);
    });
    this.map.on('mouseleave', stations, () => {
      this.map.getCanvas().style.cursor = '';
      this.hover.remove();
    });
    this.map.on('mouseenter', existing, () => {
      this.map.getCanvas().style.cursor = 'pointer';
    });
    this.map.on('mouseleave', existing, () => {
      this.map.getCanvas().style.cursor = '';
    });
    this.map.on('mouseenter', districts, () => {
      if (!this.map.getCanvas().style.cursor) this.map.getCanvas().style.cursor = 'pointer';
    });
    this.map.on('mouseleave', districts, () => {
      this.map.getCanvas().style.cursor = '';
    });
    this.map.on('click', (e) => {
      const hits = this.map.queryRenderedFeatures(e.point, {
        layers: [stations, existing, districts].filter((id) => this.map.getLayer(id)),
      });
      this.existingTip.remove();
      const station = hits.find((f) => f.layer.id === stations);
      if (station) {
        this.cb.onStationClick(station.properties as unknown as StationProps);
        return;
      }
      // 기존 대여소는 선택 상태를 바꾸지 않고 이름·번호만 작은 팝업으로 보여 준다 (숨긴 레이어는 잡히지 않는다)
      const ex = hits.find((f) => f.layer.id === existing);
      if (ex) {
        const p = ex.properties as unknown as ExistingStationProps;
        const el = document.createElement('div');
        const strong = document.createElement('strong');
        strong.textContent = p.stationName ?? '(이름 없음)';
        const span = document.createElement('span');
        span.textContent = `기존 대여소 #${p.stationId}`;
        el.append(strong, span);
        this.existingTip.setLngLat(e.lngLat).setDOMContent(el).addTo(this.map);
        return;
      }
      const district = hits.find((f) => f.layer.id === districts);
      if (district && this.map.getLayoutProperty(districts, 'visibility') !== 'none') {
        this.cb.onDistrictClick(district.properties as unknown as DistrictProps);
        return;
      }
      this.cb.onClear();
    });
  }
}
