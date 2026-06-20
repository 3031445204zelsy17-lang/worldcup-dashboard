import { useEffect, useState } from 'react'
import { fetchJson } from './api.js'

/** 通用数据 hook: useApi(path) → {data, error, loading}. path 变自动重取. */
export function useApi(path) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    let alive = true
    setLoading(true)
    setError(null)
    fetchJson(path)
      .then((d) => { if (alive) { setData(d); setLoading(false) } })
      .catch((e) => { if (alive) { setError(e); setLoading(false) } })
    return () => { alive = false }
  }, [path])
  return { data, error, loading }
}

/** 轮询 hook: usePollingApi(path, intervalMs) → {data, error}.
 * 立即取一次 + 每 intervalMs 重取; path 变化重置; 卸载 clearInterval. 用于 P2-1 实时胜率曲线. */
export function usePollingApi(path, intervalMs = 30000) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  useEffect(() => {
    let alive = true
    const poll = () => fetchJson(path)
      .then((d) => { if (alive) { setData(d); setError(null) } })
      .catch((e) => { if (alive) setError(e) })
    poll()                                              // 立即取一次(不等首个 interval)
    const timer = setInterval(poll, intervalMs)
    return () => { alive = false; clearInterval(timer) }
  }, [path, intervalMs])
  return { data, error }
}
