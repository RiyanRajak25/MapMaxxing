import { PRESETS, drySeasonLabel, yearOptions } from '../presets'

export default function ControlPanel({
  aoi,
  year1,
  year2,
  cloudThreshold,
  includeWetland,
  loading,
  onYearChange,
  onCloudThresholdChange,
  onIncludeWetlandChange,
  onLoadPreset,
  onRun,
}) {
  const years = yearOptions()
  const canRun = Boolean(aoi) && !loading && year1 < year2

  return (
    <>
      <section className="panel">
        <h2 className="panel-title">Area</h2>
        <p className={aoi ? 'aoi-status aoi-status--set' : 'aoi-status'}>
          {aoi
            ? 'Bhoj Wetland study area loaded. Draw on the map to replace it.'
            : 'Draw an area with the square or polygon tool on the map.'}
        </p>
        <div className="preset-group">
          {PRESETS.map((preset) => (
            <button
              key={preset.id}
              className="preset-button"
              onClick={() => onLoadPreset(preset)}
              disabled={loading}
              title={preset.note}
            >
              Reset to {preset.name}
            </button>
          ))}
        </div>
      </section>

      <section className="panel">
        <h2 className="panel-title">Years</h2>
        {/* Colour-coded to the map layers each year produces, so the reader
            never has to hold "period 1" in their head. */}
        <div className="year-row">
          <YearSelect
            label="Before"
            tone="before"
            value={year1}
            years={years.filter((year) => year < year2)}
            onChange={(value) => onYearChange('year1', value)}
          />
          <span className="year-arrow" aria-hidden="true">
            to
          </span>
          <YearSelect
            label="After"
            tone="after"
            value={year2}
            years={years.filter((year) => year > year1)}
            onChange={(value) => onYearChange('year2', value)}
          />
        </div>
        <p className="season-note">
          {drySeasonLabel(year1)} vs {drySeasonLabel(year2)}
        </p>
        <p className="hint">
          A year means its post-monsoon dry season. The window is fixed rather than free-form
          because vegetation and soil moisture shift through the year, and comparing different
          seasons reads that shift as urban growth.
        </p>
      </section>

      <section className="panel">
        <h2 className="panel-title">Options</h2>
        <label className="field">
          <span className="field-label">
            Max cloud cover per scene <span className="slider-value">{cloudThreshold}%</span>
          </span>
          <input
            type="range"
            min="0"
            max="100"
            step="5"
            value={cloudThreshold}
            onChange={(event) => onCloudThresholdChange(Number(event.target.value))}
          />
        </label>

        <label className="checkbox">
          <input
            type="checkbox"
            checked={includeWetland}
            onChange={(event) => onIncludeWetlandChange(event.target.checked)}
          />
          <span>Measure growth by distance from water</span>
        </label>
        <p className="hint">
          Bins built-up change into distance zones from the shoreline, which is what
          separates encroachment from ordinary outward expansion. Adds about a minute.
        </p>

        <button className="run-button" onClick={onRun} disabled={!canRun}>
          {loading ? 'Analysing imagery' : 'Run analysis'}
        </button>
      </section>
    </>
  )
}

function YearSelect({ label, tone, value, years, onChange }) {
  return (
    <label className={`field field--${tone}`}>
      <span className="field-label">{label}</span>
      <select value={value} onChange={(event) => onChange(Number(event.target.value))}>
        {years.map((year) => (
          <option key={year} value={year}>
            {year}
          </option>
        ))}
      </select>
    </label>
  )
}
