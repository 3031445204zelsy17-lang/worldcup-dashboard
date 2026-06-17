import { useState } from 'react'

// 驱动因素可展开面板. drivers 来自 API(P1 阶段多字段空 → data_status:'pending' 降级).
export default function DriversPanel({ drivers, title = '为什么是这个概率?(驱动因素)' }) {
  const [open, setOpen] = useState(false)
  if (!drivers) return null
  const pending = drivers.data_status === 'pending'
  return (
    <div className="border border-slate-200 rounded-lg bg-slate-50">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between px-4 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-100"
      >
        <span>{title}</span>
        <span className="text-slate-400">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="px-4 pb-3 text-xs text-slate-600 space-y-1.5">
          {drivers.elo_gap != null && (
            <div>实力差(Elo): <b className={drivers.elo_gap >= 0 ? 'text-emerald-600' : 'text-rose-600'}>
              {drivers.elo_gap > 0 ? '+' : ''}{drivers.elo_gap.toFixed(0)}
            </b></div>
          )}
          {drivers.home_elo != null && drivers.away_elo != null && (
            <div>主/客 Elo: {drivers.home_elo.toFixed(0)} / {drivers.away_elo.toFixed(0)}</div>
          )}
          {drivers.elo_gap_vs_avg != null && (
            <div>Elo vs 48 队均值: {drivers.elo_gap_vs_avg > 0 ? '+' : ''}{drivers.elo_gap_vs_avg.toFixed(0)}</div>
          )}
          {drivers.neutral != null && (
            <div>场地: {drivers.neutral ? '中立场' : '本土(享主场 γ)'}</div>
          )}
          {drivers.host_advantage && <div>东道主本土作战</div>}
          {pending && (
            <div className="text-slate-400 italic pt-1">
              伤病 / 天气 / 海拔数据采集中(留 P2)
            </div>
          )}
        </div>
      )}
    </div>
  )
}
