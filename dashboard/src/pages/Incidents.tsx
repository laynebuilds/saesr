import { useState } from 'react'
import { useIncidents } from '../hooks/useIncidents'
import { ACTIVE_STATUSES, type IncidentView } from '../contracts/incident'
import { Card, Empty } from '../components/ui'
import { IncidentRow } from '../components/incident-bits'
import { cn } from '../lib/cn'

const FILTERS: { key: string; label: string; test: (i: IncidentView) => boolean }[] = [
  { key: 'all', label: 'All', test: () => true },
  { key: 'active', label: 'Active', test: (i) => ACTIVE_STATUSES.includes(i.status) },
  { key: 'approval', label: 'Awaiting approval', test: (i) => i.status === 'awaiting_approval' },
  { key: 'resolved', label: 'Fixed and verified', test: (i) => i.status === 'resolved' },
  { key: 'dismissed', label: 'Dismissed', test: (i) => i.status === 'dismissed' },
]

export function Incidents() {
  const incidents = useIncidents()
  const [f, setF] = useState('all')
  const filter = FILTERS.find((x) => x.key === f)!
  const rows = incidents.filter(filter.test)
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-1">
        {FILTERS.map((x) => (
          <button
            key={x.key}
            onClick={() => setF(x.key)}
            className={cn('rounded-lg px-3 py-1.5 text-sm text-muted hover:text-fg', f === x.key && 'bg-subtle text-fg')}
          >
            {x.label} <span className="tabular-nums text-muted">{incidents.filter(x.test).length}</span>
          </button>
        ))}
      </div>
      <Card className="divide-y divide-line overflow-hidden">
        {rows.map((i) => (
          <IncidentRow key={i.id} inc={i} />
        ))}
        {rows.length === 0 && <Empty>Nothing here.</Empty>}
      </Card>
    </div>
  )
}
