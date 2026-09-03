// Shared primitives. Everything visual in the app is built from these, so the
// design system lives in three files — this one, index.css, tailwind.config.js
// — and nowhere else.

import type { ReactNode } from 'react'

export function Panel({
  title,
  icon,
  right,
  className = '',
  bodyClassName = '',
  accent = 'none',
  children,
}: {
  title: string
  icon?: ReactNode
  right?: ReactNode
  className?: string
  bodyClassName?: string
  /** The one decisive state change a panel can take. */
  accent?: 'none' | 'fail' | 'pass'
  children: ReactNode
}) {
  // Flat on the grid: no radius, no shadow, no border of its own. A decided
  // panel gets a 2px edge on the left and nothing else.
  const edge =
    accent === 'fail'
      ? 'border-l-2 border-l-fail'
      : accent === 'pass'
        ? 'border-l-2 border-l-pass'
        : ''
  return (
    <section
      className={`panel transition-colors duration-150 ease-out ${edge} ${className}`}
    >
      <header className="panel-head">
        {icon ? <span className="shrink-0 text-zinc-500">{icon}</span> : null}
        <h2 className="panel-title">{title}</h2>
        <div className="ml-auto flex shrink-0 items-center gap-2">{right}</div>
      </header>
      <div className={`panel-body ${bodyClassName}`}>{children}</div>
    </section>
  )
}

/** An identifier: monospace, selectable, click to copy. Never accent-coloured. */
export function Id({ value, className = '' }: { value: string; className?: string }) {
  return (
    <button
      type="button"
      title="Copy"
      onClick={() => void navigator.clipboard?.writeText(value)}
      className={`mono block min-w-0 max-w-full cursor-copy truncate text-left text-zinc-400 transition-colors duration-150 ease-out hover:text-zinc-100 ${className}`}
    >
      {value}
    </button>
  )
}

type Tone = 'neutral' | 'pass' | 'fail'

const TONES: Record<Tone, string> = {
  neutral: 'border-zinc-800 bg-zinc-900 text-zinc-300',
  pass: 'border-pass/40 bg-pass/10 text-pass',
  fail: 'border-fail/40 bg-fail/10 text-fail',
}

export function Badge({
  children,
  tone = 'neutral',
}: {
  children: ReactNode
  tone?: Tone
}) {
  return (
    <span
      className={`mono inline-flex h-5 shrink-0 items-center rounded-input border px-1.5 text-xs font-medium uppercase tracking-wider ${TONES[tone]}`}
    >
      {children}
    </span>
  )
}

/** A label/value pair in a definition list. Tight within, aligned across. */
export function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline gap-4 py-0.5">
      <dt className="label w-36">{label}</dt>
      <dd className="mono min-w-0 flex-1 break-words text-sm text-zinc-200">
        {children}
      </dd>
    </div>
  )
}

/**
 * An empty state is a sentence, not a void. It sits at the top-left of its
 * region in a measure that can actually be read — the copy here teaches the
 * architecture, so it gets kept and tightened rather than centred in space.
 */
export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="px-4 py-3">
      <p className="max-w-[58ch] text-sm leading-6 text-zinc-500">{children}</p>
    </div>
  )
}

/** Skeletons, never spinners: the shape of the answer while it loads. */
export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="flex flex-col gap-2 px-4 py-3">
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="skeleton h-4"
          style={{ width: `${100 - (i % 3) * 14}%` }}
        />
      ))}
    </div>
  )
}
