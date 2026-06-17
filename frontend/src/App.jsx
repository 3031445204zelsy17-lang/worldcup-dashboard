import { Outlet } from 'react-router-dom'
import NavBar from './components/NavBar.jsx'
import Disclaimer from './components/Disclaimer.jsx'

export default function App() {
  return (
    <div className="min-h-screen flex flex-col bg-slate-50 text-slate-900">
      <NavBar />
      <main className="flex-1 w-full max-w-6xl mx-auto px-4 py-6">
        <Outlet />
        <Disclaimer />
      </main>
    </div>
  )
}
