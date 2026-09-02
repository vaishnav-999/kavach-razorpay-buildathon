// Shared primitives. Everything visual in the app is built from these, so the
// design system lives in two files and nowhere else.

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
  const edge =
    accent === 'fail'
      ? 'border-l-2 border-l-fail'
      : accent === 'pass'
        ? 'border-l-2 border-l-pass'
        : ''
  return (
    <section
      className={`panel transition-colors duration-200 ease-out ${edge} ${className}`}
    >
      <header className="panel-head">
        {icon ? <span className="text-zinc-500">{icon}</span> : null}
        <h2 className="panel-title">{title}</h2>
        <div className="ml-auto flex items-center gap-2">{right}</div>
      </header>
      <div className={`panel-body ${bodyClassName}`}>{children}</div>
    </section>
  )
}

export function Mono({
  children,
  className = '',
}: {
  children: ReactNode
  className?: string
}) {
  return <span className={`mono ${className}`}>{children}</span>
}

/** An identifier: monospace, selectable, click to copy. */
export function Id({ value, className = '' }: { value: string; className?: string }) {
  return (
    <button
      type="button"
      title="Copy"
      onClick={() => void navigator.clipboard?.writeText(value)}
      className={`mono cursor-copy text-left text-xs text-zinc-400 transition-colors duration-150 ease-out hover:text-zinc-100 ${className}`}
    >
      {value}
    </button>
  )
}

type Tone = 'neutral' | 'accent' | 'pass' | 'fail'

const TONES: Record<Tone, string> = {
  neutral: 'border-zinc-800 bg-zinc-950 text-zinc-400',
  accent: 'border-accent/40 bg-accent/10 text-accent',
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
      className={`mono inline-flex h-6 items-center rounded-input border px-2 text-xs font-medium uppercase tracking-wider ${TONES[tone]}`}
    >
      {children}
    </span>
  )
}

export function Row({
  label,
  children,
}: {
  label: string
  children: ReactNode
}) {
  return (
    <div className="flex items-baseline gap-4 py-1">
      <dt className="label w-40 shrink-0">{label}</dt>
      <dd className="mono min-w-0 flex-1 break-words text-sm text-zinc-200">
        {children}
      </dd>
    </div>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full items-center justify-center px-8 py-8 text-center text-sm text-zinc-600">
      {children}
    </div>
  )
}

/** Skeletons, never spinners: the shape of the answer while it loads. */
export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="flex flex-col gap-2 p-4">
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
