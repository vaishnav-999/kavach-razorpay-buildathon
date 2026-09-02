// §14 — the left column: intent in, narration out.
//
// The composer's enabled state is derived from the session state the audit
// chain reports (§11.3), not from anything this component remembers. A terminal
// session offers a new session and never a retry: a run that ended on a model
// turn cannot be continued, and pretending otherwise would send the human back
// into a request the provider will refuse.

import { useState } from 'react'
import { CornerDownLeft, OctagonX, CheckCircle2, Plus } from 'lucide-react'
import type { TraceItem } from '../trace'
import AgentTrace from './AgentTrace'
import { Panel } from './ui'

const CANONICAL_INTENT =
  'Order lunch for our 12-person offsite on Thursday. Eight of us are ' +
  'vegetarian, four are not. High protein if you can. Keep it under six ' +
  'thousand rupees.'

export default function ChatPanel({
  items,
  streaming,
  sessionState,
  terminalReason,
  onSend,
  onNewSession,
}: {
  items: TraceItem[]
  streaming: boolean
  sessionState: string
  terminalReason: string | null
  onSend: (content: string) => void
  onNewSession: () => void
}) {
  const [draft, setDraft] = useState('')

  const halted = sessionState === 'HALTED' || sessionState === 'EXPIRED'
  const complete = sessionState === 'ORDER_CREATED'
  const locked = halted || complete
  const canSend = !streaming && !locked && draft.trim().length > 0

  function send() {
    if (!canSend) return
    onSend(draft.trim())
    setDraft('')
  }

  return (
    <Panel
      title="Agent"
      icon={<CornerDownLeft size={14} />}
      className="min-w-0"
      bodyClassName="overflow-hidden"
      right={
        <span className="mono text-xs uppercase tracking-widest text-zinc-500">
          {sessionState}
        </span>
      }
    >
      <div className="flex h-full min-h-0 flex-col">
        <div className="min-h-0 flex-1 overflow-y-auto">
          <AgentTrace items={items} streaming={streaming} />
        </div>

        {halted ? (
          <Banner
            tone="fail"
            icon={<OctagonX size={14} />}
            title={`Session ${sessionState.toLowerCase()}`}
            body={
              terminalReason === 'provider_error'
                ? 'The model provider failed mid-turn. This session ended on a ' +
                  'model turn and cannot be continued — the provider rejects a ' +
                  'history that ends there. Nothing was submitted and nothing ' +
                  'was purchased.'
                : `Terminal reason: ${terminalReason ?? 'unknown'}. This session ` +
                  'cannot be continued. Nothing was submitted after it halted.'
            }
            action={
              <button type="button" className="btn btn-primary" onClick={onNewSession}>
                <Plus size={14} />
                Start a new session
              </button>
            }
          />
        ) : null}

        {complete ? (
          <Banner
            tone="pass"
            icon={<CheckCircle2 size={14} />}
            title="Order created"
            body="The Guard allowed this purchase and the merchant created an order. Settle it on the right; payment status comes from the merchant, never from the browser."
            action={
              <button type="button" className="btn" onClick={onNewSession}>
                <Plus size={14} />
                New session
              </button>
            }
          />
        ) : null}

        {!locked ? (
          <div className="shrink-0 border-t border-zinc-800 p-3">
            <div className="flex items-end gap-2">
              <textarea
                className="field h-auto min-h-16 flex-1 resize-none py-2 font-sans"
                placeholder="What should the agent buy?"
                rows={2}
                value={draft}
                disabled={streaming}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                    e.preventDefault()
                    send()
                  }
                }}
              />
              <button
                type="button"
                className="btn btn-primary"
                disabled={!canSend}
                onClick={send}
              >
                <CornerDownLeft size={14} />
                Send
              </button>
            </div>
            {!items.length && !streaming ? (
              <button
                type="button"
                className="mt-2 text-left text-xs leading-5 text-zinc-500 transition-colors duration-150 ease-out hover:text-zinc-300"
                onClick={() => setDraft(CANONICAL_INTENT)}
              >
                Use the canonical demo intent →
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
    </Panel>
  )
}

function Banner({
  tone,
  icon,
  title,
  body,
  action,
}: {
  tone: 'fail' | 'pass'
  icon: React.ReactNode
  title: string
  body: string
  action: React.ReactNode
}) {
  const colour =
    tone === 'fail'
      ? 'border-t-fail/40 bg-fail/10 text-fail'
      : 'border-t-pass/40 bg-pass/10 text-pass'
  return (
    <div className={`shrink-0 animate-panel-in border-t px-4 py-3 ${colour}`}>
      <div className="flex items-center gap-2">
        {icon}
        <p className="mono text-xs font-medium uppercase tracking-wider">{title}</p>
      </div>
      <p className="mt-2 text-sm leading-6 text-zinc-300">{body}</p>
      <div className="mt-3">{action}</div>
    </div>
  )
}
