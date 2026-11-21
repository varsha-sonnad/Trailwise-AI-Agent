// Chat.jsx: the chat screen. It shows the conversation held by App (through the useChat hook)
// and the message box. Everything that used to be the old Home page now lives here.

import ChatSplit from "../components/ChatSplit";
import ChatWindow from "../components/ChatWindow";

// messages / pending / onSend come from useChat in App.jsx.
export default function Chat({ messages, pending, onSend }) {
  return (
    // The map panel (when a reply has places) becomes a second column next to the chat page.
    <ChatSplit messages={messages}>
      <div className="chat-page">
        <ChatWindow messages={messages} pending={pending} onSend={onSend} />
      </div>
    </ChatSplit>
  );
}
