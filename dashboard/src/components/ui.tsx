import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from 'react'
import { cn } from '../lib/cn'

export function Card({ className, ...p }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('glass rounded-xl', className)} {...p} />
}

export function CardHeader({ title, right, className }: { title: ReactNode; right?: ReactNode; className?: string }) {
  return (
    <div className={cn('flex items-center justify-between gap-3 border-b border-line px-4 py-2.5', className)}>
      <h2 className="label !text-muted">{title}</h2>
      {right && <div className="text-xs text-dim">{right}</div>}
    </div>
  )
}

export function Label({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('label', className)}>{children}</div>
}

type Variant = 'primary' | 'ghost' | 'outline'
export function Button({ className, variant = 'outline', ...p }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return (
    <button
      className={cn(
        'inline-flex h-8 items-center justify-center gap-1.5 rounded-md px-3 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50',
        variant === 'primary' && 'bg-accent text-accent-fg hover:brightness-110',
        variant === 'outline' && 'border border-line bg-card hover:border-accent hover:text-accent',
        variant === 'ghost' && 'text-muted hover:bg-subtle hover:text-fg',
        className,
      )}
      {...p}
    />
  )
}

export function Pill({ className, ...p }: HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn('inline-flex items-center gap-1.5 whitespace-nowrap rounded-full bg-subtle px-2 py-0.5 text-[11px] font-semibold', className)}
      {...p}
    />
  )
}

export function Dot({ color, pulse, className }: { color: string; pulse?: boolean; className?: string }) {
  return <span className={cn('inline-block size-1.5 shrink-0 rounded-full', pulse && 'pulse-dot', className)} style={{ background: color }} />
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="px-4 py-10 text-center text-sm text-dim">{children}</div>
}

export type Tone = 'accent' | 'ok' | 'warn' | 'bad' | 'muted'
export const TONE: Record<Tone, string> = {
  accent: 'var(--accent)',
  ok: 'var(--ready)',
  warn: 'var(--watch)',
  bad: 'var(--critical)',
  muted: 'var(--text-dim)',
}

/** Big instrument readout: mono label, large tabular figure, optional sub line and spark. */
export function Readout({ label, value, sub, tone, spark }: { label: string; value: ReactNode; sub?: ReactNode; tone?: Tone; spark?: ReactNode }) {
  return (
    <Card className="flex flex-col justify-between px-4 py-3.5">
      <Label>{label}</Label>
      <div className="mt-2 flex items-end justify-between gap-2">
        <div className="tnum text-[1.7rem] font-bold leading-none tracking-tight" style={tone ? { color: TONE[tone] } : undefined}>
          {value}
        </div>
        {spark}
      </div>
      {sub && <div className="tnum mt-1.5 text-xs text-dim">{sub}</div>}
    </Card>
  )
}

/** Tiny area sparkline, auto-scaled. */
export function Spark({ values, width = 90, height = 26, tone = 'accent' }: { values: number[]; width?: number; height?: number; tone?: Tone }) {
  if (values.length < 2) return null
  const max = Math.max(...values, 1)
  const x = (i: number) => (i / (values.length - 1)) * width
  const y = (v: number) => height - 2 - (v / max) * (height - 4)
  const line = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
  const id = `sp-${tone}-${width}`
  return (
    <svg width={width} height={height} className="shrink-0 overflow-visible">
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={TONE[tone]} stopOpacity="0.28" />
          <stop offset="100%" stopColor={TONE[tone]} stopOpacity="0" />
        </linearGradient>
      </defs>
      <polygon points={`0,${height} ${line} ${width},${height}`} fill={`url(#${id})`} />
      <polyline points={line} fill="none" stroke={TONE[tone]} strokeWidth="1.5" strokeLinejoin="round" />
      <circle cx={x(values.length - 1)} cy={y(values[values.length - 1])} r="2" fill={TONE[tone]} />
    </svg>
  )
}

export function Donut({
  segments,
  center,
  centerLabel,
  size = 120,
  thickness = 12,
}: {
  segments: { value: number; color: string; label: string }[]
  center: ReactNode
  centerLabel?: string
  size?: number
  thickness?: number
}) {
  const total = segments.reduce((a, s) => a + s.value, 0) || 1
  const r = (size - thickness) / 2
  const c = 2 * Math.PI * r
  let acc = 0
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg width={size} height={size}>
        <g transform={`rotate(-90 ${size / 2} ${size / 2})`}>
          <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--surface-3)" strokeWidth={thickness} />
          {segments
            .filter((s) => s.value > 0)
            .map((s) => {
              const len = (s.value / total) * c
              const el = (
                <circle
                  key={s.label}
                  cx={size / 2}
                  cy={size / 2}
                  r={r}
                  fill="none"
                  stroke={s.color}
                  strokeWidth={thickness}
                  strokeDasharray={`${Math.max(0, len - 1.5).toFixed(2)} ${(c - len + 1.5).toFixed(2)}`}
                  strokeDashoffset={-acc}
                >
                  <title>{`${s.label}: ${s.value}`}</title>
                </circle>
              )
              acc += len
              return el
            })}
        </g>
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="tnum text-2xl font-bold leading-none">{center}</span>
        {centerLabel && <span className="label mt-1">{centerLabel}</span>}
      </div>
    </div>
  )
}

export function Gauge({ value, center, size = 64, thickness = 6, tone = 'accent' }: { value: number; center: ReactNode; size?: number; thickness?: number; tone?: Tone }) {
  const v = Math.max(0, Math.min(1, value))
  const r = (size - thickness) / 2
  const c = 2 * Math.PI * r
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg width={size} height={size}>
        <g transform={`rotate(-90 ${size / 2} ${size / 2})`}>
          <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--surface-3)" strokeWidth={thickness} />
          <circle
            cx={size / 2}
            cy={size / 2}
            r={r}
            fill="none"
            stroke={TONE[tone]}
            strokeWidth={thickness}
            strokeLinecap="round"
            strokeDasharray={`${(v * c).toFixed(2)} ${c.toFixed(2)}`}
            style={{ transition: 'stroke-dasharray .6s var(--ease)' }}
          />
        </g>
      </svg>
      <div className="tnum absolute inset-0 grid place-items-center text-sm font-bold">{center}</div>
    </div>
  )
}

export function Meter({ value, tone = 'accent', className }: { value: number; tone?: Tone; className?: string }) {
  return (
    <div className={cn('h-1.5 overflow-hidden rounded-full bg-raised', className)}>
      <div className="h-full rounded-full" style={{ width: `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`, background: TONE[tone], transition: 'width .6s var(--ease)' }} />
    </div>
  )
}
