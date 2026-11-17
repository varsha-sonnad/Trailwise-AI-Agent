// MessageBubble.jsx: one message in the conversation.
// User and error messages are plain text. Bot replies are rendered as Markdown, safely:
// react-markdown never inserts raw HTML (skipHtml drops it), and unsafe links such as javascript: are
// removed by its default URL check. We never use dangerouslySetInnerHTML (plan: sanitize AI markdown).

import ReactMarkdown from "react-markdown";

// Open links from the AI in a new tab, without giving that page access to this one.
const markdownComponents = {
  a: ({ node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" />,
};

// role: "user" | "bot" | "error"; text: the message text.
export default function MessageBubble({ role, text }) {
  if (role === "bot") {
    return (
      <div className="msg bot">
        <ReactMarkdown skipHtml components={markdownComponents}>
          {text}
        </ReactMarkdown>
      </div>
    );
  }
  return <div className={`msg ${role}`}>{text}</div>;
}
