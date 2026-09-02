/** @type {import('tailwindcss').Config} */

// The design system, in one file, so nothing anywhere else invents a value.
//
//  * Neutrals are Tailwind `zinc`, untouched. No hand-picked greys.
//  * ONE accent (`accent`, blue-500) for primary actions and data highlights.
//  * Green and red exist only for guard rule pass/fail and payment settlement.
//  * Type scale is exactly 12/14/16/20/24/32 — the defaults are replaced, not
//    extended, so an off-scale size is a build-visible mistake.
//  * Two radii: `card` for panels, `input` for controls and badges.
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    fontSize: {
      xs: ['12px', '16px'],
      sm: ['14px', '20px'],
      base: ['16px', '24px'],
      lg: ['20px', '28px'],
      xl: ['24px', '32px'],
      '2xl': ['32px', '40px'],
    },
    borderRadius: {
      none: '0',
      input: '4px',
      card: '8px',
    },
    extend: {
      colors: {
        accent: {
          DEFAULT: '#3b82f6', // blue-500
          muted: '#1d4ed8', // blue-700
        },
        pass: '#22c55e', // green-500
        fail: '#ef4444', // red-500
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'Segoe UI', 'sans-serif'],
        mono: [
          'JetBrains Mono',
          'ui-monospace',
          'SFMono-Regular',
          'Consolas',
          'monospace',
        ],
      },
      keyframes: {
        // Motion carries information or it does not exist. These three are the
        // whole vocabulary: rules evaluating, trace lines arriving, a panel
        // taking its decided state.
        'rule-in': {
          from: { opacity: '0', transform: 'translateY(4px)' },
          to: { opacity: '1', transform: 'none' },
        },
        'trace-in': {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
        'panel-in': {
          from: { opacity: '0', transform: 'translateY(8px)' },
          to: { opacity: '1', transform: 'none' },
        },
      },
      animation: {
        'rule-in': 'rule-in 160ms ease-out both',
        'trace-in': 'trace-in 150ms ease-out both',
        'panel-in': 'panel-in 180ms ease-out both',
      },
    },
  },
  plugins: [],
}
