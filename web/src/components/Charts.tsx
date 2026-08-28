/**
 * Charts drawn as inline SVG.
 *
 * No chart library is installed, and the brief said not to add one, so these are
 * small hand-drawn marks rather than a dependency. They are deliberately plain:
 * a bar's length and a line's height are the only encodings, and every value is
 * printed next to its mark so the chart is never the sole source of a number.
 */

const BAR_COLOURS: Record<string, string> = {
  problem: 'var(--color-flag)',
  warning: 'var(--color-caution)',
  neutral: 'var(--color-seal)',
  unknown: 'var(--color-unknown)',
}

export function BarList({
  rows,
  tone = 'neutral',
  emptyLabel = 'Nothing recorded yet',
}: {
  rows: readonly { label: string; value: number }[]
  tone?: keyof typeof BAR_COLOURS
  emptyLabel?: string
}) {
  if (rows.length === 0) {
    return <p className="t-small text-muted">{emptyLabel}</p>
  }
  const max = Math.max(...rows.map((row) => row.value), 1)
  return (
    <ul className="space-y-2.5">
      {rows.map((row) => (
        <li key={row.label}>
          <div className="flex items-baseline justify-between gap-3">
            <span className="t-small truncate text-ink">{row.label}</span>
            <span className="t-data text-muted">{row.value}</span>
          </div>
          <div className="mt-1 h-1.5 w-full rounded-full bg-ink-100">
            <div
              className="h-full rounded-full"
              style={{
                width: `${Math.max(2, (row.value / max) * 100)}%`,
                backgroundColor: BAR_COLOURS[tone],
              }}
            />
          </div>
        </li>
      ))}
    </ul>
  )
}

/** Scans per day. A single quiet line; no area fill, no gradient. */
export function TrendLine({ points }: { points: readonly { day: string; count: number }[] }) {
  if (points.length < 2) {
    return (
      <p className="t-small text-muted">
        A trend appears once there are scans on more than one day.
      </p>
    )
  }
  const width = 640
  const height = 120
  const pad = 8
  const max = Math.max(...points.map((p) => p.count), 1)
  const step = (width - pad * 2) / (points.length - 1)
  const coords = points.map((point, index) => {
    const x = pad + index * step
    const y = height - pad - (point.count / max) * (height - pad * 2)
    return `${x.toFixed(1)},${y.toFixed(1)}`
  })

  return (
    <figure>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="h-28 w-full"
        role="img"
        aria-label={`Scans per day, ${points.length} days, peak ${max}`}
      >
        <polyline
          points={coords.join(' ')}
          fill="none"
          stroke="var(--color-seal)"
          strokeWidth="1.5"
          strokeLinejoin="round"
        />
        {points.map((point, index) => {
          const [x, y] = (coords[index] ?? '0,0').split(',')
          return <circle key={point.day} cx={x} cy={y} r="2" fill="var(--color-seal)" />
        })}
      </svg>
      <figcaption className="t-small mt-1 flex justify-between text-muted">
        <span>{points[0]?.day}</span>
        <span>peak {max} per day</span>
        <span>{points[points.length - 1]?.day}</span>
      </figcaption>
    </figure>
  )
}
