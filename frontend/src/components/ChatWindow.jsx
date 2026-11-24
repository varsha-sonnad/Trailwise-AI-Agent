// ChatWindow.jsx: the whole chat area: the conversation on top and the message box below.
// It only shows what it is given; the conversation state lives in the useChat hook (see pages/Home.jsx).

import ChatInput from "./ChatInput";
import MessageList from "./MessageList";

// messages: the conversation; pending: waiting for a reply; onSend(text): send a message; replyLanguage: the saved reply language.
export default function ChatWindow({ messages, pending, onSend, replyLanguage }) {
  return (
    <>
      <MessageList messages={messages} pending={pending} />
      <ChatInput onSend={onSend} disabled={pending} replyLanguage={replyLanguage} />
    </>
  );
}
