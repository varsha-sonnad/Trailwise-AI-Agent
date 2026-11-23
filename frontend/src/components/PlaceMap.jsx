// PlaceMap.jsx: the Leaflet map inside the map panel. Plain Leaflet (no wrapper library), loaded only when a map is shown.
// - One pin for every place of the reply (name label always visible), fitted into view (one place: zoom 11).
// - The route line when the reply has one.
// - A small card on a pin: shown on hover (and on tap on phones) with the place name and, only when the reply's "images" has a
//   usable photo, one photo with its "Photo by <name> on Pexels" credit. No photo means no photo; nothing is invented.
// - Tiles from VITE_MAP_TILE_URL (default: OpenStreetMap), with the OpenStreetMap credit always on the map.
// - Marker icons are imported explicitly (Leaflet's own icon lookup breaks under Vite). The map box has a fixed height in
//   styles.css (.map-canvas), because Leaflet draws nothing in a box with no height.
// Safety: the card and the labels are built with DOM text (never innerHTML), so a place name or a photographer's name from a
// reply can never become markup. Leaflet itself is imported inside the effect, so rendering this file on a server does not touch it.

import { useEffect, useRef } from "react";
import "leaflet/dist/leaflet.css";
import markerIcon from "leaflet/dist/images/marker-icon.png";
import markerIcon2x from "leaflet/dist/images/marker-icon-2x.png";
import markerShadow from "leaflet/dist/images/marker-shadow.png";
import { LEGS_CAPTION, OSM_ATTRIBUTION, cardModel, mapView, tileUrl, validLegs, validPlaces, validRoute } from "./mapData";

// One element made with the given document (a parameter so the tests can pass a small fake); text always goes in as text.
function el(doc, tag, className, text) {
  const node = doc.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// The card shown on a pin: [photo + credit] and the place name, all as DOM text. model comes from cardModel().
export function buildCard(model, doc = document) {
  const card = el(doc, "div", "pin-card");
  if (model.photo) {
    const image = el(doc, "img", "pin-card-photo");
    image.setAttribute("src", model.photo.url);
    image.setAttribute("alt", model.photo.alt || "");
    image.setAttribute("loading", "lazy");
    image.setAttribute("referrerpolicy", "no-referrer");
    card.appendChild(image);
  }
  const body = el(doc, "div", "pin-card-body");
  body.appendChild(el(doc, "strong", "pin-card-name", model.name));
  if (model.photo) {
    if (model.photo.link) {
      const credit = el(doc, "a", "pin-card-credit", model.photo.credit);
      credit.setAttribute("href", model.photo.link);
      credit.setAttribute("target", "_blank");
      credit.setAttribute("rel", "noopener noreferrer");
      body.appendChild(credit);
    } else {
      body.appendChild(el(doc, "span", "pin-card-credit", model.photo.credit));
    }
  }
  card.appendChild(body);
  return card;
}

// places, route: the reply's lists (checked again here); images: the reply's photos (for the card of the first pin).
export default function PlaceMap({ places, route, legs, images }) {
  const box = useRef(null);

  useEffect(() => {
    let map = null;
    let timer = null;
    let observer = null;
    let cancelled = false;
    let panel = null;
    const onEnd = () => map && map.invalidateSize();
    const pins = validPlaces(places);
    const line = validRoute(route);
    const lines = validLegs(legs);
    const view = mapView(pins, line, lines);
    if (!view || !box.current) return undefined;

    (async () => {
      // Leaflet is loaded here, when the panel opens (it needs a browser, so it is never loaded on a server).
      const loaded = await import("leaflet");
      const L = loaded.default ?? loaded;
      if (cancelled || !box.current) return;

      const icon = L.icon({
        iconUrl: markerIcon, iconRetinaUrl: markerIcon2x, shadowUrl: markerShadow,
        iconSize: [25, 41], iconAnchor: [12, 41], popupAnchor: [1, -34], tooltipAnchor: [16, -28], shadowSize: [41, 41],
      });
      // Give the box a real pixel size before the map exists (the panel is still growing, so CSS alone may give 0 or a tiny box).
      const size = box.current.getBoundingClientRect();
      if (size.height < 1) box.current.style.height = "520px";
      if (size.width < 1) box.current.style.minWidth = "320px";
      map = L.map(box.current, { zoomControl: true });
      L.tileLayer(tileUrl(import.meta.env.VITE_MAP_TILE_URL), { attribution: OSM_ATTRIBUTION, maxZoom: 19 }).addTo(map);
      if (line.length > 0) L.polyline(line, { color: "#c2410c", weight: 4, opacity: 0.85 }).addTo(map);
      // Road legs are solid; every other suggested connection (trek, pony, helicopter, train or bus link) is dashed.
      lines.forEach((leg) =>
        L.polyline(leg.points, leg.kind === "road"
          ? { color: "#c2410c", weight: 4, opacity: 0.85 }
          : { color: "#475569", weight: 3, opacity: 0.9, dashArray: "8 8" }).addTo(map));

      // Hovering opens the card where the device can hover; a tap opens it everywhere (Leaflet's own click on a bound popup).
      const canHover = typeof window.matchMedia === "function" && window.matchMedia("(hover: hover)").matches;
      pins.forEach((place, index) => {
        const marker = L.marker([place.lat, place.lng], { icon, title: place.name, alt: place.name }).addTo(map);
        marker.bindTooltip(el(document, "span", "", place.name), { permanent: true, direction: "right", offset: [10, -22], className: "pin-label" });
        marker.bindPopup(buildCard(cardModel(place, index, images)), { maxWidth: 260, minWidth: 200, className: "pin-popup", autoPan: true });
        if (canHover) marker.on("mouseover", () => marker.openPopup());
      });

      if (view.kind === "single") map.setView(view.center, view.zoom);
      else map.fitBounds(view.bounds, { padding: [48, 48], maxZoom: view.maxZoom });
      // The panel grows while it opens (and the chat shrinks), so the map is measured again whenever its box changes size.
      if (typeof ResizeObserver !== "undefined") {
        observer = new ResizeObserver(() => map && map.invalidateSize());
        observer.observe(box.current);
      }
      // After the panel's open transition ends, measure again (fallback timer in case the event never fires).
      panel = box.current.closest(".map-panel");
      if (panel) panel.addEventListener("animationend", onEnd);
      timer = setTimeout(() => map && map.invalidateSize(), 350);
    })().catch(() => {
      // Leaflet could not load (for example offline): the panel simply stays empty.
    });

    return () => {
      cancelled = true;
      clearTimeout(timer);
      if (observer) observer.disconnect();
      if (panel) panel.removeEventListener("animationend", onEnd);
      if (map) map.remove();
    };
  }, [places, route, legs, images]);

  // The caption only appears when the reply has dashed connections.
  const hasSuggested = validLegs(legs).some((leg) => leg.kind === "other");
  return (
    <>
      <div ref={box} className="map-canvas" role="region" aria-label="Map of the places in this reply" />
      {hasSuggested && <p className="map-legs-caption">{LEGS_CAPTION}</p>}
    </>
  );
}
