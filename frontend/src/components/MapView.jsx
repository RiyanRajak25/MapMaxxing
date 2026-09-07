import { useCallback, useEffect, useMemo, useRef } from 'react'
import { MapContainer, TileLayer, LayersControl, useMap } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import AoiDrawControl from './AoiDrawControl'

const INITIAL_CENTER = [23.2599, 77.4126] // Bhopal - the paper's change-detection case study
const INITIAL_ZOOM = 11

// Every result layer shares Leaflet's tilePane with the basemap, where paint
// order follows DOM insertion - which lets the basemap end up on top. An
// explicit zIndex per layer pins the stack instead. Higher sits above; change
// layers are last so they are never hidden by the state layers.
//
// Change layers render fully opaque: they are sparse single pixels, and any
// transparency blends them into the imagery until they disappear.
const RESULT_LAYERS = [
  { key: 'period1_rgb', label: 'Satellite photo, before', period: 'period1', zIndex: 150, opacity: 1 },
  { key: 'period2_rgb', label: 'Satellite photo, after', period: 'period2', zIndex: 160, opacity: 1 },
  { key: 'water', label: 'Open water', zIndex: 210, opacity: 0.75 },
  // `period` lets the legend swap in the actual year once a result exists, so a
  // swatch never says "period 1" when the reader is thinking "2017".
  { key: 'period1_amcbi', label: 'Built-up, before', period: 'period1', zIndex: 220, opacity: 0.7 },
  { key: 'period2_amcbi', label: 'Built-up, after', period: 'period2', zIndex: 230, opacity: 0.7 },
  // Named for what it measures. In a growing city these pixels are overwhelmingly
  // classifier disagreement between the two dates, not demolition - see
  // backend/app/services/change_filters.py.
  { key: 'loss', label: 'No longer detected', zIndex: 240, opacity: 1 },
  { key: 'gain', label: 'New built-up', zIndex: 250, opacity: 1 },
]

function FitBounds({ bounds }) {
  const map = useMap()
  useEffect(() => {
    if (bounds) map.fitBounds(bounds, { padding: [24, 24] })
  }, [map, bounds])
  return null
}

/**
 * One result layer, reporting whether it currently has tiles in flight.
 *
 * Earth Engine renders each tile the moment it is first requested, so a layer
 * keeps loading well after the analysis response arrives, and starts again on
 * every pan and zoom. Without this the map looks finished while it is still
 * filling in.
 */
function ResultTileLayer({ layerKey, url, zIndex, opacity, onLoadingChange }) {
  // A layer switched off or replaced mid-request never fires `load`, so the
  // registration has to be dropped on unmount or the indicator sticks on.
  useEffect(() => () => onLoadingChange(layerKey, false), [layerKey, onLoadingChange])

  const handlers = useMemo(
    () => ({
      loading: () => onLoadingChange(layerKey, true),
      // Leaflet fires `load` once every tile in the viewport has settled,
      // errors included, so this is reached even when Earth Engine refuses one.
      load: () => onLoadingChange(layerKey, false),
    }),
    [layerKey, onLoadingChange]
  )

  return <TileLayer url={url} zIndex={zIndex} opacity={opacity} eventHandlers={handlers} />
}

export default function MapView({
  aoi,
  onAoiChange,
  tiles,
  visibleLayers,
  fitBounds,
  onTilesLoadingChange,
}) {
  // Held in refs, not state: tile batches start and finish constantly, and
  // re-rendering the map for each one would rebind every layer's handlers.
  // Only the derived boolean leaves this component, and only when it flips.
  const pending = useRef(new Set())
  const reported = useRef(false)

  const handleLayerLoading = useCallback(
    (key, isLoading) => {
      if (isLoading) pending.current.add(key)
      else pending.current.delete(key)

      const loading = pending.current.size > 0
      if (loading !== reported.current) {
        reported.current = loading
        onTilesLoadingChange?.(loading)
      }
    },
    [onTilesLoadingChange]
  )

  return (
    <MapContainer center={INITIAL_CENTER} zoom={INITIAL_ZOOM} className="map">
      <LayersControl position="topleft">
        <LayersControl.BaseLayer checked name="Satellite">
          <TileLayer
            url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
            attribution="Esri, Maxar, Earthstar Geographics"
            maxZoom={19}
            zIndex={100}
          />
        </LayersControl.BaseLayer>
        <LayersControl.BaseLayer name="Street map">
          <TileLayer
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            attribution="&copy; OpenStreetMap contributors"
            zIndex={100}
          />
        </LayersControl.BaseLayer>
      </LayersControl>

      {tiles &&
        RESULT_LAYERS.filter(({ key }) => visibleLayers[key] && tiles[key]).map(
          ({ key, zIndex, opacity }) => (
            <ResultTileLayer
              key={key}
              layerKey={key}
              url={tiles[key]}
              zIndex={zIndex}
              opacity={opacity}
              onLoadingChange={handleLayerLoading}
            />
          )
        )}

      <AoiDrawControl aoi={aoi} onAoiChange={onAoiChange} />
      <FitBounds bounds={fitBounds} />
    </MapContainer>
  )
}

export { RESULT_LAYERS }
