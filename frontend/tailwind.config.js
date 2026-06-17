/** @type {import('tailwindcss').Config} */
// 浅色专业调性: slate 底 + indigo(主)/emerald(胜·涨)/amber(平)/rose(负·跌) 数据色
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
