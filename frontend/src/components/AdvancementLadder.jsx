import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell, ErrorBar } from 'recharts'

// 球队详情: 晋级阶梯(垂直 BarChart, 6 轮 + 夺冠) + 95% 置信区间误差带
// props: steps [{round, prob, ci_low, ci_high, label}, ...](7 格, 来自 /api/tournament/{team} advancement_path)
export default function AdvancementLadder({ steps }) {
  const data = (steps || []).map((s) => ({
    label: s.label,
    prob: s.prob ?? 0,
    error: [s.ci_low ?? s.prob ?? 0, s.ci_high ?? s.prob ?? 0],   // 绝对端点 [下, 上]
    isWin: s.round === 'win',
  }))
  return (
    <div className="h-72">
      <ResponsiveContainer>
        <BarChart data={data} layout="vertical" margin={{ left: 8, right: 24, top: 8, bottom: 8 }}>
          <XAxis type="number" domain={[0, 1]} hide />
          <YAxis type="category" dataKey="label" width={76}
            tick={{ fontSize: 12, fill: '#475569' }} />
          <Tooltip
            formatter={(v, name) => name === 'error'
              ? [`±${(((v[1] ?? 0) - (v[0] ?? 0)) * 100 / 2).toFixed(1)}pp`, '95% 区间']
              : `${(v * 100).toFixed(1)}%`}
            cursor={{ fill: '#f1f5f9' }} />
          <Bar dataKey="prob" radius={[0, 4, 4, 0]} isAnimationActive={false}>
            {data.map((d, i) => (
              <Cell key={i} fill={d.isWin ? '#e11d48' : '#4f46e5'} />
            ))}
            <ErrorBar dataKey="error" stroke="#94a3b8" strokeWidth={1} width={5} opacity={0.7} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
