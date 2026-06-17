export default function ErrorState({ message = '加载失败' }) {
  return (
    <div className="text-rose-600 text-sm py-8 text-center">{message}</div>
  )
}
