// §15 — the demo control panel, in the header bar.
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
    <div className="relative flex items-center gap-2">
      {ACTIONS.map(({ action, label, icon: Icon, title }) => (
        <button
          key={action}
          type="button"
          title={title}
          className="btn h-7 px-2 text-xs"
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

      <button
        type="button"
        title="PK-005 served verbatim, the forced poisoned cart, and the tool schema it had nowhere to land in."
        className="btn h-7 border-accent/40 bg-accent/10 px-2 text-xs text-accent hover:border-accent hover:bg-accent/20 hover:text-accent"
        onClick={onOpenInjection}
      >
        <Syringe size={12} />
        Injection
      </button>

      {result || error ? (
        <div className="panel animate-panel-in absolute right-0 top-9 z-30 w-[560px] shadow-xl">
          <header className="panel-head">
            <h2 className="panel-title">{result?.action ?? error?.action}</h2>
            <button
              type="button"
              className="ml-auto text-zinc-500 hover:text-zinc-100"
              onClick={() => {
                setResult(null)
                setError(null)
              }}
            >
              <X size={14} />
            </button>
          </header>

          <div className="panel-body max-h-72 p-4">
            {error ? (
              <p className="mono text-sm text-fail">{error.text}</p>
            ) : result ? (
              <>
                <p className="text-sm text-zinc-100">{result.summary}</p>

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
                  <p className="mt-3 text-xs leading-5 text-zinc-500">
                    {result.triggers}
                  </p>
                ) : null}
              </>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  )
}
