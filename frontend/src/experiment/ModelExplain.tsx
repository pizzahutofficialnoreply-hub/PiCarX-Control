// 고른 관계식의 각 항목(식, 매개변수, 맞춤 지표)을 실제 값으로 쉬운 말 설명합니다.
import type { ModelComparison, ModelParam, ModelResult } from '../services/experiment';

const f = (x: number, d = 2) => (Math.abs(x) >= 100 ? x.toFixed(0) : x.toFixed(d));
const inCi = (p: ModelParam | undefined, x: number) => !!p?.ci95 && p.ci95[0] <= x && x <= p.ci95[1];
const ciText = (p: ModelParam | undefined, exp = false) => !p?.ci95 ? '점이 적어 신뢰구간은 아직 못 구했습니다.'
  : `95% 신뢰구간 ${exp ? Math.exp(p.ci95[0]).toPrecision(3) : f(p.ci95[0])} ~ ${exp ? Math.exp(p.ci95[1]).toPrecision(3) : f(p.ci95[1])}: 같은 실험을 여러 번 다시 해도 참값이 이 범위 안에 있을 가능성이 95%라는 뜻이고, 좁을수록 확실합니다.`;

type Row = { name: string; value: string; text: string };

function paramRows(m: ModelResult): Row[] {
  const c = m.coef, P = (n: string) => m.params.find(p => p.name === n);
  const rows: Row[] = [];
  if (m.kind === 'power') {
    const n = c.n, p = c.p ?? 0;
    rows.push({ name: 'k₀ (크기 계수)', value: c.k0.toPrecision(3),
      text: `속도 1 m/s${m.id === 'power_v' ? '' : ', 질량 1 kg'}일 때 예상되는 충격(평소보다 더 받는 가속도)이 약 ${c.k0.toPrecision(3)} g라는 뜻입니다. 방지턱 모양·차 서스펜션이 바뀌면 이 값이 바뀝니다. ${ciText(P('ln k₀'), true)}` });
    const nFixed = m.id === 'n1' || m.id === 'n2';
    const nP = P('n');
    rows.push({ name: 'n (속도 지수)', value: f(n),
      text: `${nFixed ? `이 식은 n을 ${n}로 고정한 이론식입니다. ` : ''}속도를 2배로 하면 충격이 2ⁿ = ${f(2 ** n)}배가 됩니다(속도 10% 증가 → 충격 약 ${f((1.1 ** n - 1) * 100, 0)}% 증가). `
        + (nFixed ? (n === 1 ? 'n = 1은 바퀴가 턱에 부딪히는 충돌이 충격을 만든다는 모델입니다.' : 'n = 2는 턱의 둥근 면을 따라 차가 휘어 오르는 원운동(v²/R)이 충격을 만든다는 모델입니다.')
          : `n이 1에 가까우면 '부딪힘(충돌)', 2에 가까우면 '휘어 오름(원운동)'이 주된 원인이라는 해석입니다. 지금 값은 ${n < 1.3 ? '충돌 쪽에 가깝습니다' : n > 1.7 ? '원운동 쪽에 가깝습니다' : '두 효과가 섞인 중간입니다'}`
            + (nP?.ci95 ? `${inCi(nP, 1) && inCi(nP, 2) ? ' (신뢰구간이 1과 2를 모두 포함해 아직 어느 쪽인지 단정하기 어렵습니다)' : inCi(nP, 1) ? ' (신뢰구간이 1을 포함)' : inCi(nP, 2) ? ' (신뢰구간이 2를 포함)' : ''}. ${ciText(nP)}` : `. ${ciText(nP)}`)) });
    if (m.id !== 'power_v') {
      const pP = P('p');
      rows.push({ name: 'p (질량 지수)', value: f(p),
        text: pP ? `무게를 2배로 하면 충격이 2ᵖ = ${f(2 ** p)}배가 됩니다. ${p < -0.05 ? '음수이므로 무거울수록 덜 흔들립니다(같은 충격을 더 큰 질량이 나눠 받음).' : p > 0.05 ? '양수이므로 무거울수록 더 흔들립니다.' : '0에 가까워 무게는 흔들림에 거의 영향이 없습니다.'}${inCi(pP, 0) ? ' 다만 신뢰구간이 0을 포함하므로 "무게 효과가 있다"고 단정하긴 이릅니다.' : ''} ${ciText(pP)}`
          : '질량이 한 단계뿐이라 질량 효과는 계산하지 않았습니다(p = 0으로 둠). 무게추를 2단계 이상으로 바꿔 측정하면 생깁니다.' });
    }
  } else {
    const sq = m.kind === 'quadratic';
    rows.push({ name: 'a (절편)', value: f(c.a, 3), text: `속도가 0일 때 식이 예측하는 충격입니다. 실제로는 0이어야 하므로 0에서 많이 벗어나면 이 식은 느린 속도 쪽 예측이 부정확합니다. ${ciText(P('a'))}` });
    rows.push({ name: `b (${sq ? 'v² 계수' : '기울기'})`, value: f(c.b, 3), text: sq ? `속도의 제곱이 1 늘 때 충격이 ${f(c.b, 3)} g 늘어납니다(예: 0.5 → 0.7 m/s면 v²가 0.24 늘어 약 ${f(c.b * 0.24, 3)} g 증가). ${ciText(P('b'))}` : `속도가 0.1 m/s 빨라질 때마다 충격이 약 ${f(c.b * 0.1, 3)} g 늘어납니다. ${ciText(P('b'))}` });
    if (P('c')) rows.push({ name: 'c (질량 계수)', value: f(c.c, 3), text: `무게가 100 g 늘 때 충격이 ${f(c.c * 0.1, 3)} g ${c.c < 0 ? '줄어듭니다' : '늘어납니다'}. ${ciText(P('c'))}` });
  }
  return rows;
}

export default function ModelExplain({ model, comparison, aLimit, vmax }: { model: ModelResult; comparison: ModelComparison; aLimit: number | null; vmax: number | null }) {
  const rows = paramRows(model);
  const best = comparison.models.find(m => m.id === comparison.best);
  const fitRows: Row[] = [
    { name: 'R² (결정계수)', value: model.r2.toFixed(3), text: `0~1 사이 값으로, 1에 가까울수록 식의 곡선이 측정한 점들을 잘 지나갑니다. ${model.r2 >= 0.9 ? '아주 잘 맞습니다.' : model.r2 >= 0.7 ? '대체로 맞습니다.' : '잘 맞지 않습니다 – 조건을 늘리거나 이상한 회차를 확인하세요.'}${model.exact_fit ? ' 단, 점 수가 매개변수 수와 같아서 1이 나오는 것은 의미가 약합니다.' : ''}` },
    { name: 'RMSE (평균 오차)', value: `${model.rmse.toFixed(3)} g`, text: '식이 예측한 충격과 실제 측정값의 차이를 평균 낸 크기입니다. 작을수록 좋습니다.' },
    { name: '교차검증 RMSE', value: model.loocv_rmse == null ? '—' : `${model.loocv_rmse.toFixed(3)} g`, text: `측정 조건 하나를 빼고 식을 만든 뒤, 뺀 조건을 얼마나 잘 맞히는지 본 오차입니다. '처음 보는 조건을 예측하는 실력'이라 식을 고르는 가장 중요한 기준입니다.${model.loocv_rmse == null ? ' 조건(속도·질량)이 더 있어야 계산됩니다.' : ''}` },
    { name: 'AIC', value: model.aic == null ? '—' : model.aic.toFixed(1), text: '잘 맞는 정도와 식의 복잡함(매개변수 수)을 함께 따진 점수입니다. 값 자체는 의미가 없고, 후보끼리 비교해 작은 쪽이 좋습니다.' },
  ];
  return <div className="exp-explain">
    <h3>관계식 항목 설명 – {model.name}</h3>
    <p className="exp-formula"><b>{model.formula}</b> · Δa = 평소(1 g)보다 더 받은 위아래 가속도(g), v = 방지턱 통과 속도(m/s, 1초에 가는 거리){model.kind === 'power' && model.id !== 'power_v' ? ', m = 차 전체 질량(kg)' : ''}</p>
    <p className="exp-hint">{model.explain}{best && best.id !== model.id ? ` (교차검증으로는 '${best.name}'이 더 좋다고 나옵니다.)` : ''}</p>
    <dl>{rows.map(r => <div key={r.name}><dt>{r.name}<b>{r.value}</b></dt><dd>{r.text}</dd></div>)}</dl>
    <h4>식이 얼마나 잘 맞나</h4>
    <dl>{fitRows.map(r => <div key={r.name}><dt>{r.name}<b>{r.value}</b></dt><dd>{r.text}</dd></div>)}</dl>
    {aLimit != null && <p className="exp-hint"><b>v_max {vmax == null ? '—' : `${vmax.toFixed(3)} m/s`}</b>: 이 식으로 계산한 '충격이 허용 기준(평소보다 +{(aLimit - 1).toFixed(2)} g)을 넘지 않는 가장 빠른 속도'입니다. 식 검증 탭에서 이 속도 근처로 실제 측정해 맞는지 확인하세요.</p>}
  </div>;
}
