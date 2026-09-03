// §13.3, §14 — the record. Secondary by design: collapsed to one hairline row
// until someone asks for it.
//
// The chain is the thing that proves the claim after the fact, not the thing a
// first-time viewer needs on screen while the claim is being made. So it states
// its headline — how many events, on which correlation id — and opens on a
// click. Progressive disclosure, not a fifth competing panel.
//
// One correlation id, every event in `audit_events.seq` order, each row
// expandable to the payload that was written at the time. `audit_events` is
// append-only (invariant 8): nothing in this panel can edit or remove a row,
// and the §15 reset button deliberately leaves the table intact.
//
// The second tab shows the signed artifacts, because "the merchant signed this
// number" is a claim a judge should be able to check rather than take.

import { useState } from 'react'
import { ChevronRight, ExternalLink, FileClock } from 'lucide-react'
import type { AuditChain } from '../types'
import { clockTime, dateTime, rupees, shortHex } from '../lib/format'
import { Badge, Empty, Id, Panel, Row, Skeleton } from './ui'

// Green and red carry one meaning in this app: something passed, or something
// failed. These are the events where that meaning applies.
const PASS_EVENTS = new Set([
  'POLICY_APPROVED',
  'AUTHORIZATION_GRANTED',
  'PAYMENT_VERIFIED',
  'ORDER_COMPLETED',
  'CHECKOUT_VALIDATED',
])

const FAIL_EVENTS = new Set([
  'POLICY_BLOCKED',
  'CHECKOUT_REJECTED',
  'PAYMENT_SIGNATURE_INVALID',
  'WEBHOOK_SIGNATURE_INVALID',
  'ILLEGAL_STATE_TRANSITION',
  'ORDER_FAILED',
  'AGENT_LIMIT_REACHED',
  'LLM_UNAVAILABLE',
  'UNTRUSTED_CONTENT_FLAGGED',
  'MERCHANT_REJECTED',
  'MANDATE_REVOKED',
])

export default function AuditTrace({
  chain,
  correlationId,
  loading,
  open,
  onToggle,
}: {
  chain: AuditChain | null
  correlationId: string | null
  loading: boolean
  open: boolean
  onToggle: () => void
}) {
  const [tab, setTab] = useState<'events' | 'artifacts'>('events')
  const count = chain ? chain.events.length : 0

  // ── collapsed: one row that says what it holds ──────────────────────────
  if (!open) {
    return (
      <div className="flex h-10 shrink-0 items-center border-t border-zinc-800 bg-zinc-950">
        <button
          type="button"
          onClick={onToggle}
          className="flex h-full min-w-0 flex-1 items-center gap-3 px-4 text-left transition-colors duration-150 ease-out hover:bg-zinc-900"
        >
          <ChevronRight size={12} className="shrink-0 text-zinc-600" />
          <FileClock size={13} className="shrink-0 text-zinc-500" />
          <span className="panel-title">Audit chain</span>
          <span className="mono shrink-0 text-xs text-zinc-400">
            {chain ? `${count} events` : loading ? 'reading…' : 'no events yet'}
          </span>
          {correlationId ? (
            <span className="mono min-w-0 truncate text-xs text-zinc-600">
              {correlationId}
            </span>
          ) : null}
          <span className="ml-auto hidden shrink-0 text-xs text-zinc-600 lg:inline">
            every event in this run, in order — click to open
          </span>
        </button>
      </div>
    )
  }

  // ── open: the full record, taking real space ───────────────────────────
  return (
    <Panel
      title="Audit chain"
      icon={<FileClock size={14} />}
      className="min-h-0 flex-[2] border-t border-zinc-800"
      right={
        <>
          <Tab active={tab === 'events'} onClick={() => setTab('events')}>
            events {count || ''}
          </Tab>
          <Tab active={tab === 'artifacts'} onClick={() => setTab('artifacts')}>
            artifacts
          </Tab>
          {correlationId ? (
            <a
              className="btn-ghost"
              href={`/api/audit/${correlationId}`}
              target="_blank"
              rel="noreferrer"
            >
              <ExternalLink size={12} />
              raw
            </a>
          ) : null}
          <button
            type="button"
            className="btn-ghost"
            onClick={onToggle}
            title="Collapse"
          >
            <ChevronRight size={12} className="rotate-90" />
          </button>
        </>
      }
    >
      {correlationId ? (
        <div className="flex items-baseline gap-3 border-b border-zinc-800 px-4 py-1.5">
          <span className="label">correlation</span>
          <Id value={correlationId} className="text-xs text-zinc-300" />
        </div>
      ) : null}

      {!chain && loading ? <Skeleton rows={6} /> : null}
      {!chain && !loading ? (
        <Empty>Every event in a run is written here, in order, as it happens.</Empty>
      ) : null}

      {chain && tab === 'events' ? <Events chain={chain} /> : null}
      {chain && tab === 'artifacts' ? <Artifacts chain={chain} /> : null}
    </Panel>
  )
}

function Tab({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`mono h-6 shrink-0 rounded-input px-2 text-xs uppercase tracking-wider transition-colors duration-150 ease-out ${
        active
          ? 'bg-zinc-800 text-zinc-100'
          : 'text-zinc-500 hover:bg-zinc-800 hover:text-zinc-300'
      }`}
    >
      {children}
    </button>
  )
}

function Events({ chain }: { chain: AuditChain }) {
  return (
    <ol>
      {chain.events.map((event) => (
        <EventRow key={event.seq} event={event} />
      ))}
    </ol>
  )
}

function EventRow({ event }: { event: AuditChain['events'][number] }) {
  const [open, setOpen] = useState(false)
  const tone = FAIL_EVENTS.has(event.event_type)
    ? 'text-fail'
    : PASS_EVENTS.has(event.event_type)
      ? 'text-pass'
      : 'text-zinc-200'

  return (
    <li className="border-b border-zinc-800/60 last:border-b-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-baseline gap-3 px-4 py-1.5 text-left transition-colors duration-150 ease-out hover:bg-zinc-900"
      >
        <ChevronRight
          size={12}
          className={`shrink-0 self-center text-zinc-600 transition-transform duration-150 ease-out ${
            open ? 'rotate-90' : ''
          }`}
        />
        <span className="mono w-8 shrink-0 text-xs text-zinc-600">{event.seq}</span>
        <span className={`mono min-w-0 flex-1 truncate text-sm ${tone}`}>
          {event.event_type}
        </span>
        <span className="mono shrink-0 text-xs uppercase tracking-wider text-zinc-600">
          {event.actor}
        </span>
        <span className="mono shrink-0 text-xs text-zinc-700">
          {clockTime(event.created_at)}
        </span>
      </button>

      {open ? (
        <div className="well mx-4 mb-3 overflow-x-auto p-3">
          <pre className="mono whitespace-pre text-xs leading-5 text-zinc-400">
            {JSON.stringify(event.payload, null, 2)}
          </pre>
        </div>
      ) : null}
    </li>
  )
}

function Artifacts({ chain }: { chain: AuditChain }) {
  const quote = chain.quotes[chain.quotes.length - 1] ?? null
  const mandate = chain.mandates[chain.mandates.length - 1] ?? null
  const order = chain.orders[chain.orders.length - 1] ?? null

  return (
    <dl className="px-4 py-3">
      <p className="label mb-1.5">quote — signed by the merchant (Ed25519)</p>
      {quote ? (
        <>
          <Row label="quote">
            <Id value={quote.id} />
          </Row>
          <Row label="total">{rupees(quote.total_paise)}</Row>
          <Row label="status">{quote.status}</Row>
          <Row label="expires">{dateTime(quote.expires_at)}</Row>
          <Row label="signature">{shortHex(quote.signature)}</Row>
        </>
      ) : (
        <p className="py-0.5 text-xs text-zinc-600">none yet</p>
      )}

      <p className="label mb-1.5 mt-4">
        mandate — signed by the Mandate Authority (Ed25519)
      </p>
      {mandate ? (
        <>
          <Row label="mandate">
            <Id value={mandate.id} />
          </Row>
          <Row label="status">
            <Badge tone={mandate.status === 'ACTIVE' ? 'pass' : 'neutral'}>
              {mandate.status}
            </Badge>
          </Row>
          <Row label="per-transaction">{rupees(mandate.max_amount_paise)}</Row>
          <Row label="cumulative">{rupees(mandate.cumulative_cap_paise)}</Row>
          <Row label="expires">{dateTime(mandate.expires_at)}</Row>
          <Row label="signature">{shortHex(mandate.signature)}</Row>
        </>
      ) : (
        <p className="py-0.5 text-xs text-zinc-600">none yet</p>
      )}

      <p className="label mb-1.5 mt-4">order and webhooks</p>
      {order ? (
        <>
          <Row label="order">
            <Id value={order.id} />
          </Row>
          <Row label="guard decision">
            <Id value={order.guard_decision_id} />
          </Row>
          <Row label="razorpay order">
            <Id value={order.razorpay_order_id ?? '—'} />
          </Row>
          <Row label="amount">{rupees(order.amount_paise)}</Row>
        </>
      ) : (
        <p className="py-0.5 text-xs text-zinc-600">none yet</p>
      )}

      {chain.webhook_events.length ? (
        <ul className="mt-2">
          {chain.webhook_events.map((w) => (
            <li
              key={w.id}
              className="mono flex items-baseline gap-3 border-t border-zinc-800/60 py-1.5 text-xs"
            >
              <span className="min-w-0 flex-1 truncate text-zinc-300">
                {w.event_type}
              </span>
              <span className={w.signature_valid ? 'text-pass' : 'text-fail'}>
                {w.signature_valid ? 'signature ok' : 'signature invalid'}
              </span>
              <span className={w.was_duplicate ? 'text-zinc-500' : 'text-zinc-600'}>
                {w.was_duplicate ? 'duplicate ignored' : 'processed'}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </dl>
  )
}
