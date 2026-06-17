// API 客户端 —— dev 走 vite proxy(同源 /api→8001); 生产注入 VITE_API_BASE
export const API_BASE = import.meta.env.VITE_API_BASE ?? ''

export async function fetchJson(path) {
  const res = await fetch(API_BASE + path)
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new Error(`API ${res.status}: ${path} ${detail}`)
  }
  return res.json()
}

// 队名/比赛 key 含空格与 |, 进 URL 须编码
export const enc = encodeURIComponent
