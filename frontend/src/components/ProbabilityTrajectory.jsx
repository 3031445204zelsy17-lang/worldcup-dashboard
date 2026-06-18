import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts'
import { useApi } from '../hooks.js'
import { enc } from '../api.js'
import Spinner from './Spinner.jsx'

// 球队详情: 概率轨迹折线 —— 夺冠/出线/8强概率随每次 MC 重算(赛果)的变动.
// 数据源 /api/tournament/{team}/history(snapshot 列表, 每次赛果重算一份).
const SERIES = [
  { key: 'win', label: '夺冠', color: '#e11d48' },
  { key: 'ro32', label: '出线', color: '#4f46e5' },
  { key: 'qf', label: '8 强', color: '#10b981' },
]

export default function ProbabilityTrajectory({ team }) {
  const { data, error, loading } = useApi(`/api/tournament/${enc(team)}/history`)
  if (loading) return <Spinner />
  if (error) return null
  const snaps = data?.snapshots || []
  if (snaps.length < 2) {
    return (
      <div className="text-xs text-slate-400 italic py-6 text-center">
        概率轨迹数据累积中 —— worker 每次赛果重算追加一份快照(部署后常驻自动累积,
        本地需多轮重算才有多个点)。
      </div>
    )
  }
  const chartData = snaps.map((s) => ({
    time: (s.calculated_at || '').slice(5, 16).replace('T', ' '),   // MM-DD HH:MM
    win: s.win_prob,
    ro32: s.advancement?.ro32 ?? 0,
    qf: s.advancement?.qf ?? 0,
  }))
  return (
    <div className="h-64">
      <ResponsiveContainer>
        <LineChart data={chartData} margin={{ left: 0, right: 16, top: 8, bottom: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
          <XAxis dataKey="time" tick={{ fontSize: 11, fill: '#475569' }} />
          <YAxis tick={{ fontSize: 11, fill: '#475569' }}
            tickFormatter={(v) => `${(v * 100).toFixed(0)}%`} domain={[0, 'auto']} />
          <Tooltip formatter={(v) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`)} />
          <Legend />
          {SERIES.map((s) => (
            <Line key={s.key} type="monotone" dataKey={s.key} name={s.label}
              stroke={s.color} dot={{ r: 3 }} strokeWidth={2} connectNulls />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
