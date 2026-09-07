import { useCallback, useState } from 'react'
import MapView from './components/MapView'
import ControlPanel from './components/ControlPanel'
import StatsPanel from './components/StatsPanel'
import WetlandPanel from './components/WetlandPanel'
import LayerToggle from './components/LayerToggle'
import AnalysisOverlay from './components/AnalysisOverlay'
import EarthLoader from './components/EarthLoader'
import { downloadShapefile, runChangeDetection } from './api/client'
import { DEFAULT_PRESET } from './presets'
import './App.css'

const DEFAULT_VISIBILITY = {
  period1_rgb: false,
  period2_rgb: false,
  period1_amcbi: false,
  period2_amcbi: false,
  gain: true,
  loss: true,
  water: true,
}

export default function App() {
  // The prototype is scoped to one site, so the AOI starts loaded rather than
  // making every visit begin by drawing the same rectangle.
  const [aoi, setAoi] = useState(DEFAULT_PRESET.aoi)
  const [year1, setYear1] = useState(DEFAULT_PRESET.year1)
  const [year2, setYear2] = useState(DEFAULT_PRESET.year2)
  const [cloudThreshold, setCloudThreshold] = useState(20)
  const [includeWetland, setIncludeWetland] = useState(true)
  const [result, setResult] = useState(null)
  const [visibleLayers, setVisibleLayers] = useState(DEFAULT_VISIBILITY)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [fitBounds, setFitBounds] = useState(DEFAULT_PRESET.bounds)
  // Separate from `loading`: the request finishing is not the map being drawn.
  // Earth Engine renders tiles on demand, so the result layers keep arriving
  // afterwards and start over on every pan and zoom.
  const [tilesLoading, setTilesLoading] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [exportStatus, setExportStatus] = useState('')

  // Stable identity keeps the draw control's effect from tearing down on every render.
  const handleAoiChange = useCallback((geometry) => {
    setAoi(geometry)
    setError(null)
  }, [])

  const handleYearChange = (which, value) => {
    if (which === 'year1') setYear1(value)
    else setYear2(value)
  }

  const handleLoadPreset = (preset) => {
    setAoi(preset.aoi)
    setYear1(preset.year1)
    setYear2(preset.year2)
    // New array identity each time so re-clicking always re-zooms.
    setFitBounds([...preset.bounds])
    setResult(null)
    setError(null)
  }

  const handleToggleLayer = (key) =>
    setVisibleLayers((prev) => ({ ...prev, [key]: !prev[key] }))

  const handleRun = async () => {
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const data = await runChangeDetection({
        aoi,
        year1,
        year2,
        cloudThreshold,
        includeWetland,
      })
      setResult(data)
      setVisibleLayers(DEFAULT_VISIBILITY)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const handleExport = async () => {
    setExporting(true)
    setExportStatus('Starting export...')
    setError(null)
    try {
      await downloadShapefile(result.export_urls, setExportStatus)
    } catch (err) {
      setError(err.message)
    } finally {
      setExporting(false)
      setExportStatus('')
    }
  }

  return (
    <div className="layout">
      <aside className="sidebar">
        <header className="brand">
          <h1>Bhoj Wetland, built-up change</h1>
          <p>
            Sentinel-2 surface reflectance, AMCBI constraint index, measured against distance
            from the shoreline.
          </p>
        </header>

        <ControlPanel
          aoi={aoi}
          year1={year1}
          year2={year2}
          cloudThreshold={cloudThreshold}
          loading={loading}
          onYearChange={handleYearChange}
          includeWetland={includeWetland}
          onCloudThresholdChange={setCloudThreshold}
          onIncludeWetlandChange={setIncludeWetland}
          onLoadPreset={handleLoadPreset}
          onRun={handleRun}
        />

        {error && (
          <p className="alert" role="alert">
            {error}
          </p>
        )}

        {result && (
          <>
            <StatsPanel
              stats={result.stats}
              aoiKm2={result.aoi_km2}
              periods={result.periods}
            />
            {result.wetland && <WetlandPanel wetland={result.wetland} />}
            <LayerToggle
              visibleLayers={visibleLayers}
              availableLayers={Object.keys(result.tiles)}
              exportUrls={result.export_urls}
              exporting={exporting}
              exportStatus={exportStatus}
              onExport={handleExport}
              onToggle={handleToggleLayer}
              periods={result.periods}
            />
          </>
        )}
      </aside>

      <main className="map-area">
        <MapView
          aoi={aoi}
          onAoiChange={handleAoiChange}
          tiles={result?.tiles}
          visibleLayers={visibleLayers}
          fitBounds={fitBounds}
          onTilesLoadingChange={setTilesLoading}
        />
        {!aoi && !loading && (
          <div className="map-empty">
            <strong>Draw an area</strong>
            <span>using the square or polygon tool.</span>
          </div>
        )}
        {result && !loading && <div className="map-scale-note">Zoom 14+ to resolve change pixels</div>}

        {/* Deliberately not an overlay - tiles arrive while the map stays
            usable, and blocking a pan to announce that a pan is loading would
            be worse than saying nothing. */}
        {tilesLoading && !loading && (
          <div className="tile-status" role="status">
            <EarthLoader size={22} />
            <span>Rendering pixels</span>
          </div>
        )}

        {loading && <AnalysisOverlay includeWetland={includeWetland} />}
      </main>
    </div>
  )
}
