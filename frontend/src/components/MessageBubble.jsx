// MessageBubble.jsx: one message in the conversation.
// User and error messages are plain text. Bot replies are rendered as Markdown, safely:
// react-markdown never inserts raw HTML (skipHtml drops it), and unsafe links such as javascript: are
// removed by its default URL check. We never use dangerouslySetInnerHTML (plan: sanitize AI markdown).

import ReactMarkdown from "react-markdown";
import ImageRow, { usableImages } from "./ImageRow";
import { hasMap, validPlaces } from "./mapData";

// The place name shown under the photos: only when the reply has exactly one place (photos carry no place name of their own,
// so with several places a caption could be wrong and none is shown).
function placeCaption(places) {
  const pins = validPlaces(places);
  return pins.length === 1 ? pins[0].name : "";
}

// Open links from the AI in a new tab, without giving that page access to this one.
const markdownComponents = {
  a: ({ node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" />,
};

// role: "user" | "bot" | "error"; text: the message text; images: the photos of a bot reply (shown as a row under the text,
// with a small credit on each; see ImageRow); places: the reply's places; onShowMap: opens the map panel for this reply
// (the "Show map" button only exists when the reply has at least one place that can be pinned).
export default function MessageBubble({ role, text, images, places, onShowMap }) {
  if (role === "bot") {
    const hasImages = usableImages(images).length > 0;
    return (
      <div className={`msg bot${hasImages ? " has-images" : ""}`}>
        <ReactMarkdown skipHtml components={markdownComponents}>
          {text}
        </ReactMarkdown>
        <ImageRow images={images} caption={hasMap(places) ? placeCaption(places) : ""} />
        {onShowMap && hasMap(places) && (
          <button type="button" className="show-map" onClick={onShowMap}>
            Show map
          </button>
        )}
      </div>
    );
  }
  return <div className={`msg ${role}`}>{text}</div>;
}
