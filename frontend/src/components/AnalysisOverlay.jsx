import { useEffect, useState } from 'react'
import EarthLoader from './EarthLoader'

/**
 * What the map shows while an analysis request is in flight.
 *
 * A run takes roughly a minute. One unchanging sentence for that long reads as
 * a crash, so the stages below rotate. They are the pipeline's real stages in
 * the real order run_change_detection executes them - but the backend is a
 * single blocking POST with no progress stream, so they advance on a timer
 * rather than on measured progress. That is why there is no percentage and no
 * "step 3 of 6": the interface should not claim to know something it cannot.
 *
 * The elapsed clock is genuine, and it is the honest reassurance here.
 */
const PHASES = [
  'Searching the Sentinel-2 archive',
  'Masking cloud and compositing both dry seasons',
  'Matching the earlier imagery onto the later radiometric scale',
  'Computing AMCBI and classifying built-up',
  'Discarding change that sits on a decision boundary',
  // Only reached when the wetland analysis was requested - it is the slow part,
  // roughly a minute on its own, and it runs last.
  'Measuring growth by distance from the shoreline',
]

const PHASE_SECONDS = 6

export default function AnalysisOverlay({ includeWetland }) {
  const phases = includeWetland ? PHASES : PHASES.slice(0, -1)
  const [elapsed, setElapsed] = useState(0)

  useEffect(() => {
    const id = setInterval(() => setElapsed((seconds) => seconds + 1), 1000)
    return () => clearInterval(id)
  }, [])

  // Derived rather than held in its own timer, so the caption can never drift
  // out of step with the clock beside it. Holds on the last stage instead of
  // looping: a run that outlasts the list is running long, not starting over.
  const phase = Math.min(Math.floor(elapsed / PHASE_SECONDS), phases.length - 1)

  return (
    <div className="map-overlay" role="status">
      <EarthLoader size={120} />

      <div className="overlay-text">
        {/* The only line announced to a screen reader. The caption below changes
            every few seconds and the clock every second; both would interrupt
            continuously inside a live region. */}
        <p className="overlay-lead">Analysing imagery. This usually takes about a minute.</p>

        {/* Remounting on each change replays the fade instead of swapping the
            text in place. The height is reserved in CSS so the longest stage
            does not shunt the clock down when it wraps. */}
        <p className="overlay-phase" key={phase} aria-hidden="true">
          {phases[phase]}
        </p>

        <span className="overlay-elapsed num" aria-hidden="true">
          {formatElapsed(elapsed)}
        </span>
      </div>
    </div>
  )
}

function formatElapsed(seconds) {
  if (seconds < 60) return `${seconds}s`
  return `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, '0')}s`
}
