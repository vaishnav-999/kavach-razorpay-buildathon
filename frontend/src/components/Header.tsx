// The header bar: what is running, on what, and the §15 demo controls.
//
// `llm_provider` and `llm_model` are the ones the session was pinned to when it
// was created (§11.7), read back from the session row — not from configuration
// and not from anything the model reported about itself.

import { Plus, ShieldCheck } from 'lucide-react'
import type { SessionCreated } from '../types'
import { Id } from './ui'
import DemoPanel from './DemoPanel'

export default function Header({
  session,
  sessionState,
  demoMode,
  onNewSession,
  onDemoAction,
}: {
  session: SessionCreated | null
  sessionState: string
  demoMode: boolean
  onNewSession: () => void
  onDemoAction: () => void
}) {
  return (
    <header className="flex h-12 shrink-0 items-center gap-4 border-b border-zinc-800 bg-zinc-900 px-4">
      <div className="flex items-center gap-2">
        <ShieldCheck size={16} className="text-accent" />
        <span className="mono text-sm font-semibold tracking-widest text-zinc-100">
          KAVACH
        </span>
      </div>

      <div className="hidden items-center gap-4 border-l border-zinc-800 pl-4 lg:flex">
        <Meta label="session">
          <Id value={session ? session.session_id : '—'} />
        </Meta>
        <Meta label="state">
          <span className="mono text-xs uppercase tracking-wider text-zinc-300">
            {sessionState}
          </span>
        </Meta>
        <Meta label="model">
          <span className="mono text-xs text-zinc-300">
            {session ? `${session.llm_provider} · ${session.llm_model}` : '—'}
          </span>
        </Meta>
        {session?.cassette_name ? (
          <Meta label="cassette">
            <span className="mono text-xs text-zinc-300">{session.cassette_name}</span>
          </Meta>
        ) : null}
      </div>

      <div className="ml-auto flex items-center gap-3">
        {demoMode ? <DemoPanel onAction={onDemoAction} /> : null}
        <button type="button" className="btn h-7 px-2 text-xs" onClick={onNewSession}>
          <Plus size={12} />
          New session
        </button>
      </div>
    </header>
  )
}

function Meta({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline gap-2">
      <span className="label">{label}</span>
      {children}
    </div>
  )
}
