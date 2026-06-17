import { flagUrl } from '../data/flags.js'

// 队名 → flagcdn 圆角小旗. 未知队(淘汰赛占位)显示空占位.
export default function TeamFlag({ team, w = 24 }) {
  const url = flagUrl(team, Math.round(w * 2))   // 2x 高清
  if (!url) return <span className="inline-block align-middle" style={{ width: w }} />
  return (
    <img
      src={url} alt={team} loading="lazy"
      className="inline-block rounded-[3px] object-cover align-middle shadow-sm"
      style={{ width: w, height: Math.round(w * 0.66) }}
    />
  )
}
