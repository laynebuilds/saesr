import { useEffect, useState, type ReactNode } from 'react'
import { NavLink, Link } from 'react-router-dom'
import { Activity, BarChart3, ClipboardCheck, Bot, Cctv, ChevronLeft, ChevronRight, LayoutGrid, Moon, ScanSearch, RotateCcw, Siren, Sun, WifiOff, X, Zap } from 'lucide-react'
import { useDash, clearRecovery } from '../data/store'
import { siteVersion } from '../data/adapt'
import { useTheme } from '../hooks/useTheme'
import { CLOUD, DEMO, MOCK } from '../data/config'
import { source } from '../data'
import { cn } from '../lib/cn'
import { Button, Dot } from './ui'
import { HealthDrawer } from './HealthDrawer'

const NAV = [
  { to: '/', label: 'Overview', icon: LayoutGrid, end: true },
  { to: '/cameras', label: 'Cameras', icon: Cctv },
  { to: '/incidents', label: 'Incidents', icon: Siren },
  { to: '/review', label: 'Review footage', icon: ScanSearch },
  { to: '/log', label: 'Compliance log', icon: ClipboardCheck },
  { to: '/agents', label: 'Agent relay', icon: Bot },
  { to: '/history', label: 'Analytics', icon: BarChart3 },
]

function useSidebar(): [boolean, () => void] {
  const [c, setC] = useState(() => {
    try {
      return localStorage.getItem('saesr-sidebar') === 'collapsed'
    } catch {
      return false
    }
  })
  const toggle = () =>
    setC((v) => {
      try {
        localStorage.setItem('saesr-sidebar', v ? 'expanded' : 'collapsed')
      } catch {
        /* private mode */
      }
      return !v
    })
  return [c, toggle]
}

function Clock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(t)
  }, [])
  return <span className="tnum font-mono text-xs text-muted">{now.toLocaleTimeString([], { hour12: false })}</span>
}

function ConnPill() {
  const conn = useDash((s) => s.conn)
  const version = useDash((s) => siteVersion(s.site))
  const map = {
    live: ['var(--ready)', 'LIVE'],
    connecting: ['var(--text-dim)', 'CONNECTING'],
    degraded: ['var(--watch)', 'DEGRADED'],
    reconnecting: ['var(--critical)', 'RECONNECTING'],
  } as const
  const [color, label] = map[conn]
  return (
    <span className="flex items-center gap-2 rounded-md border border-line px-2 py-1 font-mono text-[10px] tracking-wider text-muted">
      <Dot color={color} pulse={conn !== 'live'} />
      {label}
      {version != null && <span className="text-dim">v{version}</span>}
      {MOCK && <span className="text-warn">MOCK</span>}
      {CLOUD && <span className="text-ok">REAL DATA</span>}
    </span>
  )
}

function ConnectionBanner() {
  const conn = useDash((s) => s.conn)
  const err = useDash((s) => s.lastError)
  const site = useDash((s) => s.site)
  if (conn !== 'reconnecting' && conn !== 'degraded') return null
  return (
    <div className="rise flex items-center gap-2 border-b border-bad/30 bg-bad-bg px-6 py-2 text-sm">
      <WifiOff className="size-4 text-bad" />
      <span className="font-medium text-bad">{conn === 'reconnecting' ? 'Reconnecting to the actor service' : 'Actor service unreachable'}</span>
      <span className="text-muted">
        {err ? `${err}. ` : ''}Holding {site?.cameras.reduce((n, c) => n + c.incidents.length, 0) ?? 0} incidents at version {siteVersion(site)}. Retrying with backoff.
      </span>
    </div>
  )
}

function RecoveredToast() {
  const rec = useDash((s) => s.recovery)
  useEffect(() => {
    if (!rec) return
    const t = setTimeout(clearRecovery, 9000)
    return () => clearTimeout(t)
  }, [rec])
  if (!rec) return null
  return (
    <div className="rise glass fixed bottom-5 right-5 z-50 w-[23rem] rounded-xl border-ok/40 p-4">
      <div className="flex items-start gap-3">
        <RotateCcw className="mt-0.5 size-4 text-ok" />
        <div className="flex-1 text-sm">
          <div className="font-semibold">Recovered, state intact</div>
          <div className="mt-1 text-muted">
            Back after {(rec.down_ms / 1000).toFixed(1)} s. The Site actor resumed at version {rec.to_version} (last seen {rec.from_version}) with all{' '}
            {rec.incidents} incidents preserved.
          </div>
        </div>
        <button onClick={clearRecovery} className="text-dim hover:text-fg" aria-label="Dismiss">
          <X className="size-4" />
        </button>
      </div>
    </div>
  )
}

function DemoControls() {
  if (!DEMO) return null
  return (
    <div className="glass fixed bottom-5 left-1/2 z-40 flex -translate-x-1/2 items-center gap-2 rounded-xl p-1.5 text-xs">
      <Zap className="ml-1 size-3.5 text-warn" />
      <Button onClick={() => source.crash?.()} title={MOCK ? 'Simulated in mock mode' : 'POST /admin/crash?target=actors (run_demo.sh restarts it)'}>
        Crash actor service
      </Button>
    </div>
  )
}

function ThemeToggle() {
  const { theme, setTheme } = useTheme()
  return (
    <Button variant="ghost" className="w-8 px-0" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')} aria-label="Toggle theme" title="Toggle theme">
      {theme === 'dark' ? <Sun className="size-4" /> : <Moon className="size-4" />}
    </Button>
  )
}

export function Shell({ children }: { children: ReactNode }) {
  const [health, setHealth] = useState(false)
  const [collapsed, toggle] = useSidebar()
  const site = useDash((s) => s.site)
  return (
    <div className="flex min-h-full">
      <aside className={cn('sticky top-0 flex h-screen shrink-0 flex-col border-r border-line bg-card/60 backdrop-blur', collapsed ? 'w-16' : 'w-56')}>
        {/* the logo goes to the homepage (saesr.com when the dashboard is served at /demo) */}
        <a href="/" title="saesr home" className={cn('flex h-14 items-center gap-2.5 border-b border-line hover:opacity-80', collapsed ? 'justify-center' : 'px-4')}>
          <span className="grid size-7 shrink-0 place-items-center rounded-md bg-fg font-mono text-xs font-bold text-bg">S</span>
          {!collapsed && (
            <div className="min-w-0 leading-tight">
              <div className="text-[15px] font-bold tracking-tight">saesr</div>
              <div className="label !text-[9px] !tracking-[0.18em]">Safety command center</div>
            </div>
          )}
        </a>
        <nav className="flex-1 space-y-0.5 p-2">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              title={collapsed ? n.label : undefined}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-3 rounded-md py-2 text-sm transition-colors',
                  collapsed ? 'justify-center' : 'px-3',
                  isActive ? 'bg-accent-bg text-accent' : 'text-muted hover:bg-subtle hover:text-fg',
                )
              }
            >
              <n.icon className="size-[17px] shrink-0" strokeWidth={1.7} />
              {!collapsed && n.label}
            </NavLink>
          ))}
        </nav>
        <div className={cn('flex border-t border-line p-2', collapsed ? 'justify-center' : 'justify-end')}>
          <Button variant="ghost" className="w-8 px-0" onClick={toggle} aria-label="Toggle sidebar">
            {collapsed ? <ChevronRight className="size-4" /> : <ChevronLeft className="size-4" />}
          </Button>
        </div>
      </aside>

      <div className="min-w-0 flex-1">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-line bg-bg/70 px-6 backdrop-blur">
          <Link to="/" className="font-mono text-xs text-muted">
            {site?.site_id ?? 'site-demo'}
          </Link>
          <span className="text-dim">/</span>
          <Clock />
          <div className="ml-auto flex items-center gap-2">
            <ConnPill />
            <Button variant="ghost" className="w-8 px-0" onClick={() => setHealth(true)} title="System health" aria-label="System health">
              <Activity className="size-4" />
            </Button>
            <ThemeToggle />
          </div>
        </header>
        <ConnectionBanner />
        <main className="px-6 py-6">{children}</main>
      </div>
      <RecoveredToast />
      <DemoControls />
      <HealthDrawer open={health} onClose={() => setHealth(false)} />
    </div>
  )
}
