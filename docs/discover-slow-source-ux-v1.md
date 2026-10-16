# Discover Slow-source UX v1

This Web-only presentation change makes the existing source wait visible without
changing HTTP, source cache, providers, matcher, timeouts, retries or contracts.
It does not reduce first-MISS network latency.

## Loading presentation

Without an available page, show a white loading card with a violet search icon,
three subtle animated points and four static vacancy-shaped skeleton cards.
Decorative motion and skeletons are hidden from assistive technology. CSS disables
all new animations for `prefers-reduced-motion: reduce`.

The initial message is “Ищем подходящие вакансии…” with “Проверяем источник и
обновляем результаты”. After 2800 ms it changes to “Источник отвечает медленнее
обычного” with “Продолжаем ждать актуальные вакансии”. A polite, atomic status
region announces the text. No request is triggered by the timer.

Discover owns one presentation timer keyed by the existing request activation key.
Both loading presentations consume its slow state; there is no second timer. A new
confirmed search, pagination, Back/Forward or retry starts a fresh timer. Completion,
error, navigation to an empty search and unmount clear it; effect replay cleans it up.
There is no minimum display delay and no historical results cache.

## Previous results and correctness

While a new activation loads, only the last result remains available as a
placeholder. Its stored canonical search URL is presentation metadata used to
distinguish next/previous pagination from a different search or refresh.
Confirmed URL state and the existing request-generation checks remain authoritative.

Previous results have an explicit previous-page label, a muted treatment and
`aria-busy`, with a description referring to the loading status. All vacancy-card
and pagination buttons are disabled. Selection/details, Save, source links and
Application links derive only from a current response, so they are absent during
stale display. This also applies when returning to the same URL before its fresh
response arrives. Draft editing and explicit submission of another query remain
available through the existing form flow.

On success the fresh page replaces the placeholder and selection resolves by the
existing complete identity. On error the placeholder and loading feedback disappear;
the existing error and manual Retry remain. Retry starts a new threshold. Submitting
still clears selection; BFCache refresh preserves the URL and restores selection
only after fresh data. Application return URLs are unchanged.

## Bottom pagination feedback (P2 fix)

Retaining a long list also retains the bottom scroll position. The upper loading
block can be outside the viewport after Next, so a compact visual duplicate now
occupies the center of the pagination controls while previous results are shown.
It reuses the same text and slow state, disappears on completion/error, and is
`aria-hidden` with no live role: only the upper status announces progress.

Pagination reserves room for both text phases to keep controls steady. At widths
up to 520 px the message occupies its own row above the two buttons. There is no
sticky overlay, scrolling change or additional request.

## Validation and remaining smoke

Discover component, state, continuity and Application-return tests use mocked HTTP.
New fake-timer cases cover initial/slow/fast responses, StrictMode, disabled stale
actions, page replacement, error/retry, Back/Forward, late responses, BFCache selection
and timer cleanup on navigation/unmount. Existing helpers now wait for enabled
current cards rather than treating a visible disabled placeholder as a result.

P2 validation: 97 targeted Web tests pass, along with TypeScript, targeted ESLint,
Prettier and whitespace checks. The long-page regression checks that the visual
duplicate lives inside pagination, shares exactly one timer, has no live role,
and disappears on success/error. Backend suites were not run.

Live smoke reused the existing runtime and the same Python Engineer query/page
keys. The original upper-only feedback was off-screen after Next from the bottom
of ten cards. After the fix, bottom feedback was visible immediately and through
the slow transition on desktop and at 320/480 px, with both controls visible.
At 480 px document scrollWidth equaled clientWidth. Completion removed feedback;
the final fast Next/Back sequence on warmed pages showed no slow feedback.
Observations spanning cache expiry also caused ordinary source misses; no cache
reset, new query variants, artificial source errors or service restarts were used.
API worker/reloader remained unchanged. Real screen-reader announcement testing
and live error injection were not performed; semantics/error cleanup are covered
by component tests.
