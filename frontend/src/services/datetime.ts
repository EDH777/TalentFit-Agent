/** SQLite stores UTC timestamps; older API responses may omit the UTC suffix. */
function toDate(value: string): Date {
  const timestamp = /(?:Z|[+-]\d{2}:\d{2})$/.test(value) ? value : value + 'Z'
  return new Date(timestamp)
}

export function formatDateTime(value: string): string {
  const date = toDate(value)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat('sv-SE', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).format(date)
}

export function formatTime(value: string): string {
  const date = toDate(value)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(date)
}

/** Business dates are plain YYYY-MM-DD strings; only array math is needed here. */
export function addDays(value: string, days: number): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return value
  const date = new Date(value + 'T00:00:00Z')
  if (Number.isNaN(date.getTime())) return value
  date.setUTCDate(date.getUTCDate() + days)
  return date.toISOString().slice(0, 10)
}

/** The API stores an exclusive end date; people read inclusive ranges. */
export function inclusiveEnd(endExclusive: string): string {
  return addDays(endExclusive, -1)
}

export function exclusiveEnd(endInclusive: string): string {
  return addDays(endInclusive, 1)
}

export function formatRange(start: string, endExclusive: string): string {
  return `${start} 至 ${inclusiveEnd(endExclusive)}`
}
