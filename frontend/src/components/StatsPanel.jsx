/**
 * Headline result, the measured areas, and the limits of both.
 *
 * Net change is the growth figure. Gain is deliberately conservative and does
 * not reconcile with it, and loss is closer to an error bar than a measurement,
 * so those two facts are stated in the interface rather than left for the
 * reader to trip over.
 */
export default function StatsPanel({ stats, aoiKm2, periods }) {
  const growth = stats.net_change_pct
  const before = periods?.period1
  const after = periods?.period2

  return (
    <section className="panel results-enter">
      <h2 className="panel-title">Result</h2>

      <div className="headline">
        <span className="headline-value">
          {growth === null ? 'n/a' : `${growth > 0 ? '+' : ''}${growth}%`}
        </span>
        <span className="headline-label">built-up area</span>
      </div>
      <p className="headline-sub">
        {before && after ? `${before.year} to ${after.year} dry seasons` : null}
        {before && after ? ' · ' : null}
        {aoiKm2} km² analysed
      </p>

      <dl className="stats">
        <div className="stat">
          <dt>
            <span className="stat-chip chip--before" />
            Built-up, {before ? before.year : 'before'}
          </dt>
          <dd>{stats.built_up_km2_period1} km²</dd>
        </div>
        <div className="stat">
          <dt>
            <span className="stat-chip chip--after" />
            Built-up, {after ? after.year : 'after'}
          </dt>
          <dd>{stats.built_up_km2_period2} km²</dd>
        </div>
        <div className="stat">
          <dt>Net change</dt>
          <dd>
            {stats.net_change_km2 > 0 ? '+' : ''}
            {stats.net_change_km2} km²
          </dd>
        </div>
        <div className="stat">
          <dt>
            <span className="stat-chip chip--gain" />
            New built-up, confident
          </dt>
          <dd className="stat-value--gain">{stats.gain_km2} km²</dd>
        </div>
        <div className="stat">
          <dt>
            <span className="stat-chip chip--instability" />
            No longer detected
          </dt>
          <dd className="stat-value--loss">{stats.loss_km2} km²</dd>
        </div>
      </dl>

      <div className="confidence">
        <div className="confidence-head">
          <span className="confidence-title">How much to trust this</span>
          <span className="confidence-figure">{stats.change_filtered_pct}% rejected</span>
        </div>
        <p>
          Read net change as the growth figure. A pixel only counts as gain if it was
          decisively non-built-up before and decisively built-up after, across a patch of at
          least {stats.min_change_unit_m2?.toLocaleString()} m², so gain under-counts on
          purpose and will not add up to net change.
        </p>
        {stats.loss_is_error_estimate && (
          <p>
            Buildings do not disappear over a few years, so the {stats.loss_km2} km² marked
            &ldquo;no longer detected&rdquo; is close to a direct measure of how much the
            classifier disagrees with itself between the two dates. Treat it as the error on{' '}
            {stats.gain_km2} km² of gain, not as demolition.
          </p>
        )}
        {stats.radiometrically_normalised && (
          <p>
            The earlier imagery was corrected onto the later date&rsquo;s radiometric scale.
            Without that step this figure roughly doubles, and it still runs high: read it as
            an upper bound.
          </p>
        )}
      </div>
    </section>
  )
}
