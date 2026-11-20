// ImageRow.jsx: the row of photos shown under a bot reply in the chat. The backend sends them in the response's
// "images" (the model never lists or credits photos in its text). Each photo is large, has NO caption, and carries a small
// corner credit "Photo by <name> on Pexels" that links to the photographer (Pexels requires the credit).
// Safety: only https addresses on pexels.com (or a subdomain) are used, for the picture and for the link; anything else is
// skipped, a photo without a photographer name is skipped, and every text value is rendered as plain text by React.

import { useEffect, useRef, useState } from "react";

// The most photos shown under one reply (the backend sends at most this many too).
const MAX_IMAGES = 6;

// True only for an https address on pexels.com or one of its subdomains (for example images.pexels.com).
export function isPexelsUrl(value) {
  if (typeof value !== "string" || !value || value.length > 500) return false;
  try {
    const { protocol, hostname } = new URL(value);
    const host = hostname.toLowerCase();
    return protocol === "https:" && (host === "pexels.com" || host.endsWith(".pexels.com"));
  } catch {
    return false;
  }
}

// The photos that may be shown: a Pexels picture and a photographer name, no duplicates, at most MAX_IMAGES. Each one comes
// back with `link`: where its credit points (the photographer's page, else the photo's own Pexels page, else nothing).
export function usableImages(images) {
  if (!Array.isArray(images)) return [];
  const seen = new Set();
  const out = [];
  for (const image of images) {
    if (!image || typeof image !== "object") continue;
    const { url, alt, photographer, photographer_url: photographerUrl, pexels_url: pexelsUrl } = image;
    if (!isPexelsUrl(url) || seen.has(url)) continue;
    if (typeof photographer !== "string" || !photographer.trim()) continue; // no credit, no photo
    seen.add(url);
    const link = isPexelsUrl(photographerUrl) ? photographerUrl : isPexelsUrl(pexelsUrl) ? pexelsUrl : "";
    out.push({ url, alt: typeof alt === "string" ? alt : "", photographer: photographer.trim(), link });
    if (out.length >= MAX_IMAGES) break;
  }
  return out;
}

// images: the "images" list of a reply (it may be missing or empty: then nothing is drawn); caption: the place name shown
// under each card (empty = no caption). Up to 3 compact cards in a scroll-snap row; a round arrow button scrolls it when it overflows.
export default function ImageRow({ images, caption = "" }) {
  const items = usableImages(images).slice(0, 3);
  const row = useRef(null);
  const [overflow, setOverflow] = useState(false); // more cards than fit, and not yet scrolled to the end

  // Re-check whether the row overflows whenever it scrolls or changes size.
  useEffect(() => {
    const node = row.current;
    if (!node) return undefined;
    const check = () => setOverflow(node.scrollWidth - node.clientWidth - node.scrollLeft > 4);
    check();
    node.addEventListener("scroll", check, { passive: true });
    let observer = null;
    if (typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(check);
      observer.observe(node);
    }
    return () => {
      node.removeEventListener("scroll", check);
      if (observer) observer.disconnect();
    };
  }, [items.length]);

  if (items.length === 0) return null;
  // The arrow scrolls the row by one card.
  const next = () => row.current && row.current.scrollBy({ left: 232, behavior: "smooth" });
  return (
    <div className="image-wrap">
      <div className="image-row" role="group" aria-label="Photos" ref={row}>
        {items.map((image) => {
          const credit = `Photo by ${image.photographer} on Pexels`;
          return (
            <figure className="image-card" key={image.url}>
              {/* The alt text is only for screen readers. */}
              <img src={image.url} alt={image.alt} loading="lazy" referrerPolicy="no-referrer" />
              {image.link ? (
                <a className="image-credit" href={image.link} target="_blank" rel="noopener noreferrer">
                  {credit}
                </a>
              ) : (
                <span className="image-credit">{credit}</span>
              )}
              {caption && <figcaption className="image-caption">{caption}</figcaption>}
            </figure>
          );
        })}
      </div>
      {overflow && (
        <button type="button" className="image-next" onClick={next} aria-label="Next photo">
          &#8250;
        </button>
      )}
    </div>
  );
}
