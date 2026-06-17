export default function Spinner({ label = '加载中…' }) {
  return (
    <div className="flex items-center gap-2 text-slate-500 text-sm py-8 justify-center">
      <span className="inline-block w-4 h-4 border-2 border-slate-300 border-t-indigo-600 rounded-full animate-spin" />
      {label}
    </div>
  )
}
