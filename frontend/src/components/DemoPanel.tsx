// §15 — the demo control panel, in the header bar.
//
// Each button POSTs to one `/api/demo/*` endpoint and reports exactly what came
// back. The endpoints arrive with M8 and are mounted only when `DEMO_MODE=true`;
// until then a button reports the 404 it received rather than implying an
// action happened. Nothing here simulates an effect client-side.

import { useState } from 'react'
import {
  Ban,
  PackageMinus,
  RotateCcw,
  Repeat,
  TrendingUp,
  Loader2,
} from 'lucide-react'
import { api, KavachApiError } from '../lib/api'

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

export default function DemoPanel({ onAction }: { onAction: () => void }) {
  const [busy, setBusy] = useState<string | null>(null)
  const [note, setNote] = useState<{ text: string; failed: boolean } | null>(null)

  async function run(action: string) {
    setBusy(action)
    setNote(null)
    try {
      await api.demo(action)
      setNote({ text: `${action} ok`, failed: false })
      onAction()
    } catch (err) {
      const message =
        err instanceof KavachApiError
          ? err.status === 404
            ? `${action}: not mounted (M8, DEMO_MODE)`
            : `${action}: ${err.code}`
          : `${action}: request failed`
      setNote({ text: message, failed: true })
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="flex items-center gap-2">
      {note ? (
        <span
          className={`mono text-xs ${note.failed ? 'text-fail' : 'text-zinc-500'}`}
        >
          {note.text}
        </span>
      ) : null}
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
    </div>
  )
}
