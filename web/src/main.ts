// 지도 화면. 시나리오(조건 + 대여소 수 p)를 고르면 API에서 받아 그대로 보여 준다.
import { api, ApiError, type DistrictProps, type Districts, type Scenario, type StationProps, type Stations } from './api';
import { renderCurve } from './curve';
import { StationMap } from './map';

type Cond = Pick<Scenario, 'mode' | 'buildType' | 'walkM' | 'tripUnit'>;
const COND_KEYS = ['mode', 'buildType', 'walkM', 'tripUnit'] as const;

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const fmtPct = (v: number | null) => (v === null ? '–' : `${(v * 100).toFixed(1)}%`);
const fmtNum = (v: number) => v.toLocaleString('ko-KR', { maximumFractionDigits: v < 100 ? 2 : 0 });
// 목적값: 거리 합은 m 정수, 통행 비율 합은 소수점 한 자리
const fmtObj = (v: number, mode: string) =>
  mode === 'absolute' ? `${fmtNum(Math.round(v))}m` : v.toLocaleString('ko-KR', { maximumFractionDigits: 1 });

const LABELS: Record<string, (v: string | number) => string> = {
  mode: (v) => ({ absolute: '거리 합 (absolute)', ratio: '통행 비율 합 (ratio)' })[v as string] ?? String(v),
  buildType: (v) => ({ new: '신규', expand: '증설', existing: '기존만' })[v as string] ?? String(v),
  walkM: (v) => `도보 ${v}m`,
  tripUnit: (v) => {
    const m = /^journey_gap(\d+)m$/.exec(String(v));
    if (m) return `여정 (공백 ${m[1]}분까지 이음)`;
    return String(v) === 'journey_nogap' ? '여정 (공백 상한 없음)' : String(v);
  },
};
const COND_TITLES: Record<string, string> = { mode: '목적함수', buildType: '설치 방식', walkM: '도보 반경', tripUnit: '통행 단위' };
const MODE_HELP: Record<string, string> = {
  absolute: '대여소 두 곳 사이를 탈 수 있는 거리(m)의 합. 긴 통행에 무게가 실린다.',
  ratio: '그 거리가 통행 길이에서 차지하는 비율의 합. 통행마다 같은 무게로 센다.',
};

const state = {
  scenarios: [] as Scenario[],
  cond: null as Cond | null,
  p: 0,
  current: null as Scenario | null,
  districts: null as Districts | null,
  stations: null as Stations | null,
  station: null as StationProps | null,
  district: null as DistrictProps | null,
  token: 0,
};

const map = new StationMap($('map'), {
  // 지도가 돌려주는 속성은 null 값이 빠지므로, API 응답 원본에서 같은 대여소를 찾아 쓴다
  onStationClick: (p) => void showStation(state.stations?.features.find((f) => f.properties.candId === p.candId)?.properties ?? p),
  onDistrictClick: (p) => void showDistrict(p),
  onClear: () => clearSelection(),
});

// ---------------------------------------------------------------------------
// 오류
// ---------------------------------------------------------------------------
function showError(e: unknown) {
  const box = $('error');
  const msg = e instanceof ApiError ? e.message : '알 수 없는 오류가 났습니다.';
  box.textContent =
    e instanceof ApiError && e.status === 0 ? `${msg} Spring API가 실행 중인지, web/.env의 VITE_API_BASE가 맞는지 확인하세요.` : msg;
  box.hidden = false;
  console.error(e);
}
function clearError() {
  $('error').hidden = true;
}

// ---------------------------------------------------------------------------
// 조건·p 선택
// ---------------------------------------------------------------------------
const sameCond = (s: Scenario, c: Cond) => COND_KEYS.every((k) => s[k] === c[k]);
const choosable = () => state.scenarios.filter((s) => s.buildType !== 'existing');

function pList(c: Cond): number[] {
  return choosable()
    .filter((s) => sameCond(s, c))
    .map((s) => s.p)
    .sort((a, b) => a - b);
}

function renderConditions() {
  const root = $('conditions');
  root.replaceChildren();
  for (const key of COND_KEYS) {
    const values = [...new Set(choosable().map((s) => s[key]))];
    // 목적함수는 항상 보여 주고, 나머지 조건은 값이 두 개 이상일 때만 (민감도 분석 결과가 들어오면 나타난다)
    if (key !== 'mode' && values.length < 2) continue;
    const group = document.createElement('div');
    group.className = 'segment';
    group.setAttribute('role', 'group');
    const label = document.createElement('span');
    label.className = 'segment-label';
    label.textContent = COND_TITLES[key];
    group.setAttribute('aria-label', COND_TITLES[key]);
    group.append(label);
    for (const v of values) {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = LABELS[key](v);
      b.setAttribute('aria-pressed', String(state.cond?.[key] === v));
      b.addEventListener('click', () => {
        if (!state.cond || state.cond[key] === v) return;
        const next = { ...state.cond, [key]: v } as Cond;
        // 새 조건에 같은 p가 없으면 가장 가까운 p로
        const ps = pList(next);
        if (ps.length === 0) return;
        state.cond = next;
        state.p = ps.reduce((a, b2) => (Math.abs(b2 - state.p) < Math.abs(a - state.p) ? b2 : a));
        renderConditions();
        renderSlider();
        void select();
      });
      group.append(b);
    }
    root.append(group);
  }
  $('mode-help').textContent = state.cond ? MODE_HELP[state.cond.mode] ?? '' : '';
}

function renderSlider() {
  if (!state.cond) return;
  const ps = pList(state.cond);
  const slider = $<HTMLInputElement>('p-slider');
  slider.max = String(ps.length - 1);
  slider.value = String(Math.max(0, ps.indexOf(state.p)));
  slider.setAttribute('aria-valuetext', `대여소 ${state.p}개`);
  $('p-value').textContent = `${state.p}개`;
  const ticks = $('p-ticks');
  ticks.replaceChildren(...ps.map((p) => Object.assign(document.createElement('span'), { textContent: String(p) })));
}

$<HTMLInputElement>('p-slider').addEventListener('input', (e) => {
  if (!state.cond) return;
  const ps = pList(state.cond);
  state.p = ps[Number((e.target as HTMLInputElement).value)];
  $('p-value').textContent = `${state.p}개`;
  (e.target as HTMLInputElement).setAttribute('aria-valuetext', `대여소 ${state.p}개`);
  void select();
});

// ---------------------------------------------------------------------------
// 시나리오 불러오기
// ---------------------------------------------------------------------------
async function select() {
  if (!state.cond) return;
  const sc = choosable().find((s) => sameCond(s, state.cond as Cond) && s.p === state.p);
  if (!sc) return;
  state.current = sc;
  const token = ++state.token;
  const url = new URL(location.href);
  url.searchParams.set('scenario', sc.scenarioId);
  history.replaceState(null, '', url);

  $('result').classList.add('loading');
  $('curve').parentElement?.classList.add('loading');
  try {
    const [stations, routes, curve] = await Promise.all([
      api.stations(sc.scenarioId),
      api.routes(sc.scenarioId),
      api.coverageCurve(sc.scenarioId),
    ]);
    if (token !== state.token) return; // 그사이 다른 시나리오를 골랐다
    clearError();
    state.stations = stations;
    // 패널은 지도 로딩과 상관없이 먼저 그린다
    renderTiles(sc, routes.features.length);
    renderCurve($('curve'), curve, sc.p, (p) => {
      state.p = p;
      renderSlider();
      void select();
    });
    renderCompare(sc);
    await map.ready;
    if (token !== state.token) return;
    map.setStations(stations);
    map.setRoutes(routes);
    if (state.station && !stations.features.some((f) => f.properties.candId === state.station?.candId)) {
      clearSelection();
    } else if (state.district) {
      void showDistrict(state.district);
    }
  } catch (e) {
    if (token === state.token) showError(e);
  } finally {
    if (token === state.token) {
      $('result').classList.remove('loading');
      $('curve').parentElement?.classList.remove('loading');
    }
  }
}

function tile(label: string, value: string, sub: string) {
  const t = document.createElement('div');
  t.className = 'tile';
  for (const [cls, text] of [
    ['label', label],
    ['value', value],
    ['sub', sub],
  ]) {
    const d = document.createElement('div');
    d.className = cls;
    d.textContent = text;
    t.append(d);
  }
  return t;
}

function renderTiles(sc: Scenario, nTrips: number) {
  const ub = sc.upperBound === null ? '' : ` / 상한 ${fmtObj(sc.upperBound, sc.mode)}`;
  const root = $('tiles');
  root.replaceChildren(
    tile('목적값 (상한 대비)', fmtPct(sc.objectiveRatio), `${fmtObj(sc.objective, sc.mode)}${ub}`),
    tile('선택 대여소', `${sc.nSelected}개`, sc.nSelected < sc.p ? `p=${sc.p}보다 적게 골라도 상한에 닿음` : `p=${sc.p}`),
    tile('혜택 받는 통행', `${sc.nRoutesBenefit}`, `전체 통행 ${nTrips}개 중`),
  );
  const status = document.createElement('p');
  status.className = 'status';
  const gap = sc.mipGap === null ? '' : `, 상대 갭 ${(sc.mipGap * 100).toFixed(3)}%`;
  status.textContent = `최적화 상태: ${sc.status === 'optimal' ? '최적' : sc.status}${gap}`;
  root.append(status);
  status.style.gridColumn = '1 / -1';
}

// ---------------------------------------------------------------------------
// 선택 정보 (대여소, 구)
// ---------------------------------------------------------------------------
function card(title: string, rows: [string, string][], note?: string) {
  const c = document.createElement('div');
  c.className = 'card';
  const h = document.createElement('h3');
  h.textContent = title;
  const dl = document.createElement('dl');
  for (const [k, v] of rows) {
    const dt = document.createElement('dt');
    dt.textContent = k;
    const dd = document.createElement('dd');
    dd.textContent = v;
    dl.append(dt, dd);
  }
  c.append(h, dl);
  if (note) {
    const p = document.createElement('p');
    p.className = 'muted small';
    p.style.marginTop = '8px';
    p.textContent = note;
    c.append(p);
  }
  const b = document.createElement('button');
  b.type = 'button';
  b.textContent = '선택 해제';
  b.addEventListener('click', clearSelection);
  c.append(b);
  return c;
}

function districtName(code: string | null) {
  if (!code) return '서울 밖';
  return state.districts?.features.find((f) => f.properties.sggCode === code)?.properties.sggName ?? code;
}

async function showStation(p: StationProps) {
  state.station = p;
  state.district = null;
  map.selectDistrict(null);
  map.selectStation(p.candId);
  const detail = $('detail');
  const base: [string, string][] = [
    ['구', districtName(p.sggCode)],
    ['혜택 구간의 끝으로', p.used ? '쓰임' : '안 쓰임'],
    ['부트스트랩 선택 빈도', p.bootFreq == null ? '계산 전' : fmtPct(p.bootFreq)],
  ];
  detail.replaceChildren(card(`대여소 #${p.candId}`, [...base, ['도보 반경 안 통행', '불러오는 중…']]));
  try {
    const cov = await api.stationCoverage(p.candId);
    if (state.station?.candId !== p.candId) return;
    map.selectStation(p.candId, cov.routes.map((r) => r.routeId));
    detail.replaceChildren(
      card(`대여소 #${p.candId}`, [...base, [`도보 ${cov.radiusM}m 안 통행`, `${cov.nRoutes}개`]], '반경 안 통행은 지도에 진하게 표시됩니다. 거리는 직선거리입니다.'),
    );
  } catch (e) {
    showError(e);
  }
}

async function showDistrict(p: DistrictProps) {
  if (!state.current) return;
  state.district = p;
  state.station = null;
  map.selectStation(null);
  map.selectDistrict(p.sggCode);
  const detail = $('detail');
  const scenario = state.current.scenarioId;
  try {
    const s = await api.districtStats(p.sggCode, scenario);
    if (state.district?.sggCode !== p.sggCode || state.current?.scenarioId !== scenario) return;
    detail.replaceChildren(
      card(
        s.sggName,
        [
          ['선택 대여소', `${s.nSelected}개 (후보지 ${s.nCandidates}개)`],
          ['구를 지나는 통행', `${s.nRoutes}개`],
          ['혜택 받는 통행', `${s.nRoutesBenefit}개`],
          ['커버율 (통행 수 기준)', fmtPct(s.routeCoverageRate)],
          ['커버율 (가중치 기준)', fmtPct(s.tripCoverageRate)],
        ],
        '커버율 = 구를 지나는 통행 중 이 시나리오에서 대여소 사이를 탈 수 있는 통행의 비율.',
      ),
    );
  } catch (e) {
    showError(e);
  }
}

function clearSelection() {
  state.station = null;
  state.district = null;
  map.selectStation(null);
  map.selectDistrict(null);
  const hint = document.createElement('p');
  hint.className = 'muted';
  hint.textContent = '지도에서 대여소나 구를 누르면 자세한 내용이 여기에 나옵니다.';
  $('detail').replaceChildren(hint);
}

// ---------------------------------------------------------------------------
// 기존 대여소 비교 (기준선 = build_type 'existing' 시나리오)
// ---------------------------------------------------------------------------
function renderCompare(sc: Scenario) {
  const root = $('compare');
  const base = state.scenarios.find(
    (s) => s.buildType === 'existing' && s.mode === sc.mode && s.walkM === sc.walkM && s.tripUnit === sc.tripUnit,
  );
  if (!base) {
    const p = document.createElement('p');
    p.className = 'muted';
    p.textContent =
      '기존 대여소 데이터가 아직 없습니다. 데이터를 넣고 분석을 다시 돌리면 기존 대여소만 있을 때의 점수(기준선)와 여기서 비교됩니다.';
    root.replaceChildren(p);
    return;
  }
  const c = document.createElement('div');
  c.className = 'card';
  const dl = document.createElement('dl');
  for (const [k, v] of [
    [`기존 대여소 ${base.p}개 (기준선)`, `${fmtObj(base.objective, base.mode)} (${fmtPct(base.objectiveRatio)})`],
    [`이 시나리오 (p=${sc.p})`, `${fmtObj(sc.objective, sc.mode)} (${fmtPct(sc.objectiveRatio)})`],
  ]) {
    const dt = document.createElement('dt');
    dt.textContent = k;
    const dd = document.createElement('dd');
    dd.textContent = v;
    dl.append(dt, dd);
  }
  c.append(dl);
  root.replaceChildren(c);
}

// ---------------------------------------------------------------------------
// 지도 표시 토글
// ---------------------------------------------------------------------------
for (const [id, group] of [
  ['show-routes', 'routes'],
  ['show-districts', 'districts'],
] as const) {
  $<HTMLInputElement>(id).addEventListener('change', async (e) => {
    await map.ready;
    map.setLayerVisible(group, (e.target as HTMLInputElement).checked);
  });
}

// 기존 따릉이 대여소: 기본 꺼짐. 처음 켤 때 한 번 받는다 (api 캐시)
$<HTMLInputElement>('show-existing').addEventListener('change', async (e) => {
  const box = e.target as HTMLInputElement;
  try {
    if (box.checked) {
      const fc = await api.existingStations();
      await map.ready;
      map.setExistingStations(fc);
    }
    await map.ready;
    map.setLayerVisible('existing', box.checked);
  } catch (err) {
    box.checked = false;
    showError(err);
  }
});

// ---------------------------------------------------------------------------
// 시작
// ---------------------------------------------------------------------------
async function start() {
  clearSelection();
  try {
    state.scenarios = await api.scenarios();
  } catch (e) {
    showError(e);
    return;
  }
  const list = choosable();
  if (list.length === 0) {
    showError(new ApiError(404, '시나리오가 없습니다. 9단계 적재를 먼저 실행하세요.'));
    return;
  }
  // 주소의 ?scenario=가 있으면 그 시나리오로, 없으면 첫 조건에서 p=100 (없으면 가운데 p)
  const wanted = new URL(location.href).searchParams.get('scenario');
  const first = list.find((s) => s.scenarioId === wanted) ?? list[0];
  state.cond = { mode: first.mode, buildType: first.buildType, walkM: first.walkM, tripUnit: first.tripUnit };
  const ps = pList(state.cond);
  state.p = first.scenarioId === wanted ? first.p : ps.includes(100) ? 100 : ps[Math.floor(ps.length / 2)];
  renderConditions();
  renderSlider();

  api
    .districts()
    .then(async (d) => {
      state.districts = d;
      await map.ready;
      map.setDistricts(d);
    })
    .catch(showError);
  await select();
}

void start();
