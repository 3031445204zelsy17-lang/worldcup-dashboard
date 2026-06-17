import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import App from './App.jsx'
import Overview from './pages/Overview.jsx'
import TeamDetail from './pages/TeamDetail.jsx'
import MatchDetail from './pages/MatchDetail.jsx'
import Methodology from './pages/Methodology.jsx'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<App />}>
          <Route index element={<Overview />} />
          {/* 队名/比赛 key 含空格与 |, 前端用 encodeURIComponent 编码进 URL */}
          <Route path="team/:name" element={<TeamDetail />} />
          <Route path="match/:key" element={<MatchDetail />} />
          <Route path="methodology" element={<Methodology />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
)
