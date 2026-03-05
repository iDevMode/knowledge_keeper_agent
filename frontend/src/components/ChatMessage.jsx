export default function ChatMessage({ role, content }) {
  const isAgent = role === 'agent'

  return (
    <div
      className={`flex ${isAgent ? 'justify-start' : 'justify-end'} animate-message-in`}
    >
      <div
        className={`max-w-[80%] rounded-2xl px-5 py-3.5 text-[15px] leading-relaxed whitespace-pre-wrap ${
          isAgent
            ? 'bg-parchment-100 text-ink rounded-bl-md'
            : 'bg-keeper-500 text-white rounded-br-md'
        }`}
      >
        {content}
      </div>
    </div>
  )
}
