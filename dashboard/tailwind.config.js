/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"DM Sans"', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'monospace'],
      },
      colors: {
        void: '#07090E',
        surface: {
          DEFAULT: '#0D1017',
          raised: '#131720',
          overlay: '#1A1F2B',
        },
        edge: {
          DEFAULT: '#1E2433',
          hover: '#2A3246',
          active: '#364059',
        },
        accent: {
          DEFAULT: '#00E5CC',
          dim: '#00E5CC33',
          glow: '#00E5CC18',
        },
        signal: {
          amber: '#FFBF47',
          coral: '#FF6B6B',
          sky: '#5BA4F5',
          lime: '#7DDB80',
        },
        ink: {
          DEFAULT: '#E8ECF4',
          muted: '#8892A6',
          faint: '#4A5568',
        },
      },
      boxShadow: {
        glow: '0 0 20px -4px rgba(0, 229, 204, 0.15)',
        'glow-sm': '0 0 10px -2px rgba(0, 229, 204, 0.1)',
        card: '0 1px 3px rgba(0, 0, 0, 0.3), 0 0 0 1px rgba(30, 36, 51, 0.5)',
      },
      backgroundImage: {
        'dot-grid': 'radial-gradient(circle, #1E2433 1px, transparent 1px)',
      },
      backgroundSize: {
        'dot-grid': '24px 24px',
      },
      animation: {
        'fade-in': 'fadeIn 0.5s ease-out forwards',
        'slide-up': 'slideUp 0.5s ease-out forwards',
        'pulse-subtle': 'pulseSubtle 2s ease-in-out infinite',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideUp: {
          '0%': { opacity: '0', transform: 'translateY(8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        pulseSubtle: {
          '0%, 100%': { opacity: '0.4' },
          '50%': { opacity: '0.7' },
        },
      },
    },
  },
  plugins: [],
}
