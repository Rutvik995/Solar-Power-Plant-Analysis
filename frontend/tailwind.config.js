/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        bg: '#F8FAFC',
        surface: '#FFFFFF',
        card: '#FFFFFF',
        border: '#E2E8F0',
        text: '#0F172A',
        muted: '#64748B',
        primary: {
          DEFAULT: '#2563EB',
          hover: '#1D4ED8',
          tint: '#EFF6FF',
        },
        'primary-hover': '#1D4ED8',
        'primary-tint': '#EFF6FF',
        success: {
          DEFAULT: '#16A34A',
          tint: '#F0FDF4',
        },
        'success-tint': '#F0FDF4',
        warning: {
          DEFAULT: '#D97706',
          tint: '#FFFBEB',
        },
        'warning-tint': '#FFFBEB',
        danger: {
          DEFAULT: '#DC2626',
          tint: '#FEF2F2',
        },
        'danger-tint': '#FEF2F2',
        chart: {
          blue: '#2563EB',
          teal: '#0D9488',
          amber: '#D97706',
          violet: '#7C3AED',
          grey: '#94A3B8',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'sans-serif'],
      },
      borderRadius: {
        DEFAULT: '8px',
        sm: '6px',
        md: '8px',
        lg: '8px',
        xl: '10px',
        '2xl': '12px',
      },
      boxShadow: {
        sm: '0 1px 2px 0 rgba(0, 0, 0, 0.05)',
        DEFAULT: '0 1px 3px 0 rgba(0, 0, 0, 0.05)',
        md: '0 2px 4px 0 rgba(0, 0, 0, 0.05)',
        lg: 'none',
        xl: 'none',
        '2xl': 'none',
      },
    },
  },
  plugins: [],
}
