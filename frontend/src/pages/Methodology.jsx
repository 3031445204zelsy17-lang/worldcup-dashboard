import { useApi } from '../hooks.js'
import Spinner from '../components/Spinner.jsx'
import ErrorState from '../components/ErrorState.jsx'
import CalibrationChart from '../components/CalibrationChart.jsx'

const VARIANT_LABEL = { elo: '纯 Elo', dc: 'Dixon-Coles', dcs: 'DC + 收缩' }

export default function Methodology() {
  const { data, error, loading } = useApi('/api/methodology')
  if (loading) return <Spinner />
  if (error) return <ErrorState message={`加载失败: ${error.message}`} />
  if (!data) return null
  const acc = data.accuracy

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

      <section className="bg-white rounded-xl border border-slate-200 p-4">
        <h2 className="font-bold text-slate-900 mb-1">历史准确率</h2>
        <p className="text-xs text-slate-500 mb-3">
          {acc
            ? `2018 + 2022 世界杯 ${acc.n_matches} 场赛前 walk-forward 预测 vs 实际 · 目标区间 ${acc.target_range}`
            : '回测数据未生成(运行 backend.models.backtest)'}
        </p>
        {acc && (
          <div className="grid grid-cols-3 gap-3">
            {['elo', 'dc', 'dcs'].map((v) => {
              const a = acc.variants[v]
              const prod = acc.production_variant === v
              return (
                <div key={v}
                  className={`rounded-lg p-3 border ${prod ? 'border-emerald-300 bg-emerald-50' : 'border-slate-200 bg-slate-50'}`}>
                  <div className="text-xs font-medium text-slate-600">
                    {VARIANT_LABEL[v]}{prod && ' ✦ 生产'}
                  </div>
                  <div className="text-2xl font-bold text-slate-900 mt-1">{(a.accuracy * 100).toFixed(1)}%</div>
                  <div className="text-xs text-slate-500">准确率</div>
                  <div className="text-xs text-slate-500 mt-1">Brier {a.brier.toFixed(3)}</div>
                </div>
              )
            })}
          </div>
        )}
      </section>

      <section className="bg-white rounded-xl border border-slate-200 p-4">
        <h2 className="font-bold text-slate-900 mb-1">校准曲线</h2>
        <p className="text-xs text-slate-500 mb-3">预测概率 vs 实际频率 —— 越贴对角虚线越准</p>
        <CalibrationChart calibration={data.calibration} />
      </section>
    </div>
  )
}
