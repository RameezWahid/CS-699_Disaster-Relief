import { layers, namedFlavor } from '@protomaps/basemaps'
import type { Feature, FeatureCollection, MultiPolygon, Point, Polygon } from 'geojson'
import {
  addProtocol, LngLatBounds, Map as MapLibreMap, NavigationControl, setWorkerUrl,
  type GeoJSONSource, type MapLayerMouseEvent, type StyleSpecification,
} from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
// MapLibre 6 looks for its worker next to its own module, which does not exist once Vite has
// bundled it. Let Vite build the worker (with its shared-chunk import) and hand over the URL.
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import { Protocol } from 'pmtiles'
import { useEffect, useRef, useState } from 'react'
import type { Incident, Offer } from './api.ts'
import { URGENCY_COLORS } from './urgency.ts'

// BMC extent (same box as backend config.MUMBAI_BBOX).
const MUMBAI_BOUNDS: [[number, number], [number, number]] = [[72.77, 18.89], [72.99, 19.28]]
const OFFER_COLOR = '#15803d'
const VERIFY_RING = '#1d4ed8'
const EMPTY: FeatureCollection = { type: 'FeatureCollection', features: [] }

setWorkerUrl(maplibreWorkerUrl)
addProtocol('pmtiles', new Protocol().tile)

function basemapStyle(): StyleSpecification {
  const origin = window.location.origin
  return {
    version: 8,
    glyphs: `${origin}/basemap/fonts/{fontstack}/{range}.pbf`,
    sprite: `${origin}/basemap/sprites/light`,
    sources: {
      protomaps: {
        type: 'vector',
        url: `pmtiles://${origin}/basemap/mumbai.pmtiles`,
        attribution:
          '<a href="https://protomaps.com">Protomaps</a> © <a href="https://openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      },
    },
    layers: layers('protomaps', namedFlavor('light'), { lang: 'en' }),
  }
}

function incidentFeatures(incidents: Incident[]): FeatureCollection<Point> {
  const features: Feature<Point>[] = incidents
    .filter((i) => i.lon !== null && i.lat !== null)
    .map((i) => ({
      type: 'Feature',
      id: i.id,
      geometry: { type: 'Point', coordinates: [i.lon!, i.lat!] },
      properties: { id: i.id, urgency_level: i.urgency_level, verify: i.credibility < 0.5 && i.status === 'open' },
    }))
  return { type: 'FeatureCollection', features }
}

function offerFeatures(offers: Offer[]): FeatureCollection<Point> {
  return {
    type: 'FeatureCollection',
    features: offers.map((o) => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [o.lon, o.lat] },
      properties: { label: `${o.kind.replace('_', ' ')} ${o.qty_available}/${o.qty_total}` },
    })),
  }
}

interface Props {
  incidents: Incident[]
  offers: Offer[]
  myWards: string[]
  selectedId: number | null
  onSelect: (id: number) => void
}

export default function MapView({ incidents, offers, myWards, selectedId, onSelect }: Props) {
  const container = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const [ready, setReady] = useState(false)
  const onSelectRef = useRef(onSelect)
  useEffect(() => { onSelectRef.current = onSelect }, [onSelect])

  useEffect(() => {
    const map = new MapLibreMap({
      container: container.current!,
      style: basemapStyle(),
      bounds: MUMBAI_BOUNDS,
      maxBounds: [[72.6, 18.8], [73.15, 19.4]],
    })
    map.addControl(new NavigationControl(), 'top-right')

    map.on('load', async () => {
      const wards: FeatureCollection = await (await fetch('/api/wards')).json()
      map.addSource('wards', { type: 'geojson', data: wards })
      map.addLayer({
        id: 'ward-mine', type: 'fill', source: 'wards',
        filter: ['in', ['get', 'code'], ['literal', myWards]],
        paint: { 'fill-color': '#1d4ed8', 'fill-opacity': 0.05 },
      })
      map.addLayer({
        id: 'ward-lines', type: 'line', source: 'wards',
        paint: { 'line-color': '#475569', 'line-width': 1, 'line-dasharray': [2, 2] },
      })
      map.addLayer({
        id: 'ward-labels', type: 'symbol', source: 'wards',
        layout: { 'text-field': ['get', 'code'], 'text-font': ['Noto Sans Medium'], 'text-size': 12 },
        paint: { 'text-color': '#334155', 'text-halo-color': '#fff', 'text-halo-width': 1.5 },
      })
      const mine = wards.features.filter((f) => myWards.includes(String(f.properties?.code)))
      if (mine.length) {
        const bounds = new LngLatBounds()
        for (const ward of mine) {
          // Simplification leaves most wards as Polygon and a few as MultiPolygon.
          const geometry = ward.geometry as Polygon | MultiPolygon
          const polygons = geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.coordinates
          for (const polygon of polygons) for (const [x, y] of polygon[0]) bounds.extend([x, y])
        }
        map.fitBounds(bounds, { padding: 30, duration: 0 })
      }

      map.addSource('heat', { type: 'geojson', data: EMPTY })
      map.addLayer({
        id: 'incident-heat', type: 'heatmap', source: 'heat', maxzoom: 15,
        paint: {
          'heatmap-weight': ['/', ['get', 'urgency_level'], 5],
          'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 10, 20, 14, 40],
          'heatmap-opacity': ['interpolate', ['linear'], ['zoom'], 12, 0.7, 15, 0],
        },
      })
      map.addSource('incidents', { type: 'geojson', data: EMPTY, cluster: true, clusterMaxZoom: 13, clusterRadius: 40 })
      map.addLayer({
        id: 'clusters', type: 'circle', source: 'incidents', filter: ['has', 'point_count'],
        paint: { 'circle-color': '#334155', 'circle-radius': ['step', ['get', 'point_count'], 14, 10, 18, 50, 24], 'circle-opacity': 0.85 },
      })
      map.addLayer({
        id: 'cluster-count', type: 'symbol', source: 'incidents', filter: ['has', 'point_count'],
        layout: { 'text-field': ['get', 'point_count_abbreviated'], 'text-font': ['Noto Sans Medium'], 'text-size': 12 },
        paint: { 'text-color': '#fff' },
      })
      map.addLayer({
        id: 'incident-points', type: 'circle', source: 'incidents', filter: ['!', ['has', 'point_count']],
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['get', 'urgency_level'], 1, 5, 5, 10],
          'circle-color': ['step', ['get', 'urgency_level'], URGENCY_COLORS[0],
            2, URGENCY_COLORS[1], 3, URGENCY_COLORS[2], 4, URGENCY_COLORS[3], 5, URGENCY_COLORS[4]],
          'circle-stroke-color': ['case', ['get', 'verify'], VERIFY_RING, '#ffffff'],
          'circle-stroke-width': ['case', ['get', 'verify'], 3, 1.5],
        },
      })
      map.addSource('selected', { type: 'geojson', data: EMPTY })
      map.addLayer({
        id: 'selected-ring', type: 'circle', source: 'selected',
        paint: { 'circle-radius': 16, 'circle-color': 'transparent', 'circle-stroke-color': '#0f172a', 'circle-stroke-width': 2.5 },
      })
      map.addSource('offers', { type: 'geojson', data: EMPTY })
      map.addLayer({
        id: 'offer-points', type: 'circle', source: 'offers',
        paint: { 'circle-radius': 6, 'circle-color': OFFER_COLOR, 'circle-stroke-color': '#fff', 'circle-stroke-width': 1.5 },
      })
      map.addLayer({
        id: 'offer-labels', type: 'symbol', source: 'offers', minzoom: 12,
        layout: { 'text-field': ['get', 'label'], 'text-font': ['Noto Sans Regular'], 'text-size': 11, 'text-offset': [0, 1.2] },
        paint: { 'text-color': OFFER_COLOR, 'text-halo-color': '#fff', 'text-halo-width': 1.5 },
      })

      map.on('click', 'incident-points', (e: MapLayerMouseEvent) => {
        const id = e.features?.[0]?.properties?.id
        if (id !== undefined) onSelectRef.current(Number(id))
      })
      map.on('click', 'clusters', (e: MapLayerMouseEvent) => {
        map.easeTo({ center: e.lngLat, zoom: map.getZoom() + 2 })
      })
      for (const layer of ['incident-points', 'clusters']) {
        map.on('mouseenter', layer, () => { map.getCanvas().style.cursor = 'pointer' })
        map.on('mouseleave', layer, () => { map.getCanvas().style.cursor = '' })
      }
      mapRef.current = map
      setReady(true)
    })

    return () => {
      mapRef.current = null
      setReady(false)
      map.remove()
    }
  }, [myWards])

  // Push data into the map whenever it changes, once the map has finished loading.
  useEffect(() => {
    const map = mapRef.current
    if (!ready || !map) return
    const points = incidentFeatures(incidents)
    ;(map.getSource('incidents') as GeoJSONSource).setData(points)
    ;(map.getSource('heat') as GeoJSONSource).setData(points)
    ;(map.getSource('offers') as GeoJSONSource).setData(offerFeatures(offers))
    const selected = points.features.filter((f) => f.id === selectedId)
    ;(map.getSource('selected') as GeoJSONSource).setData({ type: 'FeatureCollection', features: selected })
  }, [ready, incidents, offers, selectedId])

  return (
    <div className="map-wrap">
      <div ref={container} className="map" />
      <div className="legend">
        <strong>Urgency</strong>
        {URGENCY_COLORS.map((color, i) => (
          <span key={color}><i style={{ background: color }} />{i + 1}</span>
        ))}
        <span><i className="review-swatch" />needs verification</span>
        <span><i style={{ background: OFFER_COLOR }} />offer</span>
      </div>
    </div>
  )
}
