// 한계 설명 페이지의 '지금 데이터 상태'. API 응답을 읽어 목록으로 보여 준다.
import { api, ApiError, type Scenario } from './api';

const list = document.getElementById('status') as HTMLUListElement;

function item(title: string, text: string) {
  const li = document.createElement('li');
  const strong = document.createElement('strong');
  strong.textContent = `${title}: `;
  li.append(strong, document.createTextNode(text));
  return li;
}

const MODE = { absolute: '거리 합', ratio: '통행 비율 합' } as Record<string, string>;

async function main() {
  const scenarios = await api.scenarios();
  const choosable = scenarios.filter((s) => s.buildType !== 'existing');
  if (choosable.length === 0) throw new ApiError(404, '시나리오가 없습니다.');
  const first = choosable[0];
  const [routes, stations, curve, districts] = await Promise.all([
    api.routes(first.scenarioId),
    api.stations(first.scenarioId),
    api.coverageCurve(first.scenarioId),
    api.districts(),
  ]);

  const items: HTMLLIElement[] = [];
  items.push(item('통행', `${routes.features.length}개 (서울 안을 지나는 여정)`));
  const unitText = (u: string) => {
    const m = /^journey_gap(\d+)m$/.exec(u);
    return m ? `여정 (끊긴 조각을 공백 ${m[1]}분까지 이음)` : u === 'journey_nogap' ? '여정 (공백 상한 없음)' : u;
  };
  items.push(item('통행 단위', [...new Set(choosable.map((s) => s.tripUnit))].map(unitText).join(', ')));
  items.push(item('도보 반경', [...new Set(choosable.map((s) => `${s.walkM}m`))].join(', ') + ' (직선거리)'));

  const conds = [...new Set(choosable.map((s) => s.condId))];
  const ps = [...new Set(choosable.map((s) => s.p))].sort((a, b) => a - b);
  items.push(item('시나리오', `조건 ${conds.length}개 × 대여소 수 p ${ps.join(', ')}`));

  const walks = new Set(choosable.map((s) => s.walkM));
  const units = new Set(choosable.map((s) => s.tripUnit));
  const builds = new Set(choosable.map((s) => s.buildType));
  items.push(
    item(
      '민감도 분석',
      walks.size > 1 || units.size > 1 || builds.size > 1
        ? `도보 반경 ${walks.size}개, 통행 단위 ${units.size}개, 설치 방식 ${builds.size}개 조건`
        : '아직 없음 (도보 반경·통행 단위·설치 방식이 한 조건뿐)',
    ),
  );

  const curvePs = curve.points.map((d) => d.p);
  items.push(
    item(
      '커버리지 곡선',
      curvePs.length ? `p ${curvePs[0]} ~ ${curvePs[curvePs.length - 1]} 중 ${curvePs.length}개 점` : '없음',
    ),
  );

  items.push(
    item(
      '부트스트랩 선택 빈도',
      stations.features.some((f) => f.properties.bootFreq !== null) ? '계산됨' : '계산 전 (지도에 "계산 전"으로 표시)',
    ),
  );

  items.push(
    item(
      '기존 대여소',
      scenarios.some((s) => s.buildType === 'existing') ? '기준선 시나리오 있음' : '데이터 없음 (기준선·증설 비교 불가)',
    ),
  );

  // 조건마다 상한(objectiveRatio = 1)에 처음 닿는 p
  for (const cond of conds) {
    const rows = choosable.filter((s) => s.condId === cond).sort((a, b) => a.p - b.p);
    const hit = rows.find((s: Scenario) => s.objectiveRatio !== null && s.objectiveRatio >= 0.9999);
    const mode = MODE[rows[0].mode] ?? rows[0].mode;
    items.push(item(`상한 도달 (${mode})`, hit ? `p=${hit.p}부터 상한의 99.99% 이상` : '이 p 범위에서는 닿지 않음'));
  }

  items.push(item('구 경계', `${districts.features.length}개 구, 표시용 단순화 허용오차 ${districts.simplifyToleranceM}m`));
  list.replaceChildren(...items);
}

main().catch((e) => {
  const box = document.getElementById('error') as HTMLDivElement;
  box.textContent = e instanceof ApiError ? e.message : '데이터 상태를 불러오지 못했습니다.';
  box.hidden = false;
  list.replaceChildren();
});
