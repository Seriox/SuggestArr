# AI Search: complete history and IMDb filters

## Watch history

In **AI Search → Advanced Options**, enable **Exclude already watched** and
**Use entire watch history** (checked by default). This fetches every currently
played movie, series and episode from Jellyfin/Emby, with pagination, across
all libraries accessible to the selected media user. Watching any episode
excludes that series. Items already removed from Jellyfin cannot be recovered
from its played-item list. Trakt/Plex full-history aggregation is not implemented;
disable this option for those providers.

The verified account linked under **My Profile** takes precedence. An unlinked
administrator uses the explicitly selected users in **Services**, not all server
users. Other users must link their own account. Jellyfin account linking now
uses the current `Authorization` header, including on Jellyfin 12.

Matching uses TMDb/IMDb IDs and media type. Exact normalized title and year
matching is only a fallback when comparable IDs are absent; substring matching
does not exclude sequels. Incomplete/failed full-history reads fail the search
instead of silently pretending no content was watched.

**Use viewing history** is independent: it sends the existing small recent-history
sample to the LLM for personalization. The complete list stays on the SuggestArr
server and is never added to the AI prompt. Turn personalization off to send no
viewing history to the LLM while retaining complete watched-title exclusion.

## IMDb ratings

Configure an **OMDb API key** in Services. This is separate from the OpenAI key;
OMDb supplies actual IMDb ratings (not TMDb ratings or AI guesses).

Enable **Filter by IMDb rating** and set:

- **Minimum IMDb rating (0–10)**, initially 7.0.
- **Minimum IMDb votes**, initially 1,000.

The per-search filter overrides the global rating source for that search, retains
other configured filters and excludes missing ratings. If minimum votes is above
zero, missing vote counts also fail the filter. With the checkbox off, global
rating-source settings still apply, now also to direct AI suggestions.

Returned ratings are explicitly labelled IMDb or TMDb. A missing key, connection
error or exhausted OMDb quota produces an actionable error, not an unfiltered
result. Lookups are deduplicated within each media-type search and limited to four
concurrent candidates. There is no additional LLM call for rating verification.
OMDb's request quota still applies. Tight filters may return fewer results than
requested; rejected results are not replaced with unverified suggestions.

Checkboxes and thresholds persist in the browser. Existing API clients remain
compatible; new request fields are `entire_watch_history`, `filter_imdb`,
`imdb_min_rating`, and `imdb_min_votes`. Complete history is opt-in at the API
level. IMDb thresholds are validated before paid AI calls.

## Verification

Backend regression tests: `python -m pytest api_service/test/test_ai_search_history_filters.py`.
The tests use mocked media/AI/rating providers and never spend OpenAI credit.
Frontend request serialization is covered in `client/src/api/api.test.js`.
No database migration or Kubernetes change is required.
