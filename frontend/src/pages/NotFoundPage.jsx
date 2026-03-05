import { Link } from 'react-router-dom'

export default function NotFoundPage() {
  return (
    <div className="min-h-screen flex items-center justify-center px-6">
      <div className="text-center animate-page-in">
        <p className="text-6xl font-display text-parchment-300 mb-4">404</p>
        <h1 className="font-display text-2xl text-ink-heading mb-2">Page not found</h1>
        <p className="text-sm text-parchment-500 mb-8">
          The page you're looking for doesn't exist or the session has expired.
        </p>
        <Link
          to="/"
          className="inline-flex px-6 py-2.5 rounded-xl bg-keeper-500 text-white font-medium text-sm hover:bg-keeper-400 transition-colors"
        >
          Back to Home
        </Link>
      </div>
    </div>
  )
}
