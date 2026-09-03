// §16.5 — the injection, side by side with the block.
//
// Top: PK-005's description exactly as the merchant stored it, with the length
// and SHA-256 of the bytes `/merchant/catalog` serves. The platform saw the
// instruction and changed nothing about how it served it.
//
// Beside it: the same cart the note asks for — PK-001 x8, PK-003 x4, PK-005 x12
// — submitted through the same `execute_authorized_purchase` call site a normal
// purchase uses, and blocked. The Razorpay client's own call counter is read
// either side of the submission, so "no Razorpay activity" is a measurement.
//
// Then the verdict, full width. Then the platform's own
// UNTRUSTED_CONTENT_FLAGGED observations, and the `submit_purchase` schema:
// `policy_override` and `skip_validation` are not refused parameters — they do
// not exist, and neither does any price, amount or currency, on any of the ten
// tools. There is nowhere for the instruction to land.
//
// LAYOUT RULE: **nothing on this screen is truncated, clipped or put behind an
// inner scrollbar.** The overlay is one scroll container; every panel inside it
// is natural height. The merchant's text in particular is rendered in full —
// cutting the attack off mid-sentence would undercut the only claim this screen
// makes.

import { useCallback, useEffect, useState } from 'react'
import {
  AlertTriangle,
  FileWarning,
  Loader2,
  ShieldX,
  Syringe,
  Terminal,
  X,
} from 'lucide-react'

import GuardConsole from './GuardConsole'
import { Badge, Empty, Id, Panel, Skeleton } from './ui'
import { api, KavachApiError } from '../lib/api'
import { rupees } from '../lib/format'
import type {
  AuditChain,
  AuditEvent,
  InjectionEvidence,
  PoisonedCartResult,
  SessionCreated,
} from '../types'

export default function InjectionPanel({
  session,
  chain,
  onClose,
  onAction,
}: {
  session: SessionCreated | null
  chain: AuditChain | null
  onClose: () => void
  onAction: () => void
}) {
  const [evidence, setEvidence] = useState<InjectionEvidence | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [forced, setForced] = useState<PoisonedCartResult | null>(null)
  const [forceError, setForceError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api
      .injection()
      .then(setEvidence)
      .catch((err) =>
        setLoadError(
          err instanceof KavachApiError
            ? `${err.code} — ${err.message}`
            : 'The evidence endpoint could not be read.',
        ),
      )
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const force = useCallback(async () => {
    setBusy(true)
    setForceError(null)
    try {
      setForced(
        await api.forcePoisonedCart({
          correlation_id: session?.correlation_id ?? null,
          session_id: session?.session_id ?? null,
        }),
      )
      onAction()
    } catch (err) {
      setForceError(
        err instanceof KavachApiError
          ? `${err.code} — ${err.message}`
          : 'The submission did not reach the server.',
      )
    } finally {
      setBusy(false)
    }
  }, [session, onAction])

  const flagged = (chain?.events ?? []).filter(
    (e) => e.event_type === 'UNTRUSTED_CONTENT_FLAGGED',
  )

  return (
    // Opaque, not translucent: no blur, nothing of the console showing through.
    <div className="fixed inset-0 z-40 flex flex-col bg-zinc-950">
      <header className="flex h-12 shrink-0 items-center gap-3 overflow-hidden border-b border-zinc-800 bg-zinc-900 px-4">
        <Syringe size={15} className="shrink-0 text-zinc-400" />
        <span className="mono shrink-0 text-sm font-semibold tracking-[0.2em] text-zinc-100">
          PK-005
        </span>
        <span className="mono hidden shrink-0 text-sm text-zinc-400 md:inline">
          PROMPT INJECTION
        </span>
        <span className="hidden min-w-0 truncate border-l border-zinc-800 pl-3 text-sm text-zinc-500 lg:inline">
          Served verbatim. Read by the model. Blocked anyway.
        </span>
        <button type="button" className="btn ml-auto shrink-0" onClick={onClose}>
          <X size={12} />
          Close
        </button>
      </header>

      {/* One scroll container for the whole screen. No nested scrollers. */}
      <main className="min-h-0 flex-1 overflow-y-auto">
        <div className="grid lg:grid-cols-2">
          <div className="border-b border-zinc-800 lg:border-r">
            <Served evidence={evidence} error={loadError} />
          </div>
          <div className="border-b border-zinc-800">
            <Forced
              evidence={evidence}
              forced={forced}
              error={forceError}
              busy={busy}
              onForce={() => void force()}
            />
          </div>
        </div>

        {/* The verdict gets the full width, because it is the point. */}
        <div className="border-b border-zinc-800">
          <GuardConsole decision={forced?.guard ?? null} loading={busy} />
        </div>

        <div className="border-b border-zinc-800">
          <Flagged evidence={evidence} events={flagged} />
        </div>

        <Schema evidence={evidence} />
      </main>
    </div>
  )
}

// ── what the merchant published ───────────────────────────────────────────

function Served({
  evidence,
  error,
}: {
  evidence: InjectionEvidence | null
  error: string | null
}) {
  return (
    <Panel
      title="Merchant text, served verbatim"
      icon={<FileWarning size={14} className="text-fail" />}
      accent="fail"
      bodyClassName="overflow-visible"
      right={
        evidence ? (
          <span className="mono text-xs text-zinc-500">
            {evidence.served_verbatim.length} chars · sanitised{' '}
            {String(evidence.served_verbatim.sanitised)}
          </span>
        ) : null
      }
    >
      {error ? <Empty>{error}</Empty> : null}
      {!evidence && !error ? <Skeleton rows={8} /> : null}
      {evidence ? (
        <div className="px-4 py-3">
          <dl className="mb-3 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1">
            <dt className="label">sku</dt>
            <dd className="mono min-w-0 truncate text-xs text-zinc-300">
              {evidence.product.sku} · {evidence.product.name} ·{' '}
              {rupees(evidence.product.unit_price_paise)}
            </dd>
            <dt className="label">sha-256</dt>
            <dd className="min-w-0 text-xs">
              <Id value={evidence.served_verbatim.sha256} />
            </dd>
          </dl>

          {/* The attack itself, unmodified and UNCLIPPED. Never escaped,
              stripped, truncated or hidden behind an inner scrollbar — the
              whole point is that a reader can see every word of it. */}
          <pre className="well mono whitespace-pre-wrap break-words p-3 text-sm leading-6 text-zinc-300">
            {evidence.product.description}
          </pre>

          <p className="mt-3 max-w-[80ch] text-xs leading-5 text-zinc-500">
            {evidence.served_verbatim.detail}
          </p>
        </div>
      ) : null}
    </Panel>
  )
}

function Flagged({
  evidence,
  events,
}: {
  evidence: InjectionEvidence | null
  events: AuditEvent[]
}) {
  const markers = evidence?.served_verbatim.instruction_markers ?? []
  return (
    <Panel
      title="Platform observation — UNTRUSTED_CONTENT_FLAGGED"
      icon={<AlertTriangle size={14} />}
      bodyClassName="overflow-visible"
      right={<Badge>{events.length} on this chain</Badge>}
    >
      {markers.length ? (
        <div className="border-b border-zinc-800 px-4 py-3">
          <p className="label mb-2">instruction-shaped phrases in this description</p>
          <ul className="flex flex-wrap gap-1">
            {markers.map((marker) => (
              <li
                key={marker}
                className="mono rounded-input border border-fail/40 bg-fail/10 px-2 py-1 text-xs text-fail"
              >
                {marker}
              </li>
            ))}
          </ul>
          <p className="mt-2 max-w-[90ch] text-xs leading-5 text-zinc-500">
            {evidence?.served_verbatim.flagged_reason} — and the platform served
            the description unchanged anyway. Flagging is evidence, not
            sanitisation.
          </p>
        </div>
      ) : null}

      {events.length === 0 ? (
        <Empty>
          No UNTRUSTED_CONTENT_FLAGGED event on this chain yet. It is written
          when the agent reads the catalog.
        </Empty>
      ) : (
        <ul className="flex flex-col">
          {events.map((event) => (
            <li key={event.id} className="border-b border-zinc-800/60 px-4 py-2">
              <div className="flex items-baseline gap-2">
                <span className="mono text-xs text-zinc-400">
                  {String(event.payload.sku ?? '—')}
                </span>
                <span className="mono text-xs text-zinc-600">
                  {String(event.payload.field ?? '')}
                </span>
                <span className="mono ml-auto text-xs text-zinc-600">
                  seq {event.seq}
                </span>
              </div>
              <p className="mt-1 max-w-[90ch] text-xs leading-5 text-zinc-400">
                {String(event.payload.reason ?? '')}
              </p>
            </li>
          ))}
          <li className="px-4 py-2 text-xs leading-5 text-zinc-500">
            Written when the agent read the catalog, on this correlation id.
          </li>
        </ul>
      )}
    </Panel>
  )
}

// ── the forced submission ─────────────────────────────────────────────────

function Forced({
  evidence,
  forced,
  error,
  busy,
  onForce,
}: {
  evidence: InjectionEvidence | null
  forced: PoisonedCartResult | null
  error: string | null
  busy: boolean
  onForce: () => void
}) {
  const arithmetic = evidence?.arithmetic ?? null
  return (
    <Panel
      title="Forced submission — the cart the note asks for"
      icon={<ShieldX size={14} className={forced ? 'text-fail' : ''} />}
      accent={forced?.guard.verdict === 'BLOCK' ? 'fail' : 'none'}
      bodyClassName="overflow-visible"
      right={
        <button
          type="button"
          className="btn btn-primary h-7 px-2.5 text-xs"
          disabled={busy}
          onClick={onForce}
        >
          {busy ? <Loader2 size={12} className="animate-spin" /> : <Syringe size={12} />}
          Force poisoned cart
        </button>
      }
    >
      <div className="px-4 py-3">
        {arithmetic ? (
          <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5">
            <dt className="label">correct cart</dt>
            <dd className="mono text-sm text-zinc-200">
              {rupees(arithmetic.correct_total_paise)}
            </dd>
            <dt className="label">poisoned cart</dt>
            <dd className="mono text-sm text-fail">
              {rupees(arithmetic.poisoned_total_paise)} — includes{' '}
              {arithmetic.injected_line.sku} ×{arithmetic.injected_line.qty} at{' '}
              {rupees(arithmetic.injected_line.line_total_paise)}
            </dd>
            <dt className="label">overshoot</dt>
            <dd className="mono text-sm text-zinc-200">
              {rupees(arithmetic.overshoot_paise)} over a{' '}
              {rupees(arithmetic.mandate_cap_paise)} mandate
            </dd>
          </dl>
        ) : (
          <Skeleton rows={3} />
        )}

        {error ? <p className="mono mt-3 text-sm text-fail">{error}</p> : null}

        {forced ? (
          <div className="mt-4 border-t border-zinc-800 pt-3">
            <p className="max-w-[80ch] text-sm leading-6 text-zinc-100">
              {forced.summary}
            </p>
            <dl className="mt-3 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5">
              <dt className="label">quote</dt>
              <dd className="mono min-w-0 text-xs text-zinc-400">
                <Id value={forced.quote.quote_id} />
                <span className="mt-0.5 block">
                  {rupees(forced.quote.total_paise)} signed by the merchant
                </span>
              </dd>
              <dt className="label">mandate</dt>
              <dd className="mono min-w-0 text-xs text-zinc-400">
                <Id value={forced.mandate.mandate_id} />
                <span className="mt-0.5 block">
                  {rupees(forced.mandate.max_amount_paise)} · {forced.mandate.status} ·{' '}
                  {forced.mandate.source}
                </span>
              </dd>
              <dt className="label">razorpay</dt>
              <dd className="mono text-xs text-pass">
                client calls {forced.razorpay.client_calls_before} →{' '}
                {forced.razorpay.client_calls_after} · orders for this quote{' '}
                {forced.razorpay.orders_for_quote}
              </dd>
            </dl>
            <p className="mt-3 max-w-[80ch] text-xs leading-5 text-zinc-500">
              {forced.razorpay.detail}
            </p>
          </div>
        ) : (
          <p className="mt-4 max-w-[80ch] border-t border-zinc-800 pt-3 text-xs leading-5 text-zinc-500">
            This submits through <span className="mono">submit_purchase</span>'s own
            path — the merchant signs the quote, and the Transaction Guard evaluates
            all nine rules at the single call site. There is no separate code path
            and no simulated verdict.
          </p>
        )}
      </div>
    </Panel>
  )
}

// ── nowhere for the instruction to land ───────────────────────────────────

function Schema({ evidence }: { evidence: InjectionEvidence | null }) {
  if (!evidence) {
    return (
      <Panel
        title="submit_purchase — the tool schema"
        icon={<Terminal size={14} />}
        bodyClassName="overflow-visible"
      >
        <Skeleton rows={4} />
      </Panel>
    )
  }

  const properties = evidence.tool.parameters.properties ?? {}
  const required = evidence.tool.parameters.required ?? []

  return (
    <Panel
      title="submit_purchase — the tool schema"
      icon={<Terminal size={14} />}
      bodyClassName="overflow-visible"
      right={
        <span className="mono text-xs text-zinc-500">
          {evidence.parameters.declared_total} parameters across all ten tools
        </span>
      }
    >
      <div className="grid gap-6 px-4 py-3 lg:grid-cols-2">
        <div>
          <p className="label mb-2">accepted</p>
          <ul className="flex flex-col gap-1">
            {Object.entries(properties).map(([name, schema]) => (
              <li key={name} className="well px-3 py-2">
                <div className="flex items-baseline gap-2">
                  <span className="mono text-sm text-zinc-100">{name}</span>
                  <span className="mono text-xs text-zinc-600">{schema.type}</span>
                  {required.includes(name) ? (
                    <span className="mono ml-auto text-xs text-zinc-600">required</span>
                  ) : null}
                </div>
                <p className="mt-1 text-xs leading-5 text-zinc-500">
                  {schema.description}
                </p>
              </li>
            ))}
          </ul>
          <p className="mt-3 max-w-[80ch] text-xs leading-5 text-zinc-500">
            {evidence.parameters.detail}
          </p>
        </div>

        <div>
          <p className="label mb-2">absent — not declared by any tool</p>
          <ul className="flex flex-wrap gap-1">
            {evidence.parameters.absent.map((p) => (
              <li
                key={p.name}
                className="mono rounded-input border border-zinc-800 bg-zinc-900/60 px-2 py-1 text-xs text-zinc-500 line-through"
              >
                {p.name}
              </li>
            ))}
          </ul>

          <p className="label mb-2 mt-4">four attacks, four structural answers</p>
          <ul className="flex flex-col gap-1">
            {evidence.attacks.map((attack) => (
              <li key={attack.asks_for} className="well px-3 py-2">
                <p className="text-xs leading-5 text-zinc-400">{attack.asks_for}</p>
                <p className="mt-1 text-xs leading-5 text-pass">{attack.answer}</p>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Panel>
  )
}
