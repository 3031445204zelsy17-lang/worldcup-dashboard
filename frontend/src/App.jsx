import { Outlet } from 'react-router-dom'
import NavBar from './components/NavBar.jsx'

export default function App() {
  return (
    <div className="min-h-screen flex flex-col bg-slate-50 text-slate-900">
      <NavBar />
      <main className="flex-1 w-full max-w-6xl mx-auto px-4 py-6">
        <Outlet />
      </main>
      <footer className="border-t border-slate-200 bg-white">
        <div className="max-w-6xl mx-auto px-4 py-4 text-xs text-slate-500">
          分析工具, 非博彩建议 · 概率有不确定性 · 数据来自 martj42/international_results
        </div>
      </footer>
    </div>
  )
}
