"""Complete, user-scoped Jellyfin/Emby watched-history exclusion.

History stays on the server: only the separate, bounded taste sample is sent
to the LLM. An episode counts as having watched its series. Deleted library
items cannot be recovered from Jellyfin's current played-item list.
"""

import unicodedata

from api_service.services.jellyfin.jellyfin_client import JellyfinClient


def normalized_title(title):
    """Normalize case and whitespace without conflating sequels or remakes."""
    return " ".join(unicodedata.normalize("NFKC", title or "").casefold().split())


class WatchedHistory:
    """Index watched titles by media type and provider ID, with title fallback."""

    def __init__(self, records):
        """Index normalized records returned by the media server."""
        self.ids = set()
        self.titles = {}
        self.has_imdb_ids = False
        for record in records:
            media_type = record["type"]
            for provider in ("tmdb", "imdb"):
                value = record.get(provider)
                if value:
                    self.ids.add((media_type, provider, str(value).lower()))
                    self.has_imdb_ids |= provider == "imdb"
            key = (media_type, normalized_title(record.get("title")))
            if key[1]:
                self.titles.setdefault(key, []).append(record)

    def contains(self, item, media_type):
        """Match IDs first; only use exact titles when no shared IDs exist."""
        candidate_ids = {"tmdb": str(item["id"]), "imdb": item.get("imdb_id")}
        for provider, value in candidate_ids.items():
            if value and (media_type, provider, str(value).lower()) in self.ids:
                return True
        year = str(item.get("release_date") or item.get("first_air_date") or "")[:4]
        titles = (item.get("title"), item.get("name"), item.get("original_title"),
                  item.get("original_name"))
        for title in titles:
            for record in self.titles.get((media_type, normalized_title(title)), []):
                if any(record.get(p) and candidate_ids.get(p) for p in candidate_ids):
                    continue  # Comparable but different IDs: do not hide a remake.
                if not year or not record.get("year") or year == str(record["year"]):
                    return True
        return False


async def fetch_watched_history(config, user_ids):
    """Fetch every played item, failing explicitly on incomplete history."""
    if config.get("SELECTED_SERVICE", "").lower() not in ("jellyfin", "emby"):
        raise ValueError("Use entire watch history currently requires Jellyfin or Emby.")
    users = user_ids or config.get("SELECTED_USERS") or []
    if not users:
        raise ValueError("Link your media account or select media users in Services first.")
    url, token = config.get("JELLYFIN_API_URL"), config.get("JELLYFIN_TOKEN")
    if not url or not token:
        raise ValueError("Configure the Jellyfin/Emby connection in Services first.")

    records = []
    series_cache = {}
    # No ParentId restriction: include all libraries accessible to each user.
    async with JellyfinClient(url, token) as client:
        session = await client._get_session()

        async def get_json(path, params=None):
            """Never silently interpret a failed history request as empty history."""
            async with session.get(
                f"{client.api_url}/{path}", params=params, headers=client.headers,
                timeout=30,
            ) as response:
                if response.status != 200:
                    raise ValueError("Could not read entire watch history; check the media connection.")
                return await response.json()

        for user in users:
            user_id = user.get("id") if isinstance(user, dict) else user
            if not user_id:
                raise ValueError("Invalid media user selected.")
            start = 0
            seen_items = set()
            while True:
                page = await get_json(f"Users/{user_id}/Items", {
                    "IsPlayed": "true", "Recursive": "true",
                    "IncludeItemTypes": "Movie,Episode,Series",
                    "Fields": "ProviderIds,SeriesProviderIds",
                    "SortBy": "SortName", "SortOrder": "Ascending",
                    "StartIndex": start, "Limit": 500, "EnableTotalRecordCount": "true",
                })
                items = page.get("Items")
                total = page.get("TotalRecordCount")
                if not isinstance(items, list) or not isinstance(total, int):
                    raise ValueError("Incomplete watch-history response from the media server.")
                if not items and start < total:
                    raise ValueError("Watch-history pagination ended early; please retry.")
                for item in items:
                    item_id = item.get("Id")
                    if not item_id or item_id in seen_items:
                        raise ValueError("Watch-history pagination changed; please retry.")
                    seen_items.add(item_id)
                    episode = item.get("Type") == "Episode"
                    metadata = item
                    providers = item.get("ProviderIds") or {}
                    if episode:
                        series_id = item.get("SeriesId")
                        providers = item.get("SeriesProviderIds") or {}
                        if series_id:
                            cache_key = (str(user_id), series_id)
                            if cache_key not in series_cache:
                                series_cache[cache_key] = await get_json(
                                    f"Users/{user_id}/Items/{series_id}"
                                )
                            metadata = series_cache[cache_key]
                            providers = metadata.get("ProviderIds") or providers
                        else:
                            metadata = {"Name": item.get("SeriesName")}
                    providers = {k.lower(): v for k, v in providers.items()}
                    records.append({
                        "type": "movie" if item.get("Type") == "Movie" else "tv",
                        "title": metadata.get("Name"), "year": metadata.get("ProductionYear"),
                        "tmdb": providers.get("tmdb"), "imdb": providers.get("imdb"),
                    })
                start += len(items)
                if start >= total:
                    break
    return WatchedHistory(records)
