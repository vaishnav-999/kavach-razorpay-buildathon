// §15 — the demo controls, as a utility bar at the foot of the screen.
//
// This is deliberately the quietest element in the console. It is a tool the
// presenter reaches for, not a feature the audience is meant to read: ghost
// buttons on chrome, 36px tall, below the fold of attention. It used to sit in
// the header, where it competed with the session identity and wrapped the bar
// onto a second line.
//
// Each button POSTs to one `/api/demo/*` endpoint and reports exactly what came
// back: the summary line, the rule the change will now make fire, and every
// field that moved as `before → after`. Nothing here simulates an effect
// client-side, and nothing is described as having happened unless the server
// said it did.
//
// The endpoints are gated by `DEMO_MODE`. With the flag off they answer
// `DEMO_MODE_DISABLED`, and that is what the panel shows — a switch that is
// deliberately off, not a broken deployment.

import { useState } from 'react'
import {
  Ban,
  PackageMinus,
  RotateCcw,
  Repeat,
  Syringe,
  TrendingUp,
  Loader2,
  X,
} from 'lucide-react'
import { api, KavachApiError } from '../lib/api'
import type { DemoActionResult, SessionCreated } from '../types'

const ACTIONS = [
  {
    action: 'drift-price',
    label: 'Drift price',
    icon: TrendingUp,
    title: 'PK-003 45000 → 49500 paise. Triggers CV-003 on an issued quote.',
  },
  {
    action: 'deplete-stock',
    label: 'Deplete stock',
    icon: PackageMinus,
    title: 'PK-003 stock → 3. The happy-path cart needs 4. Triggers CV-002.',
  },
  {
    action: 'revoke-mandate',
    label: 'Revoke mandate',
    icon: Ban,
    title: 'Revokes the active mandate. Triggers MG-002.',
  },
  {
    action: 'replay-webhook',
    label: 'Replay webhook',
    icon: Repeat,
    title:
      'Re-POSTs the stored raw body and signature of the last webhook. Genuine signature, genuine event id.',
  },
  {
    action: 'reset',
    label: 'Reset',
    icon: RotateCcw,
    title: 'Restores seed prices and stock. Keeps audit_events.',
  },
] as const

export default function DemoPanel({
  session,
  onAction,
  onOpenInjection,
}: {
  session: SessionCreated | null
  onAction: () => void
  onOpenInjection: () => void
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const [result, setResult] = useState<DemoActionResult | null>(null)
  const [error, setError] = useState<{ action: string; text: string } | null>(null)

  async function run(action: string) {
    setBusy(action)
    setResult(null)
    setError(null)
    try {
      // The action lands on this session's chain, so DEMO_ACTION_TRIGGERED
      // appears in the audit trace next to what it caused.
      setResult(
        await api.demo(action, {
          correlation_id: session?.correlation_id ?? null,
          session_id: session?.session_id ?? null,
        }),
      )
      onAction()
    } catch (err) {
      setError({
        action,
        text:
          err instanceof KavachApiError
            ? `${err.code} — ${err.message}`
            : 'The request did not reach the server.',
      })
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="relative shrink-0 border-t border-zinc-800 bg-zinc-900">
      {/* The result reads upward out of the bar it came from. */}
      {result || error ? (
        <div className="absolute bottom-full left-0 z-30 mb-px w-[min(620px,100vw)] animate-panel-in border border-b-0 border-zinc-800 bg-zinc-950">
          <header className="panel-head">
            <h2 className="panel-title">{result?.action ?? error?.action}</h2>
            <button
              type="button"
              className="ml-auto text-zinc-500 transition-colors duration-150 ease-out hover:text-zinc-100"
              onClick={() => {
                setResult(null)
                setError(null)
              }}
            >
              <X size={14} />
            </button>
          </header>

          <div className="max-h-[50vh] overflow-y-auto px-4 py-3">
            {error ? (
              <p className="mono text-sm text-fail">{error.text}</p>
            ) : result ? (
              <>
                <p className="max-w-[70ch] text-sm leading-6 text-zinc-100">
                  {result.summary}
                </p>

                {result.changed.length ? (
                  <ul className="mt-3 flex flex-col gap-1">
                    {result.changed.map((change, i) => (
                      <li
                        key={`${change.target}.${change.field}.${i}`}
                        className="well mono flex items-baseline gap-2 px-2 py-1 text-xs"
                      >
                        <span className="text-zinc-500">{change.target}</span>
                        <span className="text-zinc-400">{change.field}</span>
                        <span className="ml-auto text-zinc-500">
                          {String(change.before ?? '—')}
                        </span>
                        <span className="text-zinc-600">→</span>
                        <span className="text-zinc-100">
                          {String(change.after ?? '—')}
                        </span>
                        {change.unit ? (
                          <span className="text-zinc-600">{change.unit}</span>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                ) : null}

                {result.triggers ? (
                  <p className="mt-3 max-w-[70ch] text-xs leading-5 text-zinc-500">
                    {result.triggers}
                  </p>
                ) : null}
              </>
            ) : null}
          </div>
        </div>
      ) : null}

      <div className="flex h-9 items-center gap-1 overflow-x-auto px-3">
        <span className="label mr-2">demo</span>

        {ACTIONS.map(({ action, label, icon: Icon, title }) => (
          <button
            key={action}
            type="button"
            title={title}
            className="btn-ghost"
            disabled={busy !== null}
            onClick={() => void run(action)}
          >
            {busy === action ? (
              <Loader2 size={12} className="animate-spin" />
            ) : (
              <Icon size={12} />
            )}
            {label}
          </button>
        ))}

        <span aria-hidden className="mx-2 h-4 w-px shrink-0 bg-zinc-800" />

        <button
          type="button"
          title="PK-005 served verbatim, the forced poisoned cart, and the tool schema it had nowhere to land in."
          className="btn-ghost"
          onClick={onOpenInjection}
        >
          <Syringe size={12} />
          Injection
        </button>

        <span className="mono ml-auto hidden shrink-0 pl-4 text-xs text-zinc-600 lg:inline">
          §15 — every action writes DEMO_ACTION_TRIGGERED to this chain
        </span>
      </div>
    </div>
  )
}
