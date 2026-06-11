/*
 * dispatch_map.js
 * -----------------
 * Provides a miniature Leaflet map inside the dispatch modal.
 * Highlights optimal driving routes from the closest bases for dispatched units.
 * Functions:
 *   - initDispatchMap(): creates the map instance if not already created.
 *   - updateDispatchMap(report): shows a marker for the incident (lat/lng) and draws route lines for closest bases.
 *   - updateDispatchMapFromInput(): reads unit input field and updates routes dynamically.
 *   - clearDispatchMap(): removes markers/routes and resets view.
 * Exposes functions on the global window object for usage by app.js.
 */

let dispatchMap = null;
let dispatchMarker = null;
let dispatchRoutes = [];
let currentIncidentCoords = null;

// -- Sevilla operational bounds ------------------------------------------------
const DM_SEVILLA_BOUNDS = L.latLngBounds(
  [37.25, -6.12],   // SW corner
  [37.52, -5.82]    // NE corner
);

/** Returns true if the given lat/lon is inside Sevilla's operational area. */
function dmInSevillaArea(lat, lon) {
  return lat >= 37.25 && lat <= 37.52 && lon >= -6.12 && lon <= -5.82;
}

function initDispatchMap() {
  if (dispatchMap) return;
  const container = document.getElementById('dispatch-mini-map');
  if (!container) return;
  // Create a Leaflet map without default controls to keep it lightweight.
  // maxBounds restricts panning to the city of Sevilla.
  dispatchMap = L.map('dispatch-mini-map', {
    zoomControl: false,
    attributionControl: false,
    maxBounds: DM_SEVILLA_BOUNDS,
    maxBoundsViscosity: 0.85,
    minZoom: 11,
  }).setView([37.3886, -5.9823], 13);
  // Use the same dark basemap as the main map.
  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png').addTo(dispatchMap);
  // The modal transitions from display:none, so the container may have had
  // zero dimensions when Leaflet first measured it.  Force a recalculation
  // on the next two animation frames so tiles and SVG layers become visible.
  requestAnimationFrame(() => {
    dispatchMap.invalidateSize();
    requestAnimationFrame(() => dispatchMap.invalidateSize());
  });
}

// -- Utility: Calculate straight-line distance in km (Haversine formula) --------
function getDistance(lat1, lon1, lat2, lon2) {
  const R = 6371; // km
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLon = (lon2 - lon1) * Math.PI / 180;
  const a = Math.sin(dLat/2) * Math.sin(dLat/2) +
            Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
            Math.sin(dLon/2) * Math.sin(dLon/2);
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
  return R * c;
}

// -- Utility: Decode Google Encoded Polyline algorithm -------------------------
function decodePolyline(str, precision) {
  var index = 0, lat = 0, lng = 0, coordinates = [], shift = 0, result = 0, byte = null, lat_change, lng_change, factor = Math.pow(10, precision || 5);
  while (index < str.length) {
    shift = 0; result = 0;
    do {
      byte = str.charCodeAt(index++) - 63;
      result |= (byte & 0x1f) << shift;
      shift += 5;
    } while (byte >= 0x20);
    lat_change = ((result & 1) ? ~(result >> 1) : (result >> 1));
    lat += lat_change;
    shift = 0; result = 0;
    do {
      byte = str.charCodeAt(index++) - 63;
      result |= (byte & 0x1f) << shift;
      shift += 5;
    } while (byte >= 0x20);
    lng_change = ((result & 1) ? ~(result >> 1) : (result >> 1));
    lng += lng_change;
    coordinates.push([lat / factor, lng / factor]);
  }
  return coordinates;
}

// -- Parser: Extract units from human input strings ----------------------------
function parseUnitsInput(inputStr) {
  const units = [];
  if (!inputStr && inputStr !== 0) return units;

  /** Classify a single token string into a unit type. */
  function _detectType(tok) {
    if (tok.includes('sva') || tok.includes('hospital')) return 'ambulance_sva';
    if (tok.includes('svb'))                             return 'ambulance_svb';
    if (tok.includes('pol') || tok.includes('police') || tok.includes('polic')) return 'police';
    if (tok.includes('fire') || tok.includes('bom') || tok.includes('incendio')) return 'fire';
    if (tok.includes('res')  || tok.includes('resc')) return 'rescue';
    if (tok.includes('amb')  || tok.includes('ambulancia')) return 'ambulance_svb';
    return null;
  }

  function _add(type, count) {
    const existing = units.find(u => u.type === type);
    if (existing) existing.count += count;
    else units.push({ type, count });
  }

  // ── Array input: structured unit objects {type, id, ...} or plain ID strings ──
  // For objects (pipeline output), use the .type field directly.
  // For strings, classify by keyword.
  if (Array.isArray(inputStr)) {
    inputStr.forEach(u => {
      if (u && typeof u === 'object' && u.type) {
        _add(u.type, 1);
      } else {
        const type = _detectType(String(u).toLowerCase().trim());
        if (type) _add(type, 1);
      }
    });
    return units;
  }

  // ── Numeric input: total unit count with no type information ─────────────────
  if (typeof inputStr !== 'string') {
    return units; // can't infer type from a bare number
  }

  const str = inputStr;

  // ── String input: human-readable e.g. "2 AMB · 1 POL", "AMB-SVA-01,POL-02"
  // or the dm-units format "SVA (3 min) - SVB (4 min)".
  // Split by common separators: ·, commas, semicolons, plus signs, newlines,
  // and hyphens surrounded by spaces (bare hyphens stay intact so unit IDs
  // like "AMB-SVA-01" are not broken apart).
  const tokens = str.toLowerCase().split(/[·,;\+\n]+|\s-\s/);

  tokens.forEach(tok => {
    tok = tok.trim();
    if (!tok) return;

    const type = _detectType(tok);
    if (type) {
      // Only treat leading/standalone digits as explicit counts, never
      // the trailing number in a unit-ID like "AMB-SVA-03".
      // Pattern: optional leading count followed by non-digit → "2 amb", "2amb"
      const leadingCount = tok.match(/^(\d+)\s*[a-z]/);
      const count = leadingCount ? parseInt(leadingCount[1], 10) : 1;
      _add(type, count);
    }
  });

  // Fallback word search if no delimiters were matched
  if (units.length === 0) {
    const words = str.toLowerCase().split(/\s+/);
    words.forEach(word => {
      let type = null;
      if (word.startsWith('pol')) type = 'police';
      else if (word.includes('sva')) type = 'ambulance_sva';
      else if (word.includes('svb')) type = 'ambulance_svb';
      else if (word.startsWith('bom') || word.startsWith('fir')) type = 'fire';
      else if (word.startsWith('res')) type = 'rescue';
      else if (word.startsWith('amb')) type = 'ambulance_svb';

      if (type) {
        const existing = units.find(u => u.type === type);
        if (existing) {
          existing.count += 1;
        } else {
          units.push({ type, count: 1 });
        }
      }
    });
  }

  return units;
}

// -- Base Finder: Get N closest bases of a specific type -----------------------
function getClosestBases(unitType, count, incidentLat, incidentLng) {
  if (!Array.isArray(BASES) || BASES.length === 0) {
    console.warn('[dispatch_map] getClosestBases called before BASES data was loaded — returning empty list. Routes will not be drawn until bases are available.');
    return [];
  }

  // Filter bases that can support the requested unit type
  const matchedBases = BASES.filter(base => {
    // 1. Explicit matching by type
    if (Array.isArray(base.types) && base.types.includes(unitType)) {
      return true;
    }

    // 2. String-matching fallback
    const name = (base.name || "").toLowerCase();
    if (unitType === 'ambulance_sva') {
      return name.includes('hospital') || name.includes('central');
    }
    if (unitType === 'ambulance_svb') {
      return name.includes('base') && !name.includes('central');
    }
    if (unitType === 'police') {
      return name.includes('comisaría') || name.includes('comisaria') || name.includes('patrulla');
    }
    if (unitType === 'fire') {
      return name.includes('bomberos');
    }
    if (unitType === 'rescue') {
      return name.includes('bomberos') && name.includes('sur'); // Rescue is in Parque Bomberos Sur
    }
    return false;
  });

  // Calculate geodesic distances
  const basesWithDist = matchedBases.map(base => {
    const dist = getDistance(base.lat, base.lon, incidentLat, incidentLng);
    return { ...base, distance: dist };
  });

  // Sort ascending by distance
  basesWithDist.sort((a, b) => a.distance - b.distance);

  // Return the closest N bases
  return basesWithDist.slice(0, count);
}

// -- Routing: Fetch and draw routes from closest bases to incident -------------
function drawRoutesForUnits(parsedUnits, lat, lng, rawUnitsArray) {
  clearRoutesOnly();
  if (!dispatchMap) return;

  // Build a type→eta_minutes lookup from the raw dispatch units (may be absent).
  const etaByType = {};
  if (Array.isArray(rawUnitsArray)) {
    rawUnitsArray.forEach(u => {
      if (u && u.type && u.eta_minutes != null && !(u.type in etaByType)) {
        etaByType[u.type] = u.eta_minutes;
      }
    });
  }

  const bounds = L.latLngBounds([lat, lng]);
  let routeCount = 0;
  let minRouteDuration = Infinity; // track fastest actual road ETA across all fetched routes

  parsedUnits.forEach(unit => {
    const closestBases = getClosestBases(unit.type, unit.count, lat, lng);

    closestBases.forEach((base, index) => {
      const routeColors = ['#ff6600', '#8a2be2', '#ffd700', '#00ff00', '#00ced1'];
      const color = routeColors[routeCount % routeColors.length];
      routeCount++;

      // Create fallback dashed straight line immediately
      const fallbackPolyline = L.polyline([[base.lat, base.lon], [lat, lng]], {
        color: color,
        weight: 6,
        opacity: 0.8,
        dashArray: '5, 10'
      }).addTo(dispatchMap);

      fallbackPolyline.bindTooltip(`${unit.type.toUpperCase()} (Base: ${base.name})`, { permanent: false, direction: 'auto' });
      dispatchRoutes.push(fallbackPolyline);
      bounds.extend([base.lat, base.lon]);

      // Create emphasis circle marker at base location
      const baseMarker = L.circleMarker([base.lat, base.lon], {
        radius: 7,
        color: color,
        fillColor: color,
        fillOpacity: 0.9,
      }).addTo(dispatchMap);
      baseMarker.bindTooltip(`${unit.type.toUpperCase()} Base: ${base.name}`, { permanent: false, direction: 'top' });
      dispatchRoutes.push(baseMarker);

      // Async fetch real driving route from backend
      const apiBase = (typeof API_BASE !== 'undefined') ? API_BASE : ((window.location.protocol === 'https:' ? 'https' : 'http') + '://' + window.location.host + '/api');
      const url = `${apiBase}/route?origin_lat=${base.lat}&origin_lon=${base.lon}&dest_lat=${lat}&dest_lon=${lng}&unit_type=${unit.type}`;
      fetch(url)
        .then(res => {
          if (!res.ok) throw new Error("HTTP " + res.status);
          return res.json();
        })
        .then(routeData => {
          if (routeData.error) {
            console.warn("[dispatch_map] Backend routing failed, using fallback:", routeData.error);
            fallbackPolyline.setStyle({ opacity: 1.0, dashArray: null }); // make solid on fallback success
            return;
          }

          // Prefer pre-decoded coords returned by the backend (correct precision).
          // Fall back to decoding the raw ORS polyline string (precision 5).
          let decodedPoints = null;
          if (routeData.polyline_coords && routeData.polyline_coords.length > 1) {
            decodedPoints = routeData.polyline_coords;
            console.log(`[dispatch_map] Using polyline_coords (${decodedPoints.length} pts) for ${unit.type}`);
          } else if (routeData.polyline) {
            // ORS encodes with precision 5; use that to avoid 10× coordinate error.
            decodedPoints = decodePolyline(routeData.polyline, 5);
            console.log(`[dispatch_map] Decoded raw polyline (precision 5, ${decodedPoints.length} pts) for ${unit.type}`);
          }

          // Update dm-rt with the fastest actual road duration seen so far.
          const routeDuration = routeData.duration_minutes;
          if (routeDuration != null && routeDuration < minRouteDuration) {
            minRouteDuration = routeDuration;
            const dmRt = document.getElementById("dm-rt");
            if (dmRt) dmRt.value = Math.round(minRouteDuration);
          }

          if (decodedPoints && decodedPoints.length > 1) {
            // Remove dashed fallback and draw the real road route.
            dispatchMap.removeLayer(fallbackPolyline);
            dispatchRoutes = dispatchRoutes.filter(layer => layer !== fallbackPolyline);

            const roadPolyline = L.polyline(decodedPoints, {
              color: color,
              weight: 7,
              opacity: 1.0
            }).addTo(dispatchMap);

            const dispatchEta = etaByType[unit.type];
            roadPolyline.bindTooltip(
              dispatchEta != null
                ? `${unit.type.toUpperCase()} (${dispatchEta} min)`
                : `${unit.type.toUpperCase()} (${routeData.distance_km} km, ${routeData.duration_minutes} min)`,
              { permanent: false, direction: 'auto' }
            );
            roadPolyline.bringToFront();
            dispatchRoutes.push(roadPolyline);
          } else {
            // No usable geometry — make the straight-line fallback solid.
            console.warn('[dispatch_map] No route geometry returned; keeping straight-line fallback.');
            fallbackPolyline.setStyle({ opacity: 1.0, dashArray: null });
          }
        })
        .catch(err => {
          console.error("[dispatch_map] Route fetch error:", err);
          fallbackPolyline.setStyle({ opacity: 1.0, dashArray: null });
        });
    });
  });

  // Fit map viewport to encompass incident and all base stations
  if (routeCount > 0) {
    setTimeout(() => {
      dispatchMap.fitBounds(bounds, { padding: [40, 40] });
    }, 100);
  } else {
    dispatchMap.setView([lat, lng], 14);
  }
}

function updateDispatchMap(report) {
  if (!dispatchMap) initDispatchMap();
  if (!dispatchMap) return;

  // The modal may have been hidden since the map was last used; re-measure its container.
  dispatchMap.invalidateSize();

  // Clear previous incident marker and routes
  if (dispatchMarker) { dispatchMap.removeLayer(dispatchMarker); dispatchMarker = null; }
  clearRoutesOnly();

  // Robustly extract coordinates from any possible report format (raw or normalised)
  const lat = report.lat ?? report.latitude ?? report.location?.latitude ?? report.geo?.incident_coords?.lat ?? report.geo?.incident_lat;
  const lng = report.lon ?? report.longitude ?? report.location?.longitude ?? report.geo?.incident_coords?.lng ?? report.geo?.incident_lon;

  if (lat != null && lng != null) {
    // Reject incidents whose coordinates fall outside Sevilla's operational area
    if (!dmInSevillaArea(lat, lng)) {
      console.warn(
        `[dispatch_map] Incident coordinates (${lat}, ${lng}) are outside Sevilla's ` +
        'operational area — dispatch map will not render this location.'
      );
      return;
    }

    currentIncidentCoords = { lat, lng };

    // Pulsing emergency red divIcon for Leaflet (immunises against missing standard marker-icon.png asset)
    const markerColor = '#b83030';
    const markerSize = 16;
    const html = `
      <div style="width:${markerSize}px;height:${markerSize}px;border-radius:50%;
        background:${markerColor};border:2px solid #fff;position:relative;
        animation:mapPulse 1.8s ease-out infinite;
        box-shadow: 0 0 8px ${markerColor};">
        <div style="position:absolute;inset:4px;border-radius:50%;background:#fff"></div>
      </div>`;
      
    if (!document.getElementById('map-pulse-style')) {
      const style = document.createElement('style');
      style.id = 'map-pulse-style';
      style.innerHTML = `
        @keyframes mapPulse {
          0% { transform: scale(0.9); box-shadow: 0 0 0 0 rgba(184, 48, 48, 0.7); }
          70% { transform: scale(1.1); box-shadow: 0 0 0 10px rgba(184, 48, 48, 0); }
          100% { transform: scale(0.9); box-shadow: 0 0 0 0 rgba(184, 48, 48, 0); }
        }
      `;
      document.head.appendChild(style);
    }

    const icon = L.divIcon({ 
      html, 
      className: "", 
      iconSize: [markerSize, markerSize], 
      iconAnchor: [markerSize / 2, markerSize / 2] 
    });

    // Place incident marker
    dispatchMarker = L.marker([lat, lng], { icon }).addTo(dispatchMap);

    // Determine current units to highlight.
    // When the report carries a structured dispatch.units array (pipeline output),
    // use it directly — parseUnitsInput now reads .type from each object.
    // Only fall back to the dm-units text field when the report has no structured
    // units (e.g. a legacy format), so operator edits via updateDispatchMapFromInput
    // still work through the existing code path.
    const rawUnits =
      (report.dispatch && Array.isArray(report.dispatch.units)) ? report.dispatch.units :
      Array.isArray(report.units) ? report.units : null;

    let parsedUnits;
    if (rawUnits && rawUnits.length > 0) {
      parsedUnits = parseUnitsInput(rawUnits);
    } else {
      const dmUnitsInput = document.getElementById("dm-units");
      const unitsStr = (dmUnitsInput && dmUnitsInput.value) ? dmUnitsInput.value : "";
      parsedUnits = parseUnitsInput(unitsStr);
    }

    // Pass raw unit objects (with eta_minutes) if available so the tooltip can
    // show the authoritative dispatch ETA rather than the recalculated route time.
    drawRoutesForUnits(parsedUnits, lat, lng, rawUnits);
  }
}

function updateDispatchMapFromInput() {
  if (!dispatchMap || !currentIncidentCoords) return;
  const unitsInput = document.getElementById("dm-units");
  if (!unitsInput) return;

  const parsedUnits = parseUnitsInput(unitsInput.value);
  drawRoutesForUnits(parsedUnits, currentIncidentCoords.lat, currentIncidentCoords.lng, null);
}

function clearRoutesOnly() {
  if (dispatchRoutes.length) {
    dispatchRoutes.forEach(r => dispatchMap.removeLayer(r));
    dispatchRoutes = [];
  }
}

function clearDispatchMap() {
  if (dispatchMarker) { dispatchMap.removeLayer(dispatchMarker); dispatchMarker = null; }
  clearRoutesOnly();
  currentIncidentCoords = null;
  if (dispatchMap) {
    dispatchMap.setView([37.3886, -5.9823], 13);
  }
}

// Expose globally for app.js to call.
window.initDispatchMap = initDispatchMap;
window.updateDispatchMap = updateDispatchMap;
window.updateDispatchMapFromInput = updateDispatchMapFromInput;
window.clearDispatchMap = clearDispatchMap;
