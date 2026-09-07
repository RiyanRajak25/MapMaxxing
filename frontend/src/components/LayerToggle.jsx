import { RESULT_LAYERS } from './MapView'

// Must stay in step with the palettes in backend/app/services/change_detection.py
// and with the accent tokens in App.css. All three name the same five colours.
const SWATCHES = {
  period1_rgb: '#e6a100',
  period2_rgb: '#00b4d8',
  water: '#1d7fe0',
  period1_amcbi: '#ffc300',
  period2_amcbi: '#00e5ff',
  loss: '#9e9e9e',
  gain: '#ff2d2d',
}

export default function LayerToggle({
  visibleLayers,
  availableLayers,
  exportUrls,
  exporting,
  exportStatus,
  onExport,
  onToggle,
  periods,
}) {
  // The water layer only exists when the wetland analysis ran, so the list is
  // filtered by what the backend actually returned.
  const layers = RESULT_LAYERS.filter(({ key }) => availableLayers.includes(key))

  return (
    <section className="panel">
      <h2 className="panel-title">Map layers</h2>
      <ul className="layer-list">
        {layers.map(({ key, label, period }) => {
          const year = period && periods?.[period]?.year
          const displayLabel = year
            ? key.endsWith('_rgb')
              ? `Satellite photo, ${year}`
              : `Built-up, ${year}`
            : label
          return (
            <li key={key}>
              <label className="layer-item">
                <input
                  type="checkbox"
                  checked={visibleLayers[key]}
                  onChange={() => onToggle(key)}
                />
                <span className="swatch" style={{ background: SWATCHES[key] }} />
                <span>{displayLabel}</span>
              </label>
            </li>
          )
        })}
      </ul>
      <p className="hint">
        Change is measured per 10 m pixel. Zoom past street level to see individual gain and
        loss pixels. At city zoom they are smaller than one screen pixel.
      </p>
      {Object.keys(exportUrls || {}).length > 0 && (
        <div className="export-area">
          <button
            className="export-button"
            type="button"
            onClick={onExport}
            disabled={exporting}
          >
            {exporting ? `${exportStatus || 'Preparing ZIP...'}...` : 'Download vector layers (.shp)'}
          </button>
          <p className="hint export-hint">
            Includes separate built-up, gain, loss, and water shapefiles in one ZIP for QGIS. Satellite
            photos are raster layers and are not part of the shapefile.
          </p>
        </div>
      )}
    </section>
  )
}
