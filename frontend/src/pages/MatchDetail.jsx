import { useParams, Link } from 'react-router-dom'
import { useApi } from '../hooks.js'
import { enc } from '../api.js'
import Spinner from '../components/Spinner.jsx'
import ErrorState from '../components/ErrorState.jsx'
import TeamFlag from '../components/TeamFlag.jsx'
import ProbabilityBar from '../components/ProbabilityBar.jsx'
import DriversPanel from '../components/DriversPanel.jsx'

export default function MatchDetail() {
  const { key } = useParams()
  const matchKey = decodeURIComponent(key || '')
  const { data, error, loading } = useApi(`/api/matches/${enc(matchKey)}`)

  if (loading) return <Spinner />
  if (error) return <ErrorState message={`加载失败: ${error.message}`} />
  if (!data) return null
  const m = data.match
  const p = data.prediction

  return (
    <div className="space-y-4 max-w-2xl mx-auto">
      <Link to="/" className="text-sm text-indigo-600 hover:underline">← 返回总览</Link>

      <div className="bg-white rounded-xl border border-slate-200 p-4">
        <div className="text-center text-xs text-slate-400 mb-3">
          {m.kickoff ? new Date(m.kickoff).toLocaleString('zh-CN') : m.date}
          {' · '}{m.status === 'finished' ? '已完赛' : '未赛'}
          {' · '}{m.neutral ? '中立场' : '本土'}
        </div>
        <div className="flex items-center justify-around">
          <Link to={`/team/${enc(m.home)}`} className="text-center hover:opacity-80">
            <TeamFlag team={m.home} w={48} />
            <div className="mt-1 font-bold text-slate-900 text-sm">{m.home}</div>
          </Link>
          <div className="text-3xl font-bold text-slate-400 px-4">
            {m.status === 'finished' ? `${m.home_score} : ${m.away_score}` : 'VS'}
          </div>
          <Link to={`/team/${enc(m.away)}`} className="text-center hover:opacity-80">
            <TeamFlag team={m.away} w={48} />
            <div className="mt-1 font-bold text-slate-900 text-sm">{m.away}</div>
          </Link>
        </div>
      </div>

      {p ? (
        <>
          <section className="bg-white rounded-xl border border-slate-200 p-4">
            <h2 className="font-bold text-slate-900 mb-3">赛前预测 · 胜/平/负</h2>
            <div className="flex h-8 rounded-md overflow-hidden text-white text-xs font-semibold">
              <div className="bg-emerald-500 flex items-center justify-center transition-all"
                style={{ width: `${p.home_win * 100}%` }}>
                {p.home_win > 0.12 && `${(p.home_win * 100).toFixed(0)}%`}
              </div>
              <div className="bg-amber-400 text-amber-900 flex items-center justify-center"
                style={{ width: `${p.draw * 100}%` }}>
                {p.draw > 0.12 && `${(p.draw * 100).toFixed(0)}%`}
              </div>
              <div className="bg-rose-400 flex items-center justify-center"
                style={{ width: `${p.away_win * 100}%` }}>
                {p.away_win > 0.12 && `${(p.away_win * 100).toFixed(0)}%`}
              </div>
            </div>
            <div className="flex justify-between mt-2 text-xs text-slate-500">
              <span>λ(期望进球): 主 {p.lambda_home.toFixed(2)} / 客 {p.lambda_away.toFixed(2)}</span>
              <span>主胜 / 平 / 客胜</span>
            </div>
          </section>

          <section className="bg-white rounded-xl border border-slate-200 p-4">
            <h2 className="font-bold text-slate-900 mb-3">最可能比分 Top 5</h2>
            <div className="space-y-1.5">
              {p.top_scores.slice(0, 5).map(([h, a, prob], i) => (
                <div key={i} className="flex items-center gap-2">
                  <span className="w-12 text-sm font-bold tabular-nums text-slate-700 shrink-0">{h}-{a}</span>
                  <div className="flex-1">
                    <ProbabilityBar value={prob} color="bg-indigo-400" height="h-4" rightText="" />
                  </div>
                  <span className="w-12 text-right text-xs tabular-nums text-slate-600">{(prob * 100).toFixed(1)}%</span>
                </div>
              ))}
            </div>
          </section>
        </>
      ) : (
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-sm text-amber-700 text-center">
          预测引擎未就绪(503)
        </div>
      )}

      <section className="bg-slate-50 rounded-xl border border-dashed border-slate-300 p-4 text-center text-sm text-slate-400">
        📈 实时胜率曲线(赛中)留 B 阶段
      </section>

      <DriversPanel drivers={data.drivers} />
    </div>
  )
}
