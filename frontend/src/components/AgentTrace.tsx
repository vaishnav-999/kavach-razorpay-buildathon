// §14 — the left column. What the agent did, as it did it.
//
// This is a rendering of the §11.6 SSE stream and nothing more. It holds no
// authority and no money: every figure in it came out of a tool result the
// server produced, and the record of the run is the audit chain on the right,
// not this list.

import { useEffect, useRef, useState } from 'react'
import {
  AlertTriangle,
  ChevronRight,
  CircleDot,
  User,
} from 'lucide-react'
import type { TraceItem } from '../trace'
import { clockTime } from '../lib/format'
import { Empty } from './ui'

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

  if (!items.length) {
    return (
      <Empty>
        Describe what you want bought. The agent discovers merchants, builds a
        cart and asks you for authority — it cannot spend without it.
      </Empty>
    )
  }

  return (
    <div className="flex flex-col gap-1 px-4 py-4">
      {items.map((item, i) => (
        <Line key={i} item={item} />
      ))}
      {streaming ? (
        <div className="mono animate-trace-in flex items-center gap-2 py-2 text-xs text-zinc-600">
          <CircleDot size={12} className="animate-pulse text-accent" />
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
        <div className="animate-trace-in flex gap-3 rounded-card border border-zinc-800 bg-zinc-950 px-3 py-3">
          <User size={14} className="mt-1 shrink-0 text-accent" />
          <p className="text-sm leading-6 text-zinc-100">{item.text}</p>
        </div>
      )

    case 'thought':
    case 'message':
      return (
        <p
          className={`animate-trace-in whitespace-pre-wrap py-2 text-sm leading-6 ${
            item.kind === 'message' ? 'text-zinc-100' : 'text-zinc-400'
          }`}
        >
          {item.text}
        </p>
      )

    case 'state':
      return (
        <div className="animate-trace-in flex items-center gap-3 py-2">
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
        <div className="animate-trace-in flex gap-3 rounded-card border border-fail/40 bg-fail/10 px-3 py-3">
          <AlertTriangle size={14} className="mt-1 shrink-0 text-fail" />
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
        <div className="mono animate-trace-in flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-zinc-800 pt-3 text-xs text-zinc-600">
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
        className="group flex w-full items-baseline gap-2 rounded-input px-1 py-1 text-left transition-colors duration-150 ease-out hover:bg-zinc-800/60"
      >
        <ChevronRight
          size={12}
          className={`mt-1 shrink-0 text-zinc-600 transition-transform duration-150 ease-out ${
            open ? 'rotate-90' : ''
          }`}
        />
        <span className="mono shrink-0 text-sm text-zinc-500">&gt;</span>
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
