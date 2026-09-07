/**
 * Built-up change as a cross-section outward from the shoreline.
 *
 * A single study-area percentage cannot distinguish encroachment from ordinary
 * outward expansion. Binning growth by distance from the water can, and the
 * shape of that gradient is the whole finding, so it is drawn rather than
 * tabulated: distance runs along x, growth along y, zero is a ruled line.
 * Reading left to right answers the question directly.
 */

const VIEW_W = 340
const VIEW_H = 130
const WATER_W = 16
const PLOT_L = WATER_W + 8
const PLOT_R = VIEW_W - 6
const PLOT_T = 18
const PLOT_B = 100
const AXIS_Y = 122

// Where each band begins, measured from the shoreline. The final band is
// open-ended, so all four are drawn at equal width and the true distances are
// carried by these labels rather than by the geometry. Scaling an unbounded
// band against bounded ones would misstate it. The right edge is deliberately
// left unlabelled for the same reason: it has no bound to print.
//
// Labels live inside the SVG so they share the plot's coordinate system. In a
// sibling div they would be spaced across the full container width while the
// bands are inset by PLOT_L, putting every tick off its own boundary.
const AXIS_TICKS = ['0', '500 m', '1 km', '2 km']

export default function WetlandPanel({ wetland }) {
  const { rings, water } = wetland

  const values = rings.map((ring) => ring.growth_pct ?? 0)
  const maxValue = Math.max(...values, 1)
  const minValue = Math.min(...values, 0)
  const span = maxValue - minValue || 1

  const bandWidth = (PLOT_R - PLOT_L) / rings.length
  const yOf = (value) => PLOT_B - ((value - minValue) / span) * (PLOT_B - PLOT_T)
  const zeroY = yOf(0)

  // A stepped path: one flat run per zone, joined by vertical risers. Steps
  // rather than a smooth curve because the measurement is per zone, and a curve
  // would imply we resolved growth continuously across distance. We did not.
  const step = []
  const area = [`M ${PLOT_L} ${zeroY}`]
  rings.forEach((ring, index) => {
    const x0 = PLOT_L + index * bandWidth
    const x1 = x0 + bandWidth
    const y = yOf(ring.growth_pct ?? 0)
    step.push(`${index === 0 ? 'M' : 'L'} ${x0} ${y}`, `L ${x1} ${y}`)
    area.push(`L ${x0} ${y}`, `L ${x1} ${y}`)
  })
  area.push(`L ${PLOT_R} ${zeroY} Z`)

  const inner = rings[0]
  const outer = rings[rings.length - 1]
  const encroaching =
    inner.growth_pct !== null &&
    outer.growth_pct !== null &&
    inner.growth_pct > outer.growth_pct

  return (
    <section className="panel">
      <h2 className="panel-title">Growth by distance from water</h2>

      <div className="profile">
        <svg viewBox={`0 0 ${VIEW_W} ${VIEW_H}`} role="img" aria-label={profileSummary(rings)}>
          {/* The lake itself, anchoring the left edge of the section. */}
          <rect
            className="profile-water"
            x="0"
            y={PLOT_T}
            width={WATER_W}
            height={PLOT_B - PLOT_T}
          />

          {rings.map((_, index) => {
            const x = PLOT_L + index * bandWidth
            return (
              <line
                key={index}
                className="profile-tick"
                x1={x}
                y1={PLOT_T}
                x2={x}
                y2={PLOT_B}
              />
            )
          })}

          <line className="profile-zero" x1={PLOT_L} y1={zeroY} x2={PLOT_R} y2={zeroY} />

          <path className="profile-fill" d={area.join(' ')} />
          <path className="profile-step" d={step.join(' ')} />

          {rings.map((ring, index) => {
            const value = ring.growth_pct ?? 0
            const y = yOf(value)
            return (
              <text
                key={ring.index}
                className="profile-value"
                x={PLOT_L + index * bandWidth + bandWidth / 2}
                y={value >= 0 ? y - 5 : y + 12}
                textAnchor="middle"
              >
                {formatGrowth(ring.growth_pct)}
              </text>
            )
          })}

          {AXIS_TICKS.slice(0, rings.length).map((tick, index) => (
            <text
              key={tick}
              className="profile-axis-tick"
              x={PLOT_L + index * bandWidth}
              y={AXIS_Y}
            >
              {tick}
            </text>
          ))}
        </svg>
      </div>

      <div className="zone-rows">
        {rings.map((ring) => (
          <div key={ring.index} className="zone-row">
            <span className="zone-label">{ring.label}</span>
            <span
              className={
                (ring.growth_pct ?? 0) < 0
                  ? 'zone-growth zone-growth--down'
                  : 'zone-growth zone-growth--up'
              }
            >
              {formatGrowth(ring.growth_pct)}
            </span>
          </div>
        ))}
      </div>

      <p className={encroaching ? 'verdict verdict--warn' : 'verdict'}>
        {encroaching
          ? 'Growth is highest nearest the water. Development is concentrating on the shoreline.'
          : 'Growth is lowest nearest the water. Expansion moved outward rather than onto the shoreline.'}
      </p>

      <h2 className="panel-title">Open water</h2>
      <dl className="stats">
        <div className="stat">
          <dt>Before</dt>
          <dd>{water.area_km2_period1} km²</dd>
        </div>
        <div className="stat">
          <dt>After</dt>
          <dd>{water.area_km2_period2} km²</dd>
        </div>
        <div className="stat">
          <dt>Change</dt>
          <dd>
            {water.change_km2 > 0 ? '+' : ''}
            {water.change_km2} km²
            {water.change_pct !== null ? ` (${water.change_pct}%)` : ''}
          </dd>
        </div>
      </dl>

      <p className="hint">
        Water extent comes from MNDWI on the imaging dates. Lake level swings between years,
        so a change here reflects water level rather than a change in wetland extent.
      </p>
    </section>
  )
}

function formatGrowth(value) {
  if (value === null || value === undefined) return 'n/a'
  return `${value > 0 ? '+' : ''}${value}%`
}

function profileSummary(rings) {
  const parts = rings.map((ring) => `${ring.label}: ${formatGrowth(ring.growth_pct)}`)
  return `Built-up growth by distance from water. ${parts.join('. ')}.`
}
