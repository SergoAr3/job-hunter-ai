# Discover Query & Continuity v1

Web keeps confirmed search/navigation state in the Discover URL. FastAPI remains
authoritative for search, filtering, matching, saving and Application state.
No API, database, matcher or provider changes are needed.

## URL and drafts

`/discover` accepts `query`, `remote_only`, `offset` and optional `selected`.
The last field is the JSON tuple `[source, source_scope, external_id]`, encoded
by URLSearchParams; it is never an array index. The default false remote filter
and zero offset are omitted. Offset retains the existing source record-offset
semantics, including non-page-aligned values received in a deep link.

Typing and checkbox changes affect only a draft. Submit confirms trimmed query
and remote choice, resets offset to zero and clears selection. Pagination uses
confirmed values even when the controls have an unsubmitted draft. Submitting
the same search explicitly refreshes it without adding an identical history entry.

Missing/blank/overlong/control-containing or duplicate query means no search.
Invalid/duplicate remote is false; unsafe, negative, fractional or duplicate
offset is zero. Malformed selection is discarded. Unknown parameters are removed.
Canonicalization uses one router replace and does not refetch equivalent state.

## Navigation and selection

Search and pagination use Next App Router push with scroll disabled. Back/Forward
read useSearchParams, reset controls and request fresh results. A navigation
revision prevents an earlier result for A from appearing current during A → B → A.
Refresh/remount also fetches again; persisted browser pageshow refreshes the search.
There is no historical results cache or persisted search history.

Selection replaces the current history entry through Next's documented native
history integration with useSearchParams. It makes neither a Discover GET nor a
server navigation/auth call. New results resolve selection by the complete source
identity; absent identity means no detail panel. Mobile detail focuses after the
selection is rendered, and closing it returns focus to the result card.

Discover Application links retain the existing `from` contract. The shared return
validator accepts only the existing Applications list URL or a Discover URL with
allowlisted, non-duplicate parameters. External origins, protocol-relative paths,
backslashes, fragments and unsupported paths/parameters fall back to Applications.
Application detail labels the link according to its validated destination and
retains it on retry. No separate return storage is introduced.
Login's safe-next URL bound is 8192 characters so a nested, encoded supported
200-character Unicode query can survive session renewal. Its existing internal
path, origin, encoding and control-character restrictions are unchanged.

## Profile and requests

Role suggestions make one optional Profile GET per mounted suggestions component.
StrictMode effect replay reuses that request. Missing, failed or malformed profile
data hides suggestions without blocking search. Clicking a role changes draft
text only; it never modifies Profile or applies location/workplace preferences.

Each confirmed search activation makes one Discover GET. Selection/draft changes
make none. Query/remote/offset changes, Back/Forward, remount and explicit retry
fetch current data. Pending requests are reused across StrictMode effect replay;
superseded requests are aborted and late responses cannot replace current results.
Existing save reconciliation still fills only an unsaved item of the same identity;
a loaded authoritative Application wins over an older Save response.

## Validation

Targeted Web tests cover role suggestions, URL normalization/round-trip, draft
semantics, pagination, history navigation, remount/BFCache refresh, source identity,
Application return safety, Applications-list compatibility, and search/save races.
They use mocked HTTP and a test-only Next navigation subscription. No external
vacancy requests or project runtime are needed. Real-browser smoke remains a
separate follow-up when an existing runtime is available.

Default matcher stays `job-match-v2.1`. Search Preferences, recent searches,
market/location selectors, reranking and new sources are outside this slice.
