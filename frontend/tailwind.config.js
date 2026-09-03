/** @type {import('tailwindcss').Config} */

// The design system, in one file, so nothing anywhere else invents a value.
//
//  * Neutrals are Tailwind `zinc`, untouched. No hand-picked greys.
//  * ONE accent (`accent`) and it appears only on primary actions and the focus
//    ring. Not on brand marks, not on ids, not on status.
//  * Green and red exist only for guard rule pass/fail and payment settlement.
//    They are functional, never decorative.
//  * Type scale is exactly 12/14/16/20/24/32 — the defaults are replaced, not
//    extended, so an off-scale size is a build-visible mistake.
//  * Two radii, both small: `input` for controls and badges, `card` for the one
//    remaining floated surface (the demo result popover). Panels have none.
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    fontSize: {
      xs: ['12px', '16px'],
      sm: ['14px', '20px'],
      base: ['16px', '24px'],
      lg: ['20px', '28px'],
      xl: ['24px', '32px'],
      '2xl': ['32px', '36px'],
    },
    borderRadius: {
      none: '0',
      input: '4px',
      card: '6px',
      full: '9999px',
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
        // whole vocabulary: rules landing one after another as a decision
        // renders, trace lines arriving, a region taking a decided state.
        'rule-in': {
          from: { opacity: '0', transform: 'translateY(3px)' },
          to: { opacity: '1', transform: 'none' },
        },
        'trace-in': {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
        'panel-in': {
          from: { opacity: '0', transform: 'translateY(4px)' },
          to: { opacity: '1', transform: 'none' },
        },
      },
      animation: {
        'rule-in': 'rule-in 150ms ease-out both',
        'trace-in': 'trace-in 150ms ease-out both',
        'panel-in': 'panel-in 150ms ease-out both',
      },
      transitionTimingFunction: {
        DEFAULT: 'cubic-bezier(0, 0, 0.2, 1)',
      },
    },
  },
  plugins: [],
}
