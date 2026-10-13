/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        bg: '#0B1220',
        surface: '#111A2E',
        card: '#16213A',
        border: '#24324F',
        text: '#E6ECF5',
        muted: '#8FA1BF',
        primary: '#F5B301',
        teal: '#2DD4BF',
        sky: '#38BDF8',
        violet: '#A78BFA',
        success: '#22C55E',
        warning: '#F59E0B',
        danger: '#EF4444',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
