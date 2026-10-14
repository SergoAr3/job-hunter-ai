# Vacancy source boundary

Discover uses `VacancySourceProvider` from
`apps/api/app/services/vacancy_sources.py`. The default and only connected
provider is `TrudvsemProvider`, wrapping the existing official Trudvsem client.
API query/response schemas and Bot/Web flows remain unchanged.

`DiscoverQuery` contains trimmed keywords, uppercase search market, optional
administrative region selection within that market, and the existing limit/offset.
`market_region` currently carries the existing RU region selector value; the
Trudvsem provider maps it to its region endpoint. It is not free-text vacancy
location, user residence, work authorization, or international eligibility.
The existing `remote_only` filter stays local to Discover, after source pagination;
it does not claim that the external source searched only remote jobs.

Providers own request translation, HTTP/timeout handling, external JSON parsing,
source field normalization, original source URL, identity and source pagination.
`ExternalVacancy` and `SearchPage` contain only current downstream fields.
The generic service does not import Trudvsem DTOs or know its JSON schema.
Source failures derive from `SourceError`; current codes remain
`source_unavailable`, `source_timeout`, `source_rate_limited`,
`source_bad_response`, and `vacancy_not_found`. Invalid queries/identities use
`ValueError`, translated by the existing routes. No response payload or credentials
are logged. Trudvsem retains its 25-second timeout, bounded response size and
single request per operation; this refactor adds no retries.

Discover owns user/profile checks, product URL normalization, local remote filtering,
saved-state lookup, deterministic dedupe, transactions, Job/Application persistence,
AI enrichment after save, and the existing legacy match preview. Network calls
remain outside DB transactions. Persistence identifies a vacancy by
`(source, source_scope, external_id)`; Trudvsem's scope is the company code.
A source without a scoped ID must supply a stable scope according to its official
contract. Original URLs are displayed; existing canonical URL rules apply on save.
URL reuse can attach identity to a manual job without identity, but cannot replace
a different attached source identity. No fuzzy cross-source merging is performed.

To add an official source, first obtain its documented API/feed contract and access
terms: authentication, query/location semantics, stable IDs/scopes, original URLs,
pagination, limits and errors. Implement the small Protocol with internal DTOs and
mocked contract tests; wire source selection explicitly in the API. Keep persistence,
matching and application logic in Discover. Verify compatibility with the current
offset-based API before adding any cursor API. The DB already scopes identity by
source, but its source allowlist must be extended through a separately approved
new migration when adding a new source key; historical migrations stay unchanged.

Staff.am awaits an official API or partner feed contract. No Staff.am endpoints,
auth, schemas or fixtures are assumed here. Scraping is not a fallback strategy.
Remote OK and Remotive are not connected. No provider registry or plugin framework
is needed for this boundary.
