// §14 — the left column. What the agent did, as it did it.
//
// This is a rendering of the §11.6 SSE stream and nothing more. It holds no
// authority and no money: every figure in it came out of a tool result the
// server produced, and the record of the run is the audit chain on the right,
// not this list.
//
// Nothing here is accent-coloured. The accent belongs to primary actions; a
// narration line is neither an action nor a status.

import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, ChevronRight, User } from 'lucide-react'
import type { TraceItem } from '../trace'
import { clockTime } from '../lib/format'

export default function AgentTrace({
  items,
  streaming,
}: {
  items: TraceItem[]
  streaming: boolean
}) {
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' })
  }, [items.length, streaming])

  // The empty state is the first thing a viewer who has never seen this system
  // reads, so it says what is about to happen rather than sitting blank. Kept
  // to its own measure: three lines of scaffolding, not a marketing panel.
  if (!items.length) {
    return (
      <div className="px-4 py-3">
        <p className="max-w-[58ch] text-sm leading-6 text-zinc-300">
          Describe what you want bought. The agent discovers merchants, builds a
          cart and asks you for authority — it cannot spend without it.
        </p>

        <ol className="mt-5 flex max-w-[58ch] flex-col gap-3">
          {[
            [
              'The agent works',
              'It searches the catalog and builds a cart. Merchant text is untrusted input and is never treated as instruction.',
            ],
            [
              'You grant authority',
              'A mandate is proposed, you sign it, and the limits are clamped server-side. Until then it grants nothing.',
            ],
            [
              'The Guard decides',
              'Nine rules run at the one call site. No payment order exists unless the verdict is ALLOW.',
            ],
          ].map(([title, body], i) => (
            <li key={title} className="flex gap-3">
              <span className="mono mt-px shrink-0 text-xs text-zinc-600">
                {i + 1}
              </span>
              <span className="min-w-0">
                <span className="block text-sm font-medium text-zinc-300">
                  {title}
                </span>
                <span className="mt-0.5 block text-sm leading-6 text-zinc-500">
                  {body}
                </span>
              </span>
            </li>
          ))}
        </ol>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-0.5 px-4 py-3">
      {items.map((item, i) => (
        <Line key={i} item={item} />
      ))}
      {streaming ? (
        <div className="mono flex animate-trace-in items-center gap-2 py-1.5 text-xs text-zinc-500">
          <span
            aria-hidden
            className="h-1.5 w-1.5 animate-pulse rounded-full bg-zinc-500"
          />
          working…
        </div>
      ) : null}
      <div ref={endRef} />
    </div>
  )
}

function Line({ item }: { item: TraceItem }) {
  switch (item.kind) {
    case 'user':
      return (
        <div className="my-1 flex animate-trace-in gap-3 border-l-2 border-zinc-700 bg-zinc-900/50 px-3 py-2">
          <User size={13} className="mt-1 shrink-0 text-zinc-500" />
          <p className="text-sm leading-6 text-zinc-100">{item.text}</p>
        </div>
      )

    case 'thought':
    case 'message':
      return (
        <p
          className={`animate-trace-in whitespace-pre-wrap py-1.5 text-sm leading-6 ${
            item.kind === 'message' ? 'text-zinc-100' : 'text-zinc-400'
          }`}
        >
          {item.text}
        </p>
      )

    case 'state':
      return (
        <div className="flex animate-trace-in items-center gap-3 py-2">
          <span className="h-px flex-1 bg-zinc-800" />
          <span className="mono text-xs uppercase tracking-widest text-zinc-600">
            {item.state}
          </span>
          <span className="h-px flex-1 bg-zinc-800" />
        </div>
      )

    case 'tool':
      return <ToolLine item={item} />

    case 'error':
      return (
        <div className="my-1 flex animate-trace-in gap-3 border-l-2 border-fail bg-fail/10 px-3 py-2">
          <AlertTriangle size={13} className="mt-1 shrink-0 text-fail" />
          <div className="min-w-0">
            <p className="mono text-xs font-medium uppercase tracking-wider text-fail">
              {item.code}
            </p>
            <p className="mt-1 text-sm leading-6 text-zinc-300">{item.detail}</p>
          </div>
        </div>
      )

    case 'done':
      return (
        <div className="mono mt-2 flex animate-trace-in flex-wrap items-center gap-x-4 gap-y-1 border-t border-zinc-800 pt-2 text-xs text-zinc-600">
          <span>turn ended · {item.state}</span>
          <span>{item.toolCalls} tool calls</span>
          <span>{item.submits} submit attempts</span>
          {item.reason ? <span>reason: {item.reason}</span> : null}
        </div>
      )
  }
}

function ToolLine({ item }: { item: Extract<TraceItem, { kind: 'tool' }> }) {
  const [open, setOpen] = useState(false)
  const pending = item.summary === null

  return (
    <div className="animate-trace-in">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="group flex w-full items-baseline gap-2 rounded-input px-1 py-1 text-left transition-colors duration-150 ease-out hover:bg-zinc-900"
      >
        <ChevronRight
          size={12}
          className={`mt-1 shrink-0 text-zinc-600 transition-transform duration-150 ease-out ${
            open ? 'rotate-90' : ''
          }`}
        />
        <span className="mono shrink-0 text-sm text-zinc-600">&gt;</span>
        <span className="mono shrink-0 text-sm text-zinc-100">{item.name}</span>
        <span
          className={`mono min-w-0 flex-1 truncate text-sm ${
            item.failed ? 'text-fail' : 'text-zinc-400'
          }`}
        >
          {pending ? item.args : item.summary}
        </span>
        <span className="mono shrink-0 text-xs text-zinc-700">
          {clockTime(item.at)}
        </span>
      </button>

      {open ? (
        <div className="well mx-1 mb-2 mt-1 overflow-x-auto p-3">
          <p className="label mb-1">arguments</p>
          <pre className="mono whitespace-pre text-xs leading-5 text-zinc-400">
            {JSON.stringify(item.rawArgs, null, 2)}
          </pre>
          <p className="label mb-1 mt-3">result</p>
          <pre className="mono whitespace-pre text-xs leading-5 text-zinc-400">
            {item.rawResult === null
              ? 'pending…'
              : JSON.stringify(item.rawResult, null, 2)}
          </pre>
        </div>
      ) : null}
    </div>
  )
}
