import { useState } from 'react'

export default function ConsentScreen({ onAccept, loading }) {
  const [checked, setChecked] = useState(false)

  return (
    <div className="min-h-screen flex items-center justify-center px-6 py-10">
      <div className="max-w-lg w-full animate-page-in">
        <h1 className="font-display text-2xl text-ink-heading mb-2">Before we begin</h1>
        <p className="text-sm text-parchment-500 mb-6">
          This is a short, confidential interview to capture the knowledge you carry
          in your role, so whoever comes next has the best possible start.
        </p>

        <div className="bg-parchment-50 border border-parchment-200 rounded-xl p-5 text-sm text-ink space-y-3 mb-6">
          <div>
            <span className="font-medium">What happens to your answers.</span>{' '}
            Your raw answers are never shown to your manager. They are used only to
            produce a synthesised handover document, which is shared with your
            employer's designated recipients.
          </div>
          <div>
            <span className="font-medium">This is not an assessment.</span>{' '}
            There are no right or wrong answers, and nothing here is used to evaluate
            you. You can skip anything you'd rather not answer.
          </div>
          <div>
            <span className="font-medium">Retention.</span>{' '}
            Your interview data is stored securely and deleted once the handover is
            complete and the retention period has passed.
          </div>
          <div>
            <span className="font-medium">Voluntary.</span>{' '}
            Taking part is voluntary. You can stop at any time by closing this window.
          </div>
        </div>

        <label className="flex items-start gap-3 mb-6 cursor-pointer">
          <input
            type="checkbox"
            checked={checked}
            onChange={(e) => setChecked(e.target.checked)}
            className="mt-1 h-4 w-4 accent-keeper-500"
          />
          <span className="text-sm text-ink">
            I understand how my answers will be used and I consent to taking part.
          </span>
        </label>

        <button
          onClick={onAccept}
          disabled={!checked || loading}
          className="w-full py-3 rounded-xl bg-keeper-500 text-white font-medium hover:bg-keeper-400 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
        >
          {loading ? (
            <>
              <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              Starting…
            </>
          ) : (
            'I consent — begin the interview'
          )}
        </button>
      </div>
    </div>
  )
}
