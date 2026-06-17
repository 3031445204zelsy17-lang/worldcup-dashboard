import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useApi } from '../hooks.js'
import { enc } from '../api.js'
import Spinner from '../components/Spinner.jsx'
import ErrorState from '../components/ErrorState.jsx'
import ProbabilityBar from '../components/ProbabilityBar.jsx'
import TeamFlag from '../components/TeamFlag.jsx'

const VIEWS = [
  { key: 'win', label: '夺冠' },
  { key: 'group', label: '出线' },
  { key: 'ro16', label: '16 强' },
  { key: 'qf', label: '8 强' },
  { key: 'sf', label: '4 强' },
]

export default function Overview() {
  const [view, setView] = useState('win')
  const health = useApi('/api/health')
  const tournament = useApi(`/api/tournament?view=${view}`)
  const today = useApi('/api/matches?date=today')

  return (
    <div className="space-y-6">
      <StatusBanner health={health} />

      <section className="bg-white rounded-xl border border-slate-200 p-4">
        <div className="flex items-center justify-between mb-3 gap-2 flex-wrap">
          <h2 className="font-bold text-slate-900">锦标赛概率</h2>
          <div className="flex gap-1 flex-wrap">
            {VIEWS.map((v) => (
              <button key={v.key} onClick={() => setView(v.key)}
                className={`px-2.5 py-1 text-xs rounded-md font-medium transition-colors ${
                  view === v.key ? 'bg-indigo-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                }`}>
                {v.label}
              </button>
            ))}
          </div>
        </div>
        {tournament.loading ? <Spinner /> :
         tournament.error ? <ErrorState message="锦标赛数据加载失败" /> :
         <PowerRanking teams={tournament.data.teams} view={view} />}
      </section>

      <section className="bg-white rounded-xl border border-slate-200 p-4">
        <h2 className="font-bold text-slate-900 mb-3">今日比赛</h2>
        {today.loading ? <Spinner /> :
         today.error ? <ErrorState message="今日比赛加载失败" /> :
         today.data.count === 0 ? <div className="text-sm text-slate-500 py-4 text-center">今日无比赛</div> :
         <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
           {today.data.matches.map((m) => <MatchCard key={m.match_key} m={m} />)}
         </div>}
      </section>

      <section className="bg-white rounded-xl border border-slate-200 p-4">
        <h2 className="font-bold text-slate-900 mb-2">今日变动</h2>
        <div className="text-sm text-slate-400 italic">
          概率变动追踪留 P1-6(需历史快照)。当前最近重算:
          {' '}{health.data?.last_mc_recomputed_at || '—'}
        </div>
      </section>
    </div>
  )
}

function StatusBanner({ health }) {
  if (health.loading) return <div className="text-sm text-slate-500">状态加载中…</div>
  if (health.error || !health.data?.db_readable)
    return <div className="text-sm text-rose-600">⚠ 后端不可达,请确认 API 在 8001 运行(python -m backend.api)</div>
  const c = health.data.counts
  return (
    <div className="flex flex-wrap gap-x-5 gap-y-1 text-sm text-slate-600 bg-indigo-50 border border-indigo-100 rounded-lg px-4 py-2.5">
      <span>🏟️ {c?.matches ?? 0} 场赛程</span>
      <span>✅ {c?.matches_finished ?? 0} 场已完赛</span>
      <span>📊 {c?.tournament_probs ? Math.round(c.tournament_probs / 6) : 0} 队概率</span>
      {health.data.last_mc_recomputed_at &&
        <span>🕒 概率更新: {health.data.last_mc_recomputed_at.slice(11, 16)} UTC</span>}
    </div>
  )
}

function PowerRanking({ teams, view }) {
  return (
    <div className="space-y-1 max-h-[560px] overflow-y-auto pr-1">
      {teams.map((t, i) => (
        <Link key={t.name} to={`/team/${enc(t.name)}`}
          className="flex items-center gap-2 px-2 py-1 rounded hover:bg-slate-50 group">
          <span className="w-6 text-xs text-slate-400 tabular-nums text-right">{i + 1}</span>
          <TeamFlag team={t.name} w={22} />
          <span className="flex-1 text-sm text-slate-800 truncate group-hover:text-indigo-600">{t.name}</span>
          <div className="w-36 hidden md:block">
            <ProbabilityBar value={t.sort_value} height="h-3"
              color={view === 'win' ? 'bg-rose-500' : 'bg-indigo-500'} rightText="" />
          </div>
          <span className="w-14 text-right text-sm tabular-nums font-semibold text-slate-800">
            {(t.sort_value * 100).toFixed(1)}%
          </span>
          {view !== 'win' && (
            <span className="w-16 text-right text-xs tabular-nums text-slate-400 hidden sm:inline">
              夺冠 {(t.win_prob * 100).toFixed(1)}%
            </span>
          )}
        </Link>
      ))}
    </div>
  )
}

function MatchCard({ m }) {
  return (
    <Link to={`/match/${enc(m.match_key)}`}
      className="block border border-slate-200 rounded-lg p-3 hover:border-indigo-300 hover:shadow-sm transition">
      <div className="text-xs text-slate-400 mb-2">
        {m.kickoff
          ? new Date(m.kickoff).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
          : m.date}
        {' · '}{m.status === 'finished' ? '已完赛' : m.status === 'upcoming' ? '未赛' : m.status}
      </div>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-sm font-medium text-slate-800 min-w-0">
          <TeamFlag team={m.home} w={20} /><span className="truncate">{m.home}</span>
        </div>
        {m.status === 'finished' && <span className="text-sm font-bold tabular-nums mx-1">{m.home_score}-{m.away_score}</span>}
      </div>
      <div className="flex items-center justify-between mt-1">
        <div className="flex items-center gap-1.5 text-sm font-medium text-slate-800 min-w-0">
          <TeamFlag team={m.away} w={20} /><span className="truncate">{m.away}</span>
        </div>
      </div>
      <div className="mt-2 text-xs text-indigo-500">赛前预测 →</div>
    </Link>
  )
}
