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
