import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts'

const ROUNDS = [
  { key: 'group', label: '小组出线' },
  { key: 'ro32', label: '32 强' },
  { key: 'ro16', label: '16 强' },
  { key: 'qf', label: '8 强' },
  { key: 'sf', label: '半决赛' },
  { key: 'final', label: '决赛' },
]

// 球队详情: 晋级阶梯(垂直 BarChart, 6 轮 + 夺冠)
// props: advancement {group,ro32,ro16,qf,sf,final}, winProb
export default function AdvancementLadder({ advancement, winProb }) {
  const data = [...ROUNDS, { key: 'win', label: '夺冠' }].map((r) => ({
    label: r.label,
    prob: r.key === 'win' ? winProb : (advancement?.[r.key] ?? 0),
    isWin: r.key === 'win',
  }))
  return (
    <div className="h-72">
      <ResponsiveContainer>
        <BarChart data={data} layout="vertical" margin={{ left: 8, right: 24, top: 8, bottom: 8 }}>
          <XAxis type="number" domain={[0, 1]} hide />
          <YAxis type="category" dataKey="label" width={76}
            tick={{ fontSize: 12, fill: '#475569' }} />
          <Tooltip formatter={(v) => `${(v * 100).toFixed(1)}%`} cursor={{ fill: '#f1f5f9' }} />
          <Bar dataKey="prob" radius={[0, 4, 4, 0]}>
            {data.map((d, i) => (
              <Cell key={i} fill={d.isWin ? '#e11d48' : '#4f46e5'} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
