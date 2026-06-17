// 全局免责声明(P1-6 透明度层). 挂 App footer, 每页可见.
// 内容稳定不走 API —— 与 /api/methodology 的 disclaimer 文案对齐但更详尽.
export default function Disclaimer() {
  return (
    <footer className="mt-8 border-t border-slate-200 pt-4 pb-6 text-[11px] leading-relaxed text-slate-400 space-y-1">
      <p>
        ⚠️ <b className="text-slate-500">分析工具, 非博彩建议。</b>
        所有概率都带不确定性(见置信区间 ±), 足球预测准确率天花板约 <b className="text-slate-500">53–55%</b> ——
        本仪表盘追求透明与可解释, 不追求"最准"。
      </p>
      <p>
        数据来源: martj42 国际赛历史(49417 场 / 336 队) → Elo 实力分 → Dixon-Coles 攻防模型(κ=20 收缩) →
        Monte Carlo 10000 次锦标赛模拟。概率由后台 worker 赛后自动重算, 用户访问不消耗外部 API 额度。
      </p>
      <p>
        伤病 / 天气 / 海拔等实时因素暂未纳入(后续阶段); 历史回测见
        <a href="/methodology" className="text-indigo-400 hover:underline ml-0.5">方法论页</a>。
        © 2026 World Cup Probability Dashboard.
      </p>
    </footer>
  )
}
