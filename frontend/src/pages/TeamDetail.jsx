import { useParams, Link } from 'react-router-dom'
import { useApi } from '../hooks.js'
import { enc } from '../api.js'
import Spinner from '../components/Spinner.jsx'
import ErrorState from '../components/ErrorState.jsx'
import TeamFlag from '../components/TeamFlag.jsx'
import AdvancementLadder from '../components/AdvancementLadder.jsx'
import DriversPanel from '../components/DriversPanel.jsx'

export default function TeamDetail() {
  const { name } = useParams()
  const team = decodeURIComponent(name || '')
  const { data, error, loading } = useApi(`/api/tournament/${enc(team)}`)

  if (loading) return <Spinner />
  if (error) return <ErrorState message={`加载失败: ${error.message}`} />
  if (!data) return null

  const winProb = data.advancement_path.find((s) => s.round === 'win')?.prob ?? 0
  const advancement = data.advancement_path.reduce((acc, s) => { acc[s.round] = s.prob; return acc }, {})

  return (
    <div className="space-y-4">
      <Link to="/" className="text-sm text-indigo-600 hover:underline">← 返回总览</Link>

      <div className="flex items-center gap-3 bg-white rounded-xl border border-slate-200 p-4">
        <TeamFlag team={data.name} w={44} />
        <div className="min-w-0">
          <h1 className="text-xl font-bold text-slate-900 truncate">{data.name}</h1>
          <div className="text-sm text-slate-500">
            {data.group} 组 · Elo {data.elo?.toFixed(0)} · 实力排名 #{data.rank}
          </div>
        </div>
        <div className="ml-auto text-right shrink-0">
          <div className="text-xs text-slate-400">夺冠概率</div>
          <div className="text-2xl font-bold text-rose-500">{(winProb * 100).toFixed(1)}%</div>
        </div>
      </div>

      <section className="bg-white rounded-xl border border-slate-200 p-4">
        <h2 className="font-bold text-slate-900 mb-1">晋级阶梯</h2>
        <p className="text-xs text-slate-500 mb-2">各轮晋级概率(Monte Carlo 10000 次模拟)</p>
        <AdvancementLadder advancement={advancement} winProb={winProb} />
      </section>

      <section className="bg-white rounded-xl border border-slate-200 p-4">
        <h2 className="font-bold text-slate-900 mb-3">赛程</h2>
        {data.matches.length === 0 ? <div className="text-sm text-slate-500">无赛程数据</div> :
          <div className="space-y-1.5">
            {data.matches.map((m) => (
              <Link key={m.match_key} to={`/match/${enc(m.match_key)}`}
                className="flex items-center gap-2 text-sm text-slate-700 hover:text-indigo-600">
                <span className="w-20 text-xs text-slate-400 shrink-0">{m.date}</span>
                <TeamFlag team={m.home} w={18} /><span className="truncate">{m.home}</span>
                {m.status === 'finished'
                  ? <span className="font-bold tabular-nums px-1">{m.home_score}-{m.away_score}</span>
                  : <span className="text-slate-400 px-1">v</span>}
                <TeamFlag team={m.away} w={18} /><span className="truncate">{m.away}</span>
              </Link>
            ))}
          </div>}
      </section>

      <DriversPanel drivers={data.drivers} />
    </div>
  )
}
