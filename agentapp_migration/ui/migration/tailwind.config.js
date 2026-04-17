/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,js}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        sena: {
          bg: '#FAF9F5',
          surface: '#F5F4F0',
          shell: '#F7F6F2',
        },
        primary: {
          50: '#eff6ff',
          100: '#dbeafe',
          200: '#bfdbfe',
          300: '#93c5fd',
          400: '#60a5fa',
          500: '#3B82F6',
          600: '#2563eb',
          700: '#1d4ed8',
          800: '#1e40af',
          900: '#1e3a8a',
        },
        ink: {
          primary: '#1e293b',
          secondary: '#334155',
          muted: '#64748b',
          faint: '#94a3b8',
        },
      },
      fontWeight: {
        normal: '420',
        medium: '500',
        semibold: '600',
        bold: '700',
      },
    },
  },
  plugins: [],
}
