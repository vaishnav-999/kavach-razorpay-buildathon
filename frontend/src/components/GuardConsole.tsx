// §9, §14 — the screen the demo rests on, and the dominant element on it the
// moment a decision lands.
//
// All nine rules, every time, in id order, on ALLOW as well as BLOCK
// (invariant 10). A passing rule carries its observed value, its threshold, its
// unit and its detail exactly as a failing one does, and showing them is what
// makes the Guard auditable rather than merely restrictive: eight green rows
// with real numbers in them are the evidence that the ninth red row means
// something.
//
// Nothing on this screen is computed here. Every field is read from the §9.3
// decision the Guard wrote to `guard_decisions` and the audit chain returned.
//
// LAYOUT RULE: all nine rules are visible without scrolling at 1080p. The
// verdict headline, the decision strip, the column header and nine rows are
// budgeted to roughly 830px, and that is the constraint every padding value in
// this file answers to. This is a dense instrument reading. Air separates the
// verdict from the evidence, never one rule row from the next.

import { ShieldCheck, ShieldX, Shield } from 'lucide-react'
import type { GuardDecision, GuardRule } from '../types'
import { clockTime, rupees, ruleValue } from '../lib/format'
import { Badge, Empty, Id, Panel, Skeleton } from './ui'

const RULE_ORDER = [
  'MG-001',
  'MG-002',
  'MG-003',
  'MG-004',
  'MG-005',
  'MG-006',
  'MG-007',
  'MG-008',
  'MG-009',
]

function ordered(rules: GuardRule[]): GuardRule[] {
  return [...rules].sort(
    (a, b) => RULE_ORDER.indexOf(a.rule_id) - RULE_ORDER.indexOf(b.rule_id),
  )
}

export default function GuardConsole({
  decision,
  loading,
}: {
  decision: GuardDecision | null
  loading: boolean
}) {
  const blocked = decision?.verdict === 'BLOCK'

  return (
    <Panel
      title="Transaction Guard"
      icon={
        blocked ? (
          <ShieldX size={14} className="text-fail" />
        ) : decision ? (
          <ShieldCheck size={14} className="text-pass" />
        ) : (
          <Shield size={14} />
        )
      }
      accent={decision ? (blocked ? 'fail' : 'pass') : 'none'}
      bodyClassName="overflow-hidden"
      right={
        decision ? (
          <span className="mono text-xs text-zinc-500">
            9 rules · {decision.duration_ms} ms · {clockTime(decision.evaluated_at)}
          </span>
        ) : (
          <span className="mono text-xs text-zinc-600">9 rules</span>
        )
      }
    >
      {!decision && loading ? <Skeleton rows={9} /> : null}

      {!decision && !loading ? (
        <Empty>
          No submission has been evaluated yet. Nine rules run on every
          submit_purchase, before any order can exist.
        </Empty>
      ) : null}

      {decision ? <Decision key={decision.decision_id} decision={decision} /> : null}
    </Panel>
  )
}

function Decision({ decision }: { decision: GuardDecision }) {
  const rules = ordered(decision.rules)
  const failed = rules.find((r) => r.rule_id === decision.failed_rule_id) ?? null
  const passedCount = rules.filter((r) => r.passed).length
  const blocked = decision.verdict === 'BLOCK'

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* THE ANSWER TO "WHAT JUST HAPPENED?" — the first thing a viewer who has
          never seen this screen reads, and the only 32px type in the app. */}
      <div
        className={`shrink-0 border-b border-zinc-800 px-4 py-2 ${
          blocked ? 'bg-fail/[0.07]' : ''
        }`}
      >
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <h3
            className={`mono text-2xl font-semibold tracking-tight ${
              blocked ? 'text-fail' : 'text-pass'
            }`}
          >
            {blocked ? 'BLOCKED' : 'ALLOWED'}
          </h3>
          {blocked && failed ? (
            <p className="mono text-sm font-medium text-fail">
              {failed.rule_id} · {decision.block_code}
            </p>
          ) : (
            <p className="mono text-sm text-zinc-400">
              {passedCount} / {rules.length} rules passed ·{' '}
              <span className="text-zinc-100">
                {rupees(decision.requested_total_paise)}
              </span>{' '}
              {decision.currency}
            </p>
          )}
          <Badge tone={blocked ? 'fail' : 'pass'}>{decision.verdict}</Badge>
        </div>

        <p className="mt-1 max-w-[86ch] text-base leading-6 text-zinc-100">
          {blocked && failed
            ? failed.detail
            : 'Every rule below ran against the signed quote and the signed ' +
              'mandate.'}
        </p>

        {/* One line at 1000px and up. Uncapped on purpose: this is the
            footnote, and a second line here costs a rule row. */}
        <p className="mt-1 truncate text-xs leading-5 text-zinc-500">
          {blocked
            ? 'No Razorpay order was created. All nine rules ran; failed_rule_id ' +
              'is the lowest-numbered failure, and every failure is shown below.'
            : 'A Razorpay order exists only because this verdict is ALLOW. ' +
              'orders.guard_decision_id is NOT NULL and points at the id below.'}
        </p>
      </div>

      {/* The four identifiers that tie this verdict to the rest of the record. */}
      <dl className="grid shrink-0 grid-cols-2 gap-x-6 border-b border-zinc-800 px-4 py-1 xl:grid-cols-4">
        <Meta label="decision">
          <Id value={decision.decision_id} className="text-xs" />
        </Meta>
        <Meta label="requested">
          <span className="mono text-xs text-zinc-200">
            {rupees(decision.requested_total_paise)}
          </span>
        </Meta>
        <Meta label="mandate">
          <Id value={decision.mandate_id ?? '—'} className="text-xs" />
        </Meta>
        <Meta label="quote">
          <Id value={decision.quote_id ?? '—'} className="text-xs" />
        </Meta>
      </dl>

      <div className="rule-grid shrink-0 border-b border-zinc-800 bg-zinc-900/40 px-4 py-1.5">
        <span />
        <span className="label">rule</span>
        <span className="label">check</span>
        <span className="label">observed</span>
        <span className="label">threshold</span>
      </div>

      <ol className="min-h-0 flex-1 overflow-y-auto">
        {rules.map((rule, index) => (
          <RuleRow key={rule.rule_id} rule={rule} index={index} />
        ))}
      </ol>
    </div>
  )
}

function Meta({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex min-w-0 items-baseline gap-2 py-0.5">
      <dt className="label">{label}</dt>
      <dd className="min-w-0 flex-1 truncate">{children}</dd>
    </div>
  )
}

function RuleRow({ rule, index }: { rule: GuardRule; index: number }) {
  const failed = !rule.passed
  return (
    <li
      // 45ms apart, so nine rules read as an evaluation happening rather than a
      // table appearing, and the whole set has landed inside 550ms. The
      // animation is keyed to the decision id upstream, so it runs once per
      // decision and never on a re-render.
      style={{ animationDelay: `${index * 45}ms` }}
      className={`rule-grid animate-rule-in border-b border-zinc-800/60 px-4 py-1.5 last:border-b-0 ${
        failed ? 'bg-fail/[0.07]' : ''
      }`}
    >
      <span
        aria-hidden
        className={`mt-1.5 h-1.5 w-1.5 rounded-full ${failed ? 'bg-fail' : 'bg-pass'}`}
      />

      <span
        className={`mono text-sm font-medium ${failed ? 'text-fail' : 'text-pass'}`}
      >
        {rule.rule_id}
      </span>

      <span className="min-w-0">
        <span className="flex flex-wrap items-baseline gap-x-3">
          <span className="mono text-sm text-zinc-200">{rule.name}</span>
          {failed && rule.block_code ? (
            <span className="mono text-sm font-medium text-fail">
              {rule.block_code}
            </span>
          ) : null}
        </span>
        {/* Readable from across a room: 14px, not 12px. */}
        <span className="mt-0.5 block text-sm leading-5 text-zinc-400">
          {rule.detail}
        </span>
      </span>

      <Cell value={rule.observed} unit={rule.unit} failed={failed} />
      <Cell value={rule.threshold} unit={rule.unit} failed={false} />
    </li>
  )
}

// Observed and threshold sit in fixed columns so the eye can run straight down
// either one. The unit rides inline rather than on a second line — nine rules
// times one saved line is most of a rule row.
function Cell({
  value,
  unit,
  failed,
}: {
  value: unknown
  unit: string
  failed: boolean
}) {
  return (
    <span className="mono block min-w-0 text-sm">
      <span className={`break-words ${failed ? 'text-fail' : 'text-zinc-200'}`}>
        {ruleValue(value, unit)}
      </span>
      {unit ? (
        <span className="ml-1.5 whitespace-nowrap text-xs text-zinc-600">{unit}</span>
      ) : null}
    </span>
  )
}
