// 커버리지 곡선 (SVG). x = 대여소 수 p (로그 눈금), y = 목적값 / 상한 (%). 값은 API가 준 그대로 그린다.
import type { CoverageCurve } from './api';

const SVG = 'http://www.w3.org/2000/svg';
const W = 340;
const H = 190;
const M = { top: 14, right: 16, bottom: 34, left: 44 };

type Pt = { p: number; y: number; objective: number };

function el<K extends keyof SVGElementTagNameMap>(tag: K, attrs: Record<string, string | number> = {}) {
  const e = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, String(v));
  return e;
}

const fmtPct = (v: number) => `${(v * 100).toFixed(1)}%`;
const fmtNum = (v: number) => v.toLocaleString('ko-KR', { maximumFractionDigits: v < 100 ? 2 : 0 });

/** 점이 적으면 점마다 눈금, 많으면 1·2·5 계열 눈금. */
function xTicks(ps: number[]): number[] {
  if (ps.length <= 8) return ps;
  const nice = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000];
  return nice.filter((t) => t >= ps[0] && t <= ps[ps.length - 1]);
}

export function renderCurve(
  root: HTMLElement,
  curve: CoverageCurve,
  currentP: number,
  onPick: (p: number) => void,
) {
  root.replaceChildren();
  // 상한이 없으면 목적값 자체를 최댓값 기준으로 그린다
  const hasRatio = curve.points.every((d) => d.objectiveRatio !== null);
  const maxObj = Math.max(...curve.points.map((d) => d.objective), 1e-12);
  const pts: Pt[] = curve.points
    .filter((d) => d.p > 0)
    .map((d) => ({ p: d.p, objective: d.objective, y: hasRatio ? (d.objectiveRatio as number) : d.objective / maxObj }));
  if (pts.length === 0) {
    const empty = document.createElement('p');
    empty.className = 'muted';
    empty.textContent = '이 조건의 곡선 데이터가 없습니다.';
    root.append(empty);
    return;
  }

  const ps = pts.map((d) => d.p);
  const lo = Math.log10(Math.min(...ps));
  const hi = Math.log10(Math.max(...ps));
  const span = hi - lo || 1;
  const x = (p: number) => M.left + ((Math.log10(p) - lo) / span) * (W - M.left - M.right);
  const y = (v: number) => M.top + (1 - v) * (H - M.top - M.bottom);

  const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, class: 'curve', role: 'img' });
  const title = el('title');
  title.textContent = `커버리지 곡선: 대여소 수에 따른 목적값 (${hasRatio ? '상한 대비' : '최댓값 대비'})`;
  svg.append(title);

  // 격자와 축 (가는 선, 눈에 띄지 않게)
  for (const t of [0, 0.25, 0.5, 0.75, 1]) {
    svg.append(el('line', { x1: M.left, x2: W - M.right, y1: y(t), y2: y(t), class: t === 0 ? 'axis' : 'grid' }));
    const lab = el('text', { x: M.left - 6, y: y(t) + 4, 'text-anchor': 'end', class: 'tick' });
    lab.textContent = `${t * 100}%`;
    svg.append(lab);
  }
  for (const t of xTicks(ps)) {
    const lab = el('text', { x: x(t), y: H - M.bottom + 16, 'text-anchor': 'middle', class: 'tick' });
    lab.textContent = String(t);
    svg.append(lab);
  }
  const xl = el('text', { x: (M.left + W - M.right) / 2, y: H - 4, 'text-anchor': 'middle', class: 'axis-label' });
  xl.textContent = '대여소 수 p (로그 눈금)';
  svg.append(xl);

  // 선과 점
  const d = pts.map((q, i) => `${i ? 'L' : 'M'}${x(q.p).toFixed(1)},${y(q.y).toFixed(1)}`).join('');
  svg.append(el('path', { d, class: 'series' }));
  for (const q of pts) {
    svg.append(el('circle', { cx: x(q.p), cy: y(q.y), r: q.p === currentP ? 6 : 4, class: q.p === currentP ? 'dot current' : 'dot' }));
  }
  const cur = pts.find((q) => q.p === currentP);
  if (cur) {
    const lab = el('text', {
      x: x(cur.p),
      y: y(cur.y) - 11,
      'text-anchor': x(cur.p) > W - 60 ? 'end' : x(cur.p) < M.left + 30 ? 'start' : 'middle',
      class: 'value-label',
    });
    lab.textContent = `p=${cur.p} · ${fmtPct(cur.y)}`;
    svg.append(lab);
  }

  // 십자선 + 툴팁: 포인터와 가장 가까운 p에 붙는다. 클릭하면 그 p로 이동
  const cross = el('line', { y1: M.top, y2: H - M.bottom, class: 'crosshair', visibility: 'hidden' });
  const hit = el('rect', { x: M.left - 10, y: 0, width: W - M.left - M.right + 20, height: H, fill: 'transparent', class: 'hit' });
  svg.append(cross, hit);
  const tip = document.createElement('div');
  tip.className = 'curve-tip';
  tip.hidden = true;

  const nearest = (clientX: number) => {
    const r = svg.getBoundingClientRect();
    const sx = ((clientX - r.left) / r.width) * W;
    return pts.reduce((a, b) => (Math.abs(x(b.p) - sx) < Math.abs(x(a.p) - sx) ? b : a));
  };
  const show = (q: Pt) => {
    cross.setAttribute('x1', String(x(q.p)));
    cross.setAttribute('x2', String(x(q.p)));
    cross.setAttribute('visibility', 'visible');
    const v = document.createElement('strong');
    v.textContent = fmtPct(q.y);
    const s = document.createElement('span');
    s.textContent = `p=${q.p} · 목적값 ${fmtNum(q.objective)}`;
    tip.replaceChildren(v, s);
    tip.hidden = false;
    const r = svg.getBoundingClientRect();
    const px = (x(q.p) / W) * r.width;
    tip.style.left = `${Math.min(Math.max(px, 60), r.width - 60)}px`;
    tip.style.top = `${(y(q.y) / H) * r.height}px`;
  };
  const hide = () => {
    cross.setAttribute('visibility', 'hidden');
    tip.hidden = true;
  };
  hit.addEventListener('pointermove', (e) => show(nearest(e.clientX)));
  hit.addEventListener('pointerleave', hide);
  hit.addEventListener('click', (e) => onPick(nearest(e.clientX).p));

  const wrap = document.createElement('div');
  wrap.className = 'curve-wrap';
  wrap.append(svg, tip);

  // 표로 보기 (툴팁 없이도 모든 값을 볼 수 있게)
  const details = document.createElement('details');
  const summary = document.createElement('summary');
  summary.textContent = '표로 보기';
  const table = document.createElement('table');
  const head = table.createTHead().insertRow();
  for (const h of ['p', '목적값', hasRatio ? '상한 대비' : '최댓값 대비']) {
    const th = document.createElement('th');
    th.textContent = h;
    head.append(th);
  }
  const body = table.createTBody();
  for (const q of pts) {
    const row = body.insertRow();
    if (q.p === currentP) row.className = 'current';
    for (const v of [String(q.p), fmtNum(q.objective), fmtPct(q.y)]) row.insertCell().textContent = v;
  }
  details.append(summary, table);
  root.append(wrap, details);
}
