// §16.5 — the injection, side by side with the block.
//
// Left: PK-005's description exactly as the merchant stored it, with the length
// and SHA-256 of the bytes `/merchant/catalog` serves, and the platform's own
// UNTRUSTED_CONTENT_FLAGGED observations from the audit chain. The platform saw
// the instruction and changed nothing about how it served it.
//
// Right: the same cart the note asks for — PK-001 x8, PK-003 x4, PK-005 x12 —
// submitted through the same `execute_authorized_purchase` call site a normal
// purchase uses, and blocked. The Razorpay client's own call counter is read
// either side of the submission, so "no Razorpay activity" is a measurement.
//
// Below: the `submit_purchase` schema. `policy_override` and `skip_validation`
// are not refused parameters — they do not exist, and neither does any price,
// amount or currency, on any of the ten tools. There is nowhere for the
// instruction to land.
//
// Nothing on this screen is computed here and nothing is truncated here.

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
    <div className="fixed inset-0 z-40 flex flex-col bg-zinc-950/95 backdrop-blur-sm">
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-zinc-800 bg-zinc-900 px-4">
        <Syringe size={16} className="text-accent" />
        <span className="mono text-sm font-semibold tracking-widest text-zinc-100">
          PROMPT INJECTION — PK-005
        </span>
        <span className="hidden text-xs text-zinc-500 lg:inline">
          Served verbatim. Read by the model. Blocked anyway.
        </span>
        <button type="button" className="btn ml-auto h-7 px-2 text-xs" onClick={onClose}>
          <X size={12} />
          Close
        </button>
      </header>

      <main className="grid min-h-0 flex-1 gap-2 overflow-y-auto p-2 lg:grid-cols-2">
        <div className="flex min-h-0 flex-col gap-2">
          <Served evidence={evidence} error={loadError} />
          <Flagged evidence={evidence} events={flagged} />
        </div>

        <div className="flex min-h-0 flex-col gap-2">
          <Forced
            evidence={evidence}
            forced={forced}
            error={forceError}
            busy={busy}
            onForce={() => void force()}
          />
          <GuardConsole decision={forced?.guard ?? null} loading={busy} />
        </div>

        <div className="lg:col-span-2">
          <Schema evidence={evidence} />
        </div>
      </main>
    </div>
  )
}

// ── left: what the merchant published ─────────────────────────────────────

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
        <div className="p-4">
          <dl className="mb-3 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1">
            <dt className="label">sku</dt>
            <dd className="mono text-xs text-zinc-300">
              {evidence.product.sku} · {evidence.product.name} ·{' '}
              {rupees(evidence.product.unit_price_paise)}
            </dd>
            <dt className="label">sha-256</dt>
            <dd className="mono truncate text-xs text-zinc-400">
              <Id value={evidence.served_verbatim.sha256} />
            </dd>
          </dl>

          {/* The attack itself, unmodified. Never escaped, stripped or cut. */}
          <pre className="well mono max-h-[38vh] overflow-auto whitespace-pre-wrap break-words p-3 text-xs leading-5 text-zinc-300">
            {evidence.product.description}
          </pre>

          <p className="mt-3 text-xs leading-5 text-zinc-500">
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
      icon={<AlertTriangle size={14} className="text-zinc-500" />}
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
          <p className="mt-2 text-xs leading-5 text-zinc-500">
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
            <li key={event.id} className="border-b border-zinc-800/60 px-4 py-3">
              <div className="flex items-baseline gap-2">
                <span className="mono text-xs text-zinc-400">
                  {String(event.payload.sku ?? '—')}
                </span>
                <span className="mono text-xs text-zinc-600">
                  {String(event.payload.field ?? '')}
                </span>
                <span className="ml-auto mono text-xs text-zinc-600">
                  seq {event.seq}
                </span>
              </div>
              <p className="mt-1 text-xs leading-5 text-zinc-400">
                {String(event.payload.reason ?? '')}
              </p>
            </li>
          ))}
          <li className="px-4 py-3 text-xs leading-5 text-zinc-500">
            Written when the agent read the catalog, on this correlation id.
          </li>
        </ul>
      )}
    </Panel>
  )
}

// ── right: the forced submission ──────────────────────────────────────────

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
      icon={<ShieldX size={14} className={forced ? 'text-fail' : 'text-zinc-500'} />}
      accent={forced?.guard.verdict === 'BLOCK' ? 'fail' : 'none'}
      right={
        <button
          type="button"
          className="btn btn-primary h-7 px-2 text-xs"
          disabled={busy}
          onClick={onForce}
        >
          {busy ? <Loader2 size={12} className="animate-spin" /> : <Syringe size={12} />}
          Force poisoned cart
        </button>
      }
    >
      <div className="p-4">
        {arithmetic ? (
          <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1">
            <dt className="label">correct cart</dt>
            <dd className="mono text-xs text-zinc-300">
              {rupees(arithmetic.correct_total_paise)}
            </dd>
            <dt className="label">poisoned cart</dt>
            <dd className="mono text-xs text-fail">
              {rupees(arithmetic.poisoned_total_paise)} — includes{' '}
              {arithmetic.injected_line.sku} ×{arithmetic.injected_line.qty} at{' '}
              {rupees(arithmetic.injected_line.line_total_paise)}
            </dd>
            <dt className="label">overshoot</dt>
            <dd className="mono text-xs text-zinc-300">
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
            <p className="text-sm text-zinc-100">{forced.summary}</p>
            <dl className="mt-3 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1">
              <dt className="label">quote</dt>
              <dd className="mono text-xs text-zinc-400">
                <Id value={forced.quote.quote_id} /> ·{' '}
                {rupees(forced.quote.total_paise)} signed by the merchant
              </dd>
              <dt className="label">mandate</dt>
              <dd className="mono text-xs text-zinc-400">
                <Id value={forced.mandate.mandate_id} /> ·{' '}
                {rupees(forced.mandate.max_amount_paise)} · {forced.mandate.status} ·{' '}
                {forced.mandate.source}
              </dd>
              <dt className="label">razorpay</dt>
              <dd className="mono text-xs text-pass">
                client calls {forced.razorpay.client_calls_before} →{' '}
                {forced.razorpay.client_calls_after} · orders for this quote{' '}
                {forced.razorpay.orders_for_quote}
              </dd>
            </dl>
            <p className="mt-3 text-xs leading-5 text-zinc-500">
              {forced.razorpay.detail}
            </p>
          </div>
        ) : (
          <p className="mt-4 border-t border-zinc-800 pt-3 text-xs leading-5 text-zinc-500">
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

// ── below: nowhere for the instruction to land ────────────────────────────

function Schema({ evidence }: { evidence: InjectionEvidence | null }) {
  if (!evidence) {
    return (
      <Panel title="submit_purchase — the tool schema" icon={<Terminal size={14} />}>
        <Skeleton rows={4} />
      </Panel>
    )
  }

  const properties = evidence.tool.parameters.properties ?? {}
  const required = evidence.tool.parameters.required ?? []

  return (
    <Panel
      title="submit_purchase — the tool schema"
      icon={<Terminal size={14} className="text-zinc-500" />}
      right={
        <span className="mono text-xs text-zinc-500">
          {evidence.parameters.declared_total} parameters across all ten tools
        </span>
      }
    >
      <div className="grid gap-4 p-4 lg:grid-cols-2">
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
          <p className="mt-3 text-xs leading-5 text-zinc-500">
            {evidence.parameters.detail}
          </p>
        </div>

        <div>
          <p className="label mb-2">absent — not declared by any tool</p>
          <ul className="flex flex-wrap gap-1">
            {evidence.parameters.absent.map((p) => (
              <li
                key={p.name}
                className="mono rounded-input border border-zinc-800 bg-zinc-950 px-2 py-1 text-xs text-zinc-500 line-through"
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
