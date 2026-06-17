import { useState } from 'react'

function fmt(n, d = 2) {
  return n == null || Number.isNaN(n) ? '—' : Number(n).toFixed(d)
}

// 比赛 λ 解读: λ = exp(μ) · 攻主 / 防客 · exp(γ·¬neutral), 客队无 γ
function LambdaBreakdown({ d }) {
  if (d.home_attack == null || d.away_defense == null || d.global_mu == null) return null
  const expMu = Math.exp(d.global_mu)
  const hostFactor = d.neutral ? 1 : Math.exp(d.global_gamma ?? 0)
  const lamH = expMu * d.home_attack / d.away_defense * hostFactor
  const lamA = expMu * d.away_attack / d.home_defense
  return (
    <div className="bg-white rounded p-2 border border-slate-200 text-[11px] leading-relaxed mt-1">
      <div className="font-medium text-slate-600 mb-1">进球率 λ 怎么算出来的</div>
      <div className="text-slate-500">
        基础进球率 exp(μ) = <b>{expMu.toFixed(2)}</b>
        {d.global_gamma != null && <> · 主场系数 exp(γ) = <b>{Math.exp(d.global_gamma).toFixed(2)}</b>（中立场不计）</>}
      </div>
      <div className="text-slate-500 mt-0.5">
        λ(主) = {expMu.toFixed(2)} × {fmt(d.home_attack, 3)} ÷ {fmt(d.away_defense, 3)}
        {!d.neutral && <> × {Math.exp(d.global_gamma ?? 0).toFixed(2)}</>} ≈ <b className="text-emerald-600">{lamH.toFixed(2)}</b>
      </div>
      <div className="text-slate-500">
        λ(客) = {expMu.toFixed(2)} × {fmt(d.away_attack, 3)} ÷ {fmt(d.home_defense, 3)} ≈ <b className="text-rose-500">{lamA.toFixed(2)}</b>
      </div>
      <div className="text-slate-400 mt-1 italic">λ 越大期望进球越多 → 胜平负与比分概率由此经 Poisson 采样得出</div>
    </div>
  )
}

// 驱动因素可展开面板. P1-6: elo + DC 攻防参数(λ 可解释); 伤病/天气/海拔留 P2.
// drivers 来自 API: 比赛模式含 home_/away_ 攻防; 球队模式含单值 attack/defense.
export default function DriversPanel({ drivers, title = '为什么是这个概率？(模型依据)' }) {
  const [open, setOpen] = useState(false)
  if (!drivers) return null
  const pending = drivers.data_status === 'pending'
  const isMatch = drivers.home_attack != null || drivers.away_attack != null
  const isTeam = drivers.attack != null

  return (
    <div className="border border-slate-200 rounded-lg bg-slate-50">
      <button onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between px-4 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-100">
        <span>{title}</span>
        <span className="text-slate-400">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="px-4 pb-3 text-xs text-slate-600 space-y-2">
          {/* 实力(Elo) */}
          {drivers.elo_gap != null && (
            <div>实力差(Elo): <b className={drivers.elo_gap >= 0 ? 'text-emerald-600' : 'text-rose-600'}>
              {drivers.elo_gap > 0 ? '+' : ''}{drivers.elo_gap.toFixed(0)}
            </b> {drivers.elo_gap >= 0 ? '主队占优' : '客队占优'}</div>
          )}
          {drivers.home_elo != null && drivers.away_elo != null && (
            <div>主/客 Elo: {drivers.home_elo.toFixed(0)} / {drivers.away_elo.toFixed(0)}</div>
          )}
          {drivers.elo_gap_vs_avg != null && (
            <div>Elo vs 48 队均值: {drivers.elo_gap_vs_avg > 0 ? '+' : ''}{drivers.elo_gap_vs_avg.toFixed(0)}</div>
          )}

          {/* 场地 */}
          {drivers.neutral != null && (
            <div>场地: {drivers.neutral ? '中立场(无主场加成)' : '本土(享主场 γ)'}</div>
          )}
          {drivers.host_advantage && <div>东道主本土作战</div>}

          {/* 比赛: DC 攻防参数 + λ 拆解 */}
          {isMatch && (
            <div className="pt-1 border-t border-slate-200 space-y-1">
              <div className="font-medium text-slate-600">Dixon-Coles 攻防参数(48 队排名)</div>
              <div>主队 进攻 {fmt(drivers.home_attack, 3)}(第 {drivers.home_attack_rank ?? '—'}) ·
                防守 {fmt(drivers.home_defense, 3)}(第 {drivers.home_defense_rank ?? '—'}, 大=稳)</div>
              <div>客队 进攻 {fmt(drivers.away_attack, 3)}(第 {drivers.away_attack_rank ?? '—'}) ·
                防守 {fmt(drivers.away_defense, 3)}(第 {drivers.away_defense_rank ?? '—'})</div>
              <LambdaBreakdown d={drivers} />
            </div>
          )}

          {/* 球队: DC 攻防参数 */}
          {isTeam && (
            <div className="pt-1 border-t border-slate-200 space-y-1">
              <div className="font-medium text-slate-600">Dixon-Coles 攻防参数</div>
              <div>进攻 {fmt(drivers.attack, 3)}(48 队第 {drivers.attack_rank ?? '—'}, 大=强)</div>
              <div>防守 {fmt(drivers.defense, 3)}(第 {drivers.defense_rank ?? '—'}, 大=稳 / 少失球)</div>
            </div>
          )}

          {pending && (
            <div className="text-slate-400 italic pt-1 border-t border-slate-200">
              伤病 / 天气 / 海拔数据采集中(留 P2) · 当前依据 = Elo + Dixon-Coles 攻防参数
            </div>
          )}
        </div>
      )}
    </div>
  )
}
