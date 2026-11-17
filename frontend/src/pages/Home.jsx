// Home.jsx: the main screen: chat with the Trailwise agent.
// It owns the conversation (useChat) and hands it to the ChatWindow to display.

import ChatWindow from "../components/ChatWindow";
import { useChat } from "../hooks/useChat";

export default function Home() {
  const { messages, pending, send } = useChat();
  return <ChatWindow messages={messages} pending={pending} onSend={send} />;
}
