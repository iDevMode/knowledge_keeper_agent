/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,jsx}",
  ],
  theme: {
    extend: {
      colors: {
        parchment: {
          50: '#faf8f5',
          100: '#f5f0ea',
          200: '#e8e0d4',
          300: '#d4cabb',
          400: '#b8a99a',
          500: '#96877a',
          600: '#7a6d62',
          700: '#5e5349',
        },
        keeper: {
          50: '#e6f5f3',
          100: '#b3e0db',
          200: '#80ccc3',
          300: '#4db8ab',
          400: '#2a9d8f',
          500: '#1b6b61',
          600: '#165a52',
          700: '#114a43',
          800: '#0d3a34',
          900: '#0a3832',
        },
        flag: {
          100: '#fef3c7',
          200: '#fde68a',
          300: '#fcd34d',
          400: '#fbbf24',
          500: '#f59e0b',
          600: '#d97706',
        },
        ink: {
          DEFAULT: '#3d3530',
          light: '#5e5349',
          muted: '#96877a',
          heading: '#1a1614',
        },
      },
      fontFamily: {
        display: ['"DM Serif Display"', 'Georgia', 'serif'],
        sans: ['"DM Sans"', 'system-ui', 'sans-serif'],
      },
      keyframes: {
        'message-in': {
          '0%': { opacity: '0', transform: 'translateY(12px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        'bounce-dot': {
          '0%, 80%, 100%': { transform: 'translateY(0)' },
          '40%': { transform: 'translateY(-6px)' },
        },
        'page-in': {
          '0%': { opacity: '0', transform: 'translateY(20px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        'check-draw': {
          '0%': { strokeDashoffset: '24' },
          '100%': { strokeDashoffset: '0' },
        },
      },
      animation: {
        'message-in': 'message-in 350ms ease-out forwards',
        'bounce-dot': 'bounce-dot 1.2s infinite ease-in-out',
        'page-in': 'page-in 600ms ease-out forwards',
        'check-draw': 'check-draw 500ms ease-out forwards',
      },
    },
  },
  plugins: [],
}
