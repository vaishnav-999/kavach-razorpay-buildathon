// §8, §14 — the moment a human grants authority, and the dominant element on
// the screen while it is pending.
//
// The card renders a PROPOSED mandate: a row with no signature and therefore no
// authority at all. `prompt_playback` is generated server-side from the clamped
// columns (§8.4), never from model output, and it is shown first and largest
// because it is the sentence the human is actually agreeing to. The agent's own
// justification appears below it, labelled as the agent's, so the two can never
// be confused.
//
// The amount field is the one place rupees are typed rather than displayed
// (§18.3). It is converted at the edge by `parseRupeesToPaise` and clamped
// again server-side by §8.3, so this input can lower a limit and never raise
// one past the ceiling.

import { useState } from 'react'
import { KeyRound, Lock } from 'lucide-react'
import type { Mandate } from '../types'
import { paiseToRupeeInput, parseRupeesToPaise, rupees } from '../lib/format'
import { Badge, Id, Panel, Row } from './ui'

export default function AuthorizationCard({
  mandate,
  justification,
  busy,
  error,
  onAuthorize,
}: {
  mandate: Mandate
  justification: string | null
  busy: boolean
  error: string | null
  onAuthorize: (maxAmountPaise: number, ttlMinutes: number) => void
}) {
  const [amount, setAmount] = useState(() =>
    paiseToRupeeInput(mandate.max_amount_paise),
  )
  const [ttl, setTtl] = useState('30')

  const paise = parseRupeesToPaise(amount)
  const ttlMinutes = /^\d{1,3}$/.test(ttl.trim()) ? Number(ttl.trim()) : null
  const valid = paise !== null && paise > 0 && ttlMinutes !== null && ttlMinutes > 0

  return (
    <Panel
      title="Authorization required"
      icon={<KeyRound size={14} />}
      className="animate-panel-in"
      right={<Badge>{mandate.status}</Badge>}
    >
      {/* The sentence being agreed to. Largest type in this panel, by intent. */}
      <div className="border-b border-zinc-800 px-4 py-4">
        <p className="max-w-[70ch] text-lg font-medium leading-7 text-zinc-100">
          {mandate.prompt_playback}
        </p>
        {justification ? (
          <p className="mt-3 max-w-[70ch] border-l-2 border-zinc-800 pl-3 text-sm leading-6 text-zinc-500">
            <span className="label mr-2">agent says</span>
            {justification}
          </p>
        ) : null}
      </div>

      <div className="grid grid-cols-2 gap-6 border-b border-zinc-800 px-4 py-4">
        <label className="flex flex-col gap-1.5">
          <span className="label">max amount (₹)</span>
          <input
            className="field"
            inputMode="decimal"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            aria-invalid={paise === null}
          />
          <span className="mono text-xs text-zinc-600">
            {paise === null
              ? 'enter rupees, e.g. 5160.00'
              : `${paise} paise · ${rupees(paise)}`}
          </span>
        </label>

        <label className="flex flex-col gap-1.5">
          <span className="label">expires in (minutes)</span>
          <input
            className="field"
            inputMode="numeric"
            value={ttl}
            onChange={(e) => setTtl(e.target.value)}
            aria-invalid={ttlMinutes === null}
          />
          <span className="mono text-xs text-zinc-600">
            counted from the moment you authorize
          </span>
        </label>
      </div>

      <dl className="px-4 py-3">
        <Row label="mandate">
          <Id value={mandate.id} />
        </Row>
        <Row label="merchants">{mandate.allowed_merchant_ids.join(', ') || '—'}</Row>
        <Row label="categories">{mandate.allowed_categories.join(', ') || '—'}</Row>
        <Row label="cumulative cap">{rupees(mandate.cumulative_cap_paise)}</Row>
        <Row label="transactions">{mandate.max_transactions}</Row>
        <Row label="signature">
          <span className="text-zinc-500">none — a proposal grants nothing</span>
        </Row>
      </dl>

      {error ? (
        <p className="mono border-t border-zinc-800 bg-fail/10 px-4 py-2 text-xs text-fail">
          {error}
        </p>
      ) : null}

      <div className="flex items-center gap-4 border-t border-zinc-800 bg-zinc-900/40 px-4 py-3">
        <button
          type="button"
          className="btn btn-primary"
          disabled={!valid || busy}
          onClick={() => {
            if (paise !== null && ttlMinutes !== null) onAuthorize(paise, ttlMinutes)
          }}
        >
          <Lock size={14} />
          {busy ? 'Signing…' : 'Authorize'}
        </button>
        <p className="max-w-[70ch] text-xs leading-5 text-zinc-500">
          Signing is the only way this becomes spendable. Limits are clamped
          server-side; you can grant less than the agent asked for, never more.
        </p>
      </div>
    </Panel>
  )
}
