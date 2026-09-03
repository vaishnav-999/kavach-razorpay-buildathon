// The header bar: what is running, on what, and in what state.
//
// `llm_provider` and `llm_model` are the ones the session was pinned to when it
// was created (§11.7), read back from the session row — not from configuration
// and not from anything the model reported about itself.
//
// LAYOUT RULE: this bar never wraps. It is exactly one 48px row at every width.
// The brand and the right-hand controls are `shrink-0`; the metadata group in
// between is `min-w-0` and truncates, item by item, until it disappears at the
// `xl` breakpoint. The demo controls used to live here and caused the wrap;
// they are now the utility bar at the foot of the screen.

import { Plus, ShieldCheck } from 'lucide-react'
import type { SessionCreated } from '../types'
import { Id } from './ui'

// The states that mean the run is over, one way or the other.
const TERMINAL = new Set(['HALTED', 'EXPIRED'])

export default function Header({
  session,
  sessionState,
  onNewSession,
}: {
  session: SessionCreated | null
  sessionState: string
  onNewSession: () => void
}) {
  const done = sessionState === 'ORDER_CREATED'
  const halted = TERMINAL.has(sessionState)

  return (
    <header className="flex h-12 shrink-0 items-center gap-4 overflow-hidden border-b border-zinc-800 bg-zinc-900 px-4">
      <div className="flex shrink-0 items-center gap-2">
        <ShieldCheck size={15} className="text-zinc-400" />
        <span className="mono text-sm font-semibold tracking-[0.2em] text-zinc-100">
          KAVACH
        </span>
      </div>

      <div className="hidden min-w-0 flex-1 items-baseline gap-5 border-l border-zinc-800 pl-4 xl:flex">
        <Meta label="session" className="min-w-0 max-w-[280px] flex-1">
          <Id value={session ? session.session_id : '—'} className="text-xs" />
        </Meta>
        <Meta label="model" className="min-w-0 max-w-[260px]">
          <span className="mono truncate text-xs text-zinc-400">
            {session ? `${session.llm_provider} · ${session.llm_model}` : '—'}
          </span>
        </Meta>
        {session?.cassette_name ? (
          <Meta label="cassette" className="min-w-0 max-w-[180px]">
            <span className="mono truncate text-xs text-zinc-400">
              {session.cassette_name}
            </span>
          </Meta>
        ) : null}
      </div>

      <div className="ml-auto flex shrink-0 items-center gap-3">
        <div className="flex items-center gap-2">
          <span
            aria-hidden
            className={`h-1.5 w-1.5 rounded-full ${
              halted ? 'bg-fail' : done ? 'bg-pass' : 'bg-zinc-600'
            }`}
          />
          <span
            className={`mono text-xs font-medium uppercase tracking-widest ${
              halted ? 'text-fail' : done ? 'text-pass' : 'text-zinc-300'
            }`}
          >
            {sessionState}
          </span>
        </div>
        <button type="button" className="btn" onClick={onNewSession}>
          <Plus size={12} />
          New session
        </button>
      </div>
    </header>
  )
}

function Meta({
  label,
  className = '',
  children,
}: {
  label: string
  className?: string
  children: React.ReactNode
}) {
  return (
    <div className={`flex items-baseline gap-2 ${className}`}>
      <span className="label">{label}</span>
      {children}
    </div>
  )
}
