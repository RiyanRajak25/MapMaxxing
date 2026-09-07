import { useEffect, useRef } from 'react'
import { useMap } from 'react-leaflet'
import L from 'leaflet'
import 'leaflet-draw'
import 'leaflet-draw/dist/leaflet.draw.css'

const AOI_STYLE = { color: '#00c8c8', weight: 2, fillOpacity: 0.06 }

/**
 * Rectangle/polygon AOI drawing, wired to leaflet-draw directly.
 *
 * Controlled: whatever geometry is in `aoi` is what the map shows, so an AOI
 * loaded from a preset appears exactly like a hand-drawn one. Only one AOI
 * exists at a time.
 */
export default function AoiDrawControl({ aoi, onAoiChange }) {
  const map = useMap()
  const groupRef = useRef(null)

  useEffect(() => {
    const drawnItems = new L.FeatureGroup()
    map.addLayer(drawnItems)
    groupRef.current = drawnItems

    const drawControl = new L.Control.Draw({
      position: 'topright',
      draw: {
        // showArea on polygons triggers a known leaflet-draw crash against
        // Leaflet 1.9 (readableArea reads an undefined unit table), so it stays off.
        polygon: { showArea: false, allowIntersection: false, shapeOptions: AOI_STYLE },
        rectangle: { showArea: false, shapeOptions: AOI_STYLE },
        polyline: false,
        circle: false,
        marker: false,
        circlemarker: false,
      },
      edit: { featureGroup: drawnItems, edit: false },
    })
    map.addControl(drawControl)

    const handleCreated = (event) => {
      drawnItems.clearLayers()
      drawnItems.addLayer(event.layer)
      onAoiChange(event.layer.toGeoJSON().geometry)
    }

    const handleDeleted = () => {
      if (drawnItems.getLayers().length === 0) onAoiChange(null)
    }

    map.on(L.Draw.Event.CREATED, handleCreated)
    map.on(L.Draw.Event.DELETED, handleDeleted)

    return () => {
      map.off(L.Draw.Event.CREATED, handleCreated)
      map.off(L.Draw.Event.DELETED, handleDeleted)
      map.removeControl(drawControl)
      map.removeLayer(drawnItems)
    }
  }, [map, onAoiChange])

  // Reflect externally-set geometry (presets) onto the map. Comparing the
  // serialised geometry keeps a shape the user just drew from being torn down
  // and re-added on the render that follows.
  useEffect(() => {
    const group = groupRef.current
    if (!group) return

    const current = group.getLayers()[0]
    const currentGeometry = current ? JSON.stringify(current.toGeoJSON().geometry) : null
    if (currentGeometry === JSON.stringify(aoi ?? null)) return

    group.clearLayers()
    if (aoi) group.addLayer(L.geoJSON(aoi, { style: AOI_STYLE }))
  }, [aoi])

  return null
}
