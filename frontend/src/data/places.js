// places.js: the small hardcoded list behind the Home dashboard and the Explore page on /app: six places and three
// quick-start chips, plus a mock place search for the Explore search box.
// The pictures are local placeholder images in frontend/public/places/ (small illustrations, not photos). To use real
// photos later, put the files in that folder, change the `image` path and set `credit` (and `creditUrl`) to the
// photographer's name and link, for example credit: "Photo: Jane Doe on Pexels". The Explore panel shows `credit` under
// the picture. Until real photos are in, the credit says the picture is a Trailwise illustration.
// Rule: no timings or prices anywhere in this file, because the cards must never state travel data we do not have.

// The six places, in the order the cards appear.
//   id: used by the mock data; name / tagline: the card (tagline is the one-line description);
//   tags: which of the ten interests (see api/preferences.js) the place suits, used only to rank the cards (rankPlaces below), never shown;
//   region + description: the Explore side panel; image: path served from /public; credit / creditUrl: shown under the picture.
export const PLACES = [
  {
    id: "goa", name: "Goa", tagline: "Sun, sand and slow evenings", image: "/places/goa.svg",
    region: "Goa, India",
    tags: ["beach", "food", "relaxing", "culture"],
    description: "A coastal state known for its beaches, old churches and relaxed pace. Many visitors come for the sea, the food and slow evenings by the water.",
    credit: "Illustration: Trailwise", creditUrl: "",
  },
  {
    id: "jaipur", name: "Jaipur", tagline: "Forts, palaces and bazaars", image: "/places/jaipur.svg",
    region: "Rajasthan, India",
    tags: ["heritage", "culture", "food"],
    description: "The Pink City, known for its hilltop forts, palaces and busy bazaars. A good fit if you enjoy history, architecture and street-side shopping.",
    credit: "Illustration: Trailwise", creditUrl: "",
  },
  {
    id: "kerala", name: "Kerala backwaters", tagline: "Canals, houseboats and green fields", image: "/places/kerala.svg",
    region: "Kerala, India",
    tags: ["nature", "relaxing"],
    description: "A network of calm canals and lagoons lined with palms and rice fields. Houseboats and village life make it a slow, green kind of trip.",
    credit: "Illustration: Trailwise", creditUrl: "",
  },
  {
    id: "varanasi", name: "Varanasi", tagline: "Ghats, temples and evening aarti", image: "/places/varanasi.svg",
    region: "Uttar Pradesh, India",
    tags: ["spiritual", "heritage", "culture"],
    description: "One of the oldest living cities, built along the Ganga with stepped ghats and many temples. The evening aarti by the river is the moment many visitors remember.",
    credit: "Illustration: Trailwise", creditUrl: "",
  },
  {
    id: "ladakh", name: "Ladakh", tagline: "Mountain passes and big skies", image: "/places/ladakh.svg",
    region: "Ladakh, India",
    tags: ["mountains", "adventure", "nature"],
    description: "A high-altitude region of mountain passes, monasteries and wide open landscapes. It rewards visitors who like quiet roads and big skies, and it calls for careful planning.",
    credit: "Illustration: Trailwise", creditUrl: "",
  },
  {
    id: "hampi", name: "Hampi", tagline: "Ancient ruins by the river", image: "/places/hampi.svg",
    region: "Karnataka, India",
    tags: ["heritage", "culture"],
    description: "A World Heritage site of ancient temples and boulder-strewn ruins beside the Tungabhadra river. It suits anyone who likes to wander through history on foot.",
    credit: "Illustration: Trailwise", creditUrl: "",
  },
];

// The quick-start chips: the label on the chip, and the message that is pre-filled in Chat when it is clicked.
export const QUICK_STARTS = [
  { label: "Weekend getaway", prompt: "Plan a weekend getaway" },
  { label: "Beach trip", prompt: "Plan a beach trip" },
  { label: "Mountain trek", prompt: "Plan a mountain trek" },
];

// Put the places that match the chosen interests first. A place scores one point for each of its tags that is among
// prefs.interests; a higher score moves it up and ties keep their current order. It is only a reorder: every place is
// always returned, exactly once, in a new array (the input is never changed), so nothing is ever hidden. Budget style and
// travel mode are not used because places have no field for them. No preferences (or invalid ones) keep the order as it is.
export function rankPlaces(places, prefs) {
  const wanted = Array.isArray(prefs?.interests) ? prefs.interests : [];
  const score = (p) => (Array.isArray(p.tags) ? p.tags.filter((t) => wanted.includes(t)).length : 0);
  return places
    .map((place, index) => ({ place, index, score: score(place) }))
    .sort((a, b) => b.score - a.score || a.index - b.index) // higher score first, then the original order
    .map((x) => x.place);
}

// Find a place by its id (undefined if there is none).
export function placeById(id) {
  return PLACES.find((p) => p.id === id);
}

// The message pre-filled in Chat when a Home place card is clicked, e.g. "Plan a 3-day trip to Goa".
export function tripPrompt(place) {
  return `Plan a 3-day trip to ${place.name}`;
}

// The message pre-filled in Chat by "Plan a Trip" in the Explore panel, e.g. "Plan a trip to Goa" (name: any place name).
// When the saved preferences have a budget style and/or travel mode, they are added, e.g.
// "Plan a trip to Goa. Budget style: comfort. Travel mode: train or bus." Only what is set is added; no prices or timings.
export function planPrompt(name, prefs) {
  const parts = [`Plan a trip to ${name}`];
  if (prefs?.budget) parts.push(`Budget style: ${prefs.budget}`);
  if (Array.isArray(prefs?.travel_mode) && prefs.travel_mode.length) parts.push(`Travel mode: ${prefs.travel_mode.join(" or ")}`);
  return parts.length > 1 ? `${parts.join(". ")}.` : parts[0];
}

// The message pre-filled in Chat by "Learn More" in the Explore panel, e.g. "Tell me more about Goa".
export function learnPrompt(name) {
  return `Tell me more about ${name}`;
}

// How long the mock search pretends to take, in milliseconds, so the "Searching..." state can be seen.
const MOCK_SEARCH_DELAY_MS = 700;

// TODO(backend): MOCK. Replace with a real search (Explore mode in the backend) when it exists. It only looks in the six
// hardcoded places: a place matches when its name, id or region contains what was typed, or when the typed text contains
// the first word of its name (so "kerala" and "Goa beaches" both work). It resolves to the place, or to null when
// nothing matches. It never calls the network and uses no key.
export function mockSearchPlace(query) {
  const text = String(query || "").trim().toLowerCase();
  return new Promise((resolve) => {
    setTimeout(() => {
      if (text.length < 2) return resolve(null);
      const found = PLACES.find((p) => {
        const firstWord = p.name.toLowerCase().split(" ")[0];
        return (
          p.name.toLowerCase().includes(text) || p.id.includes(text) || p.region.toLowerCase().includes(text) ||
          (firstWord.length >= 3 && text.includes(firstWord))
        );
      });
      resolve(found || null);
    }, MOCK_SEARCH_DELAY_MS);
  });
}
