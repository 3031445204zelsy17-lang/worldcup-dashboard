// 水平概率条(夺冠榜行 / 胜平负段 复用).
// value: 0-1; color: tailwind bg-* 类名; label?: 左标签; rightText?: 右侧文字(默认百分比)
export default function ProbabilityBar({
  value, color = 'bg-indigo-500', label, rightText, height = 'h-5',
}) {
  const pct = Math.max(0, Math.min(1, value || 0)) * 100
  return (
    <div className="flex items-center gap-2">
      {label !== undefined && (
        <div className="w-24 text-sm text-slate-600 truncate shrink-0">{label}</div>
      )}
      <div className={`flex-1 ${height} bg-slate-100 rounded overflow-hidden`}>
        <div
          className={`${color} ${height} rounded transition-all duration-500`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="w-14 text-right text-xs tabular-nums text-slate-700 shrink-0">
        {rightText ?? `${pct.toFixed(1)}%`}
      </div>
    </div>
  )
}
