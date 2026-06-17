import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ReferenceLine, ResponsiveContainer,
} from 'recharts'

// 方法论: 校准曲线(reliability). x=预测概率 bin, y=实际频率; 按 variant 分线 + 对角理想线.
// 入参 calibration: [{variant, outcome, bin_center, pred_mean, actual_freq, count}, ...]
// 每个 variant×bin 有 H/D/A 三行 → 聚合平均成一点
const COLORS = { elo: '#4f46e5', dc: '#f59e0b', dcs: '#10b981' }
const LABELS = { elo: '纯 Elo', dc: 'DC', dcs: 'DC+收缩(生产)' }

export default function CalibrationChart({ calibration }) {
  if (!calibration || !calibration.length) {
    return <div className="text-sm text-slate-500 py-4 text-center">校准数据未生成(回测未跑)</div>
  }
  const variants = [...new Set(calibration.map((r) => r.variant))]
  const byVar = {}
  calibration.forEach((r) => {
    const k = `${r.variant}|${r.bin_center}`
    const agg = (byVar[k] ||= { predSum: 0, actSum: 0, n: 0 })
    agg.predSum += r.pred_mean
    agg.actSum += r.actual_freq
    agg.n += 1
  })
  const bins = [...new Set(calibration.map((r) => r.bin_center))].sort((a, b) => a - b)
  const data = bins.map((b) => {
    const row = { bin: b }
    variants.forEach((v) => {
      const agg = byVar[`${v}|${b}`]
      row[`${v}_actual`] = agg ? agg.actSum / agg.n : null
    })
    return row
  })
  return (
    <div className="h-72">
      <ResponsiveContainer>
        <LineChart data={data} margin={{ left: 0, right: 16, top: 8, bottom: 16 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
          <XAxis type="number" dataKey="bin" domain={[0, 1]}
            tick={{ fontSize: 12, fill: '#475569' }}
            tickFormatter={(v) => `${(v * 100).toFixed(0)}%`}
            label={{ value: '预测概率', position: 'insideBottom', offset: -8, fontSize: 11, fill: '#64748b' }} />
          <YAxis type="number" domain={[0, 1]}
            tick={{ fontSize: 12, fill: '#475569' }}
            tickFormatter={(v) => `${(v * 100).toFixed(0)}%`} />
          <Tooltip formatter={(v) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`)} />
          <Legend />
          <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]}
            stroke="#94a3b8" strokeDasharray="4 4"
            ifOverflow="extendDomain" />
          {variants.map((v) => (
            <Line key={v} type="monotone" dataKey={`${v}_actual`}
              name={LABELS[v] || v} stroke={COLORS[v] || '#64748b'}
              dot={{ r: 3 }} strokeWidth={2} connectNulls />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
