import { NavLink } from 'react-router-dom'

export default function NavBar() {
  const cls = ({ isActive }) =>
    `px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
      isActive ? 'bg-indigo-600 text-white' : 'text-slate-600 hover:bg-slate-200'
    }`
  return (
    <header className="bg-white border-b border-slate-200 sticky top-0 z-10">
      <div className="max-w-6xl mx-auto px-4 h-14 flex items-center gap-2">
        <NavLink to="/" className="font-bold text-slate-900 mr-4 shrink-0">
          ⚽ WC2026 概率
        </NavLink>
        <nav className="flex gap-1">
          <NavLink to="/" end className={cls}>总览</NavLink>
          <NavLink to="/methodology" className={cls}>方法论</NavLink>
        </nav>
      </div>
    </header>
  )
}
