import { useApi } from '../hooks.js'
import Spinner from '../components/Spinner.jsx'
import ErrorState from '../components/ErrorState.jsx'
import CalibrationChart from '../components/CalibrationChart.jsx'

const VARIANT_LABEL = { elo: '纯 Elo', dc: 'Dixon-Coles', dcs: 'DC + 收缩' }
const pct = (x) => (x == null ? '—' : `${(x * 100).toFixed(1)}%`)

export default function Methodology() {
  const { data, error, loading } = useApi('/api/methodology')
  if (loading) return <Spinner />
  if (error) return <ErrorState message={`加载失败: ${error.message}`} />
  if (!data) return null
  const acc = data.accuracy
  const lim = acc?.limitations

  return (
    <div className="space-y-5 max-w-3xl mx-auto">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">方法论</h1>
        <p className="text-sm text-slate-600 mt-1">{data.disclaimer}</p>
      </div>

      <section className="bg-white rounded-xl border border-slate-200 p-4">
        <h2 className="font-bold text-slate-900 mb-3">预测管线</h2>
        <div className="flex items-center gap-2 text-sm flex-wrap">
          {data.algorithm_chain.map((s, i) => (
            <span key={i} className="flex items-center gap-2">
              <span className="px-3 py-1.5 bg-indigo-50 text-indigo-700 rounded-md font-medium">{s}</span>
              {i < data.algorithm_chain.length - 1 && <span className="text-slate-300">→</span>}
            </span>
          ))}
        </div>
        <div className="mt-3 text-xs text-slate-500">{data.data_window}</div>
      </section>

      {acc && (
        <section className="bg-white rounded-xl border border-slate-200 p-4">
          <h2 className="font-bold text-slate-900 mb-1">历史准确率</h2>
          <p className="text-xs text-slate-500 mb-3">
            {acc.n_matches} 场赛前 walk-forward(2018+2022) · 目标区间 {acc.target_range}
          </p>
          <div className="grid grid-cols-3 gap-3">
            {['elo', 'dc', 'dcs'].map((v) => {
              const a = acc.variants[v]
              const prod = acc.production_variant === v
              return (
                <div key={v}
                  className={`rounded-lg p-3 border ${prod ? 'border-emerald-300 bg-emerald-50' : 'border-slate-200 bg-slate-50'}`}>
                  <div className="text-xs font-medium text-slate-600">{VARIANT_LABEL[v]}{prod && ' ✦ 生产'}</div>
                  <div className="text-2xl font-bold text-slate-900 mt-1">{(a.accuracy * 100).toFixed(1)}%</div>
                  <div className="text-xs text-slate-500">准确率</div>
                  <div className="text-xs text-slate-500 mt-1">Brier {a.brier.toFixed(3)}</div>
                </div>
              )
            })}
          </div>
          {acc.per_year && (
            <div className="mt-3 pt-3 border-t border-slate-100">
              <div className="text-xs text-slate-500 mb-1.5">分年准确率(2022 对 DC 系列明显更难)</div>
              <div className="grid grid-cols-2 gap-2 text-xs">
                {Object.entries(acc.per_year).map(([yr, vs]) => (
                  <div key={yr} className="bg-slate-50 rounded p-2">
                    <div className="font-medium text-slate-600 mb-1">{yr} 世界杯</div>
                    <div className="space-y-0.5 text-slate-500">
                      {['elo', 'dc', 'dcs'].map((v) => (
                        <div key={v} className="flex justify-between">
                          <span>{VARIANT_LABEL[v]}</span>
                          <span className="tabular-nums">{pct(vs[v].accuracy)}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>
      )}

      {lim && (
        <section className="bg-amber-50 rounded-xl border border-amber-200 p-4">
          <h2 className="font-bold text-slate-900 mb-1">已知局限(诚实说明)</h2>
          <p className="text-xs text-slate-500 mb-3">我们追求透明可解释, 不追"最准" —— 以下是模型的结构性盲点</p>
          <div className="space-y-3 text-sm text-slate-700">
            <div className="flex gap-2">
              <span className="shrink-0">🎯</span>
              <div>
                <b>平局是结构盲点。</b>{acc.n_matches} 场里实际平局占 {pct(lim.draw_actual_rate)}, 但生产模型(DC+收缩)
                把平局预测对(最高概率项)的比例仅 <b className="text-rose-600">{pct(lim.prod_draw_recall)}</b>。
                平局极少成为单场最高概率项 —— 这是所有足球模型的常态(非概率校准问题, 不可简单修正)。
              </div>
            </div>
            <div className="flex gap-2">
              <span className="shrink-0">📊</span>
              <div>
                <b>准确率天花板约 {acc.target_range}。</b>
                纯 Elo {pct(acc.variants.elo.accuracy)}(最鲁棒, 无比分分布) / DC {pct(acc.variants.dc.accuracy)} /
                DC+收缩 {pct(acc.variants.dcs.accuracy)}(生产, 富输出) —— 全在足球预测正常区间,
                足球本身随机性大, 60%+ 极难达到。
              </div>
            </div>
          </div>
        </section>
      )}

      <section className="bg-white rounded-xl border border-slate-200 p-4">
        <h2 className="font-bold text-slate-900 mb-1">校准曲线</h2>
        <p className="text-xs text-slate-500 mb-3">预测概率 vs 实际频率 —— 越贴对角虚线越准(纯 Elo 最贴)</p>
        <CalibrationChart calibration={data.calibration} />
      </section>
    </div>
  )
}
