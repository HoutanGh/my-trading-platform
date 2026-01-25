import { useEffect, useMemo, useState } from 'react'
import './App.css'

type DailyPnl = {
  account: string
  trade_date: string
  realized_pnl: number
  source?: string
  trade_count?: number
  trades?: number
}

type DayCell = {
  label: string
  value?: number
  iso?: string
  weekday?: number
  trades?: number
}

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'
const DEFAULT_ACCOUNT = import.meta.env.VITE_DEFAULT_ACCOUNT ?? 'live'

function toIsoDate(year: number, monthIndex: number, day: number): string {
  // Month index is zero-based; use UTC to avoid timezone drift.
  const d = new Date(Date.UTC(year, monthIndex, day))
  return d.toISOString().slice(0, 10)
}

function formatMoney(value: number): string {
  return value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

function App() {
  const [monthCursor, setMonthCursor] = useState(() => {
    const now = new Date()
    return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1))
  })
  const [account, setAccount] = useState<string>(DEFAULT_ACCOUNT)
  const [rows, setRows] = useState<DailyPnl[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const year = monthCursor.getUTCFullYear()
  const month = monthCursor.getUTCMonth()
  const monthName = monthCursor.toLocaleString('en-US', { month: 'long', timeZone: 'UTC' })
  const daysInMonth = new Date(Date.UTC(year, month + 1, 0)).getUTCDate()
  const startWeekday = new Date(Date.UTC(year, month, 1)).getUTCDay() // 0=Sun

  const dailyByDate = useMemo(() => {
    const map: Record<string, { pnl: number; trades?: number }> = {}
    rows.forEach((r) => {
      const trades = typeof r.trade_count === 'number' ? r.trade_count : typeof r.trades === 'number' ? r.trades : undefined
      map[r.trade_date] = { pnl: r.realized_pnl, trades }
    })
    return map
  }, [rows])

  const monthTotal = useMemo(
    () => rows.reduce((sum, r) => sum + r.realized_pnl, 0),
    [rows]
  )

  const weeklyTotals = useMemo(() => {
    const totals: number[] = []
    for (let day = 1; day <= daysInMonth; day += 1) {
      const iso = toIsoDate(year, month, day)
      const weekIdx = Math.floor((startWeekday + day - 1) / 7)
      totals[weekIdx] = (totals[weekIdx] ?? 0) + (dailyByDate[iso]?.pnl ?? 0)
    }
    return totals
  }, [startWeekday, daysInMonth, year, month, dailyByDate])

  useEffect(() => {
    const acct = account.trim()
    if (!acct) {
      setRows([])
      return
    }

    const controller = new AbortController()
    const fetchPnl = async () => {
      setLoading(true)
      setError(null)
      const startDate = toIsoDate(year, month, 1)
      const endDate = toIsoDate(year, month, daysInMonth)
      try {
        const url = new URL('/pnl/daily', API_BASE)
        url.searchParams.set('account', acct)
        url.searchParams.set('start_date', startDate)
        url.searchParams.set('end_date', endDate)
        const res = await fetch(url, { signal: controller.signal })
        if (!res.ok) {
          throw new Error(`API ${res.status}: ${res.statusText}`)
        }
        const data = (await res.json()) as DailyPnl[]
        setRows(data)
      } catch (err) {
        if ((err as Error).name === 'AbortError') return
        setError((err as Error).message || 'Unknown error')
        setRows([])
      } finally {
        setLoading(false)
      }
    }

    fetchPnl()
    return () => controller.abort()
  }, [account, year, month, daysInMonth])

  const handlePrevMonth = () =>
    setMonthCursor((prev) => new Date(Date.UTC(prev.getUTCFullYear(), prev.getUTCMonth() - 1, 1)))

  const handleNextMonth = () =>
    setMonthCursor((prev) => new Date(Date.UTC(prev.getUTCFullYear(), prev.getUTCMonth() + 1, 1)))

  const dayCells = useMemo(() => {
    const cells: DayCell[] = []
    for (let i = 0; i < startWeekday; i += 1) {
      cells.push({ label: '' })
    }
    for (let day = 1; day <= daysInMonth; day += 1) {
      const iso = toIsoDate(year, month, day)
      const weekday = new Date(iso).getUTCDay()
      const daily = dailyByDate[iso]
      cells.push({ label: String(day), value: daily?.pnl, trades: daily?.trades, iso, weekday })
    }
    return cells
  }, [startWeekday, daysInMonth, year, month, dailyByDate])

  const weeks = useMemo(() => {
    const padded = [...dayCells]
    const totalWeeks = Math.ceil(padded.length / 7) || 1
    const missing = totalWeeks * 7 - padded.length
    for (let i = 0; i < missing; i += 1) {
      padded.push({ label: '' })
    }
    const list: Array<{ cells: DayCell[]; total: number }> = []
    for (let i = 0; i < totalWeeks; i += 1) {
      const slice = padded.slice(i * 7, i * 7 + 7)
      list.push({ cells: slice, total: weeklyTotals[i] ?? 0 })
    }
    return list
  }, [dayCells, weeklyTotals])

  const statusText = useMemo(() => {
    if (!account.trim()) return 'Enter an account to load data.'
    if (loading) return 'Loading P&L...'
    if (error) return `Error: ${error}`
    return `Showing ${rows.length} entries for ${monthName} ${year}`
  }, [account, loading, error, rows.length, monthName, year])

  return (
    <div className="app">
      <header className="header">
        <div>
          <p className="eyebrow">Daily P&amp;L Calendar</p>
          <h1>
            {monthName} {year}
          </h1>
        </div>
        <div className="controls">
          <label className="control">
            <span>Account</span>
            <input
              value={account}
              onChange={(e) => setAccount(e.target.value)}
              placeholder="e.g. DU12345"
            />
          </label>
          <label className="control">
            <span>API base</span>
            <input value={API_BASE} readOnly />
          </label>
        </div>
      </header>

      <div className="calendar-card">
        <div className="calendar-nav">
          <button onClick={handlePrevMonth} aria-label="Previous month">
            ←
          </button>
          <div className="month-label">
            {monthName} {year}
          </div>
          <button onClick={handleNextMonth} aria-label="Next month">
            →
          </button>
        </div>

        <div className="status">{statusText}</div>

        <div className="summary-row">
          <div className="summary-card">
            <p className="summary-label">Monthly total</p>
            <p className="summary-value">{formatMoney(monthTotal)}</p>
          </div>
        </div>

        <div className="weekday-row">
          {['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map((d) => (
            <div key={d} className="weekday">
              {d}
            </div>
          ))}
          <div className="weekday total-col">Week total</div>
        </div>

        <div className="calendar-weeks">
          {weeks.map((week, wIdx) => (
            <div className="week-row" key={`week-${wIdx}`}>
              {week.cells.map((cell, idx) => {
                const classNames = ['day-cell']
                if (cell.weekday === 0 || cell.weekday === 6) {
                  classNames.push('weekend')
                }
                if (cell.value !== undefined) {
                  if (cell.value > 0) classNames.push('positive')
                  else if (cell.value < 0) classNames.push('negative')
                  else classNames.push('flat')
                } else if (!cell.label) {
                  classNames.push('empty')
                }

                return (
                  <div key={`${cell.iso ?? 'pad'}-${idx}`} className={classNames.join(' ')}>
                    {cell.label && <div className="day-number">{cell.label}</div>}
                    {cell.value !== undefined ? (
                      <div className="pnl">{formatMoney(cell.value)}</div>
                    ) : cell.label ? (
                      <div className="pnl muted">—</div>
                    ) : null}
                    {cell.trades !== undefined && cell.label ? (
                      <div className="trades">{cell.trades} trade{cell.trades === 1 ? '' : 's'}</div>
                    ) : null}
                  </div>
                )
              })}
              <div
                className={[
                  'week-total',
                  week.total > 0 ? 'positive' : week.total < 0 ? 'negative' : 'flat',
                ].join(' ')}
              >
                <span>Week {wIdx + 1}</span>
                <strong>{formatMoney(week.total)}</strong>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

export default App
