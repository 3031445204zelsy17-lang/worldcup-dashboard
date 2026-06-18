import 'flag-icons/css/flag-icons.css'
import { TEAM_TO_ISO } from '../data/flags.js'

// 队名 → 本地 flag-icons CSS sprite(SVG 打包进 bundle, 不走外网 flagcdn, 代理/离线友好).
// 含 subnational: England=gb-eng, Scotland=gb-sct. 未知队(淘汰赛占位)显示空占位.
export default function TeamFlag({ team, w = 24 }) {
  const iso = TEAM_TO_ISO[team]
  if (!iso) {
    return <span className="inline-block align-middle bg-slate-100 rounded-[3px]"
      style={{ width: w, height: Math.round(w * 0.66) }} />
  }
  return (
    <span
      className={`fi fi-${iso} inline-block align-middle shadow-sm`}
      title={team}
      style={{ width: w, height: Math.round(w * 0.66), borderRadius: 3, overflow: 'hidden' }}
    />
  )
}
