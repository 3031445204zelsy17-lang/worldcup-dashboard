import { usePollingApi } from '../hooks.js'
import { enc } from '../api.js'
import Spinner from './Spinner.jsx'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts'

/**
 * P2-1 赛中实时胜率曲线.
 * 轮询 /api/matches/{key}/live(每 30s) → predictions 时间线 → 三线(主胜/平/客胜)随分钟.
 * data_source: live_poisson(worker 实时曲线) / pre_match(降级赛前单点) / unavailable.
 * 转折点标注(进球/红牌)留 P2-2(events 端点).
 */
export default function LiveWinProbabilityCurve({ matchKey }) {
  const { data, error } = usePollingApi(`/api/matches/${enc(matchKey)}/live`, 30000)

  if (error) return (
    <section className="bg-white rounded-xl border border-slate-200 p-4 text-sm text-rose-500">
      实时数据加载失败
    </section>
  )
  if (!data) return (
    <section className="bg-white rounded-xl border border-slate-200 p-4">
      <h2 className="font-bold text-slate-900 mb-3">实时胜率曲线</h2>
      <Spinner />
    </section>
  )

  const timeline = data.win_prob_timeline || []
  const live = data.live_win_prob || {}
  const isLive = data.is_live
  const source = data.data_source

  // 曲线数据: timeline minute→三概率(%); pre_match 无 timeline → 赛前单点 minute=0
  const chartData = timeline.length
    ? timeline.map((t) => ({
        minute: t.minute,
        主胜: +(t.home_win * 100).toFixed(1),
        平: +(t.draw * 100).toFixed(1),
        客胜: +(t.away_win * 100).toFixed(1),
      }))
    : (live.home_win != null
        ? [{ minute: 0,
            主胜: +(live.home_win * 100).toFixed(1),
            平: +(live.draw * 100).toFixed(1),
            客胜: +(live.away_win * 100).toFixed(1) }]
        : [])

  const badge = isLive
    ? { text: `● LIVE ${data.minute}'`, cls: 'bg-rose-100 text-rose-600' }
    : source === 'pre_match'
      ? { text: '赛前', cls: 'bg-slate-100 text-slate-500' }
      : { text: '待赛', cls: 'bg-slate-100 text-slate-400' }

  return (
    <section className="bg-white rounded-xl border border-slate-200 p-4">
      <div className="flex items-center justify-between mb-2">
        <h2 className="font-bold text-slate-900">实时胜率曲线</h2>
        <span className={`text-xs px-2 py-0.5 rounded-full ${badge.cls}`}>{badge.text}</span>
      </div>

      {data.current_score && (
        <div className="text-center text-2xl font-bold text-slate-900 mb-2 tabular-nums">
          {data.current_score.home} <span className="text-slate-300 mx-1">:</span> {data.current_score.away}
        </div>
      )}

      {chartData.length > 0 ? (
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={chartData} margin={{ top: 5, right: 10, bottom: 5, left: -10 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
            <XAxis dataKey="minute" unit="'" domain={[0, 90]}
                   ticks={[0, 15, 30, 45, 60, 75, 90]} tick={{ fontSize: 11 }} />
            <YAxis domain={[0, 100]} unit="%" tick={{ fontSize: 11 }} />
            <Tooltip formatter={(v) => `${v}%`} labelFormatter={(m) => `${m}'`} />
            <Legend />
            <Line type="monotone" dataKey="主胜" stroke="#10b981" strokeWidth={2} dot={false} />
            <Line type="monotone" dataKey="平" stroke="#fbbf24" strokeWidth={2} dot={false} />
            <Line type="monotone" dataKey="客胜" stroke="#fb7185" strokeWidth={2} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      ) : (
        <div className="text-center text-sm text-slate-400 py-8">赛中开始实时更新(每 30s 轮询)</div>
      )}

      {live.home_win != null && (
        <div className="flex justify-around mt-2 text-xs font-semibold">
          <span className="text-emerald-600">主胜 {(live.home_win * 100).toFixed(0)}%</span>
          <span className="text-amber-600">平 {(live.draw * 100).toFixed(0)}%</span>
          <span className="text-rose-500">客胜 {(live.away_win * 100).toFixed(0)}%</span>
        </div>
      )}
      {source === 'live_poisson' && (
        <div className="text-xs text-slate-400 mt-1 text-center">
          剩余时间 Poisson + game state 修正 · 每 30s 轮询
        </div>
      )}
    </section>
  )
}
