// places.js: the small hardcoded list behind the Home dashboard on /app: six places and three quick-start chips.
// The pictures are local placeholder images in frontend/public/places/ (small illustrations, not photos). To use real
// photos later, put the files in that folder and change the `image` paths below.
// Rule: no timings or prices anywhere in this file, because the cards must never state travel data we do not have.

// The six places, in the order the cards appear. id: used by the mock data; image: path served from /public.
export const PLACES = [
  { id: "goa", name: "Goa", tagline: "Sun, sand and slow evenings", image: "/places/goa.svg" },
  { id: "jaipur", name: "Jaipur", tagline: "Forts, palaces and bazaars", image: "/places/jaipur.svg" },
  { id: "kerala", name: "Kerala backwaters", tagline: "Canals, houseboats and green fields", image: "/places/kerala.svg" },
  { id: "varanasi", name: "Varanasi", tagline: "Ghats, temples and evening aarti", image: "/places/varanasi.svg" },
  { id: "ladakh", name: "Ladakh", tagline: "Mountain passes and big skies", image: "/places/ladakh.svg" },
  { id: "hampi", name: "Hampi", tagline: "Ancient ruins by the river", image: "/places/hampi.svg" },
];

// The quick-start chips: the label on the chip, and the message that is pre-filled in Chat when it is clicked.
export const QUICK_STARTS = [
  { label: "Weekend getaway", prompt: "Plan a weekend getaway" },
  { label: "Beach trip", prompt: "Plan a beach trip" },
  { label: "Mountain trek", prompt: "Plan a mountain trek" },
];

// Find a place by its id (undefined if there is none).
export function placeById(id) {
  return PLACES.find((p) => p.id === id);
}

// The message pre-filled in Chat when a place card is clicked, e.g. "Plan a 3-day trip to Goa".
export function tripPrompt(place) {
  return `Plan a 3-day trip to ${place.name}`;
}
