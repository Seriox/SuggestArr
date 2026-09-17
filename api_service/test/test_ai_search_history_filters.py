"""Regression tests for complete watched history and real IMDb filtering."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from flask import Flask, g

from api_service.blueprints.ai_search.routes import (
    _history_users, _imdb_filter_options, ai_search_query,
)
from api_service.blueprints.integrations.routes import _jellyfin_auth_headers
from api_service.services.ai_search.ai_search_service import AiSearchService
from api_service.services.ai_search.watched_history import WatchedHistory, fetch_watched_history
from api_service.services.omdb.omdb_client import OmdbClient


def response(data, status=200):
    """Build an aiohttp-style response context manager."""
    result = MagicMock(status=status)
    result.json = AsyncMock(return_value=data)
    result.__aenter__ = AsyncMock(return_value=result)
    result.__aexit__ = AsyncMock(return_value=False)
    return result


def service():
    """Isolate tests from on-disk configuration and production services."""
    with patch.object(AiSearchService, "__init__", return_value=None):
        result = AiSearchService()
    result.config = {"SELECTED_SERVICE": "jellyfin", "FILTER_RATING_SOURCE": "tmdb"}
    return result


def movie(item_id, title="Movie", **kwargs):
    """Return a minimal candidate."""
    return {"id": item_id, "title": title, **kwargs}


def client():
    """Return a TMDb/OMDb client mock with strict default thresholds."""
    result = MagicMock(rating_source="imdb", imdb_threshold=70, imdb_min_votes=1000,
                       include_no_ratings=False)
    result._apply_filters.return_value = {"passed": True}
    result._get_item_details = AsyncMock(return_value={"imdb_id": "tt123"})
    result.omdb_client.get_rating = AsyncMock(return_value={"imdb_rating": 7.5, "imdb_votes": 1000})
    return result


@pytest.mark.asyncio
async def test_fetches_beyond_first_500_and_uses_configured_user():
    items = [{"Id": str(i), "Type": "Movie", "Name": f"Movie {i}",
              "ProviderIds": {"Tmdb": str(i)}} for i in range(1, 502)]
    session = MagicMock()
    session.get.side_effect = [response({"Items": items[:500], "TotalRecordCount": 501}),
                               response({"Items": items[500:], "TotalRecordCount": 501})]
    with patch("api_service.services.ai_search.watched_history.JellyfinClient._get_session",
               AsyncMock(return_value=session)):
        index = await fetch_watched_history({
            "SELECTED_SERVICE": "jellyfin", "JELLYFIN_API_URL": "http://media",
            "JELLYFIN_TOKEN": "test", "SELECTED_USERS": [{"id": "me"}],
        }, None)
    assert index.contains(movie(501, "Different localized title"), "movie")
    assert not index.contains(movie(501), "tv")
    assert len(session.get.call_args_list) == 2
    assert session.get.call_args_list[1].kwargs["params"]["StartIndex"] == 500
    assert session.get.call_args_list[0].args[0] == "http://media/Users/me/Items"
    assert "ParentId" not in session.get.call_args_list[0].kwargs["params"]


@pytest.mark.asyncio
async def test_episode_uses_series_ids_and_start_year_not_episode_ids():
    session = MagicMock()
    episodes = [{"Id": str(i), "Type": "Episode", "SeriesId": "series",
                 "Name": "Pilot", "ProductionYear": 2024,
                 "ProviderIds": {"Tmdb": "wrong-episode-id"}} for i in (1, 2)]
    session.get.side_effect = [response({"Items": episodes, "TotalRecordCount": 2}),
                               response({"Name": "Series", "ProductionYear": 2020,
                                         "ProviderIds": {"Tmdb": "99"}})]
    with patch("api_service.services.ai_search.watched_history.JellyfinClient._get_session",
               AsyncMock(return_value=session)):
        index = await fetch_watched_history({"SELECTED_SERVICE": "emby",
                "JELLYFIN_API_URL": "http://media", "JELLYFIN_TOKEN": "test"}, ["me"])
    assert index.contains(movie(99, "Localized series"), "tv")
    assert not index.contains(movie("wrong-episode-id"), "tv")
    assert session.get.call_count == 2  # Series lookup cached for both episodes.


@pytest.mark.asyncio
@pytest.mark.parametrize("page,status", [({}, 401), ({"Items": [], "TotalRecordCount": 8}, 200),
                                         ({"Items": []}, 200)])
async def test_history_failure_never_silently_means_no_watches(page, status):
    session = MagicMock()
    session.get.return_value = response(page, status)
    with patch("api_service.services.ai_search.watched_history.JellyfinClient._get_session",
               AsyncMock(return_value=session)), pytest.raises(ValueError):
        await fetch_watched_history({"SELECTED_SERVICE": "jellyfin",
            "JELLYFIN_API_URL": "http://media", "JELLYFIN_TOKEN": "test"}, ["me"])


def test_exact_fallback_does_not_hide_sequels_remakes_or_other_media_types():
    index = WatchedHistory([{"type": "movie", "title": "Dune", "year": 1984}])
    assert index.contains(movie(1, "DUNE", release_date="1984-01-01"), "movie")
    assert not index.contains(movie(2, "Dune", release_date="2021-01-01"), "movie")
    assert not index.contains(movie(3, "Dune: Part Two"), "movie")
    assert not index.contains(movie(1, "Dune"), "tv")
    index = WatchedHistory([{"type": "movie", "title": "Dune", "tmdb": "1"}])
    assert not index.contains(movie(2, "Dune"), "movie")


@pytest.mark.asyncio
@pytest.mark.parametrize("rating,votes,expected", [(4.5, 50000, False), (7.0, 1000, True),
    (9.0, 12, False), (None, 10000, False), (8.0, None, False)])
async def test_imdb_rating_and_votes_are_real_filters(rating, votes, expected):
    tmdb = client()
    tmdb.omdb_client.get_rating.return_value = {"imdb_rating": rating, "imdb_votes": votes}
    item = movie(1)
    allowed = await service()._filter_external_candidates([item], "movie", tmdb, None, set(), set())
    assert (1 in allowed) == expected
    if expected:
        assert item["imdb_rating"] == rating
    tmdb.omdb_client.get_rating.assert_awaited_once_with("tt123", strict=True)


@pytest.mark.asyncio
async def test_imdb_lookups_deduplicated_and_watched_items_skip_omdb():
    tmdb = client()
    index = WatchedHistory([{"type": "movie", "tmdb": "2"}])
    items = [movie(1), movie(1), movie(2)]
    assert await service()._filter_external_candidates(items, "movie", tmdb, index, set(), set()) == {1}
    tmdb.omdb_client.get_rating.assert_awaited_once()
    assert items[0]["imdb_rating"] == items[1]["imdb_rating"] == 7.5


@pytest.mark.asyncio
async def test_imdb_only_watched_id_is_matched_after_tmdb_details():
    tmdb = client()
    index = WatchedHistory([{"type": "tv", "imdb": "tt123", "title": "Other title"}])
    assert await service()._filter_external_candidates([movie(1)], "tv", tmdb, index, set(), set()) == set()
    tmdb._get_item_details.assert_awaited_once_with(1, "tv")
    tmdb.omdb_client.get_rating.assert_not_awaited()


@pytest.mark.asyncio
async def test_entire_history_loaded_once_for_both_and_not_when_exclusion_disabled():
    instance = service()
    index = WatchedHistory([])
    instance._search_single = AsyncMock(return_value={"results": []})
    with patch("api_service.services.ai_search.ai_search_service.fetch_watched_history",
               AsyncMock(return_value=index)) as fetch, patch.object(instance, "_record_seen_items"):
        await instance.search("q", "both", entire_watch_history=True, use_history=False)
        fetch.assert_awaited_once()
        assert all(c.kwargs["watched_history"] is index for c in instance._search_single.call_args_list)
        fetch.reset_mock()
        await instance.search("q", entire_watch_history=True, exclude_watched=False, use_history=False)
        fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_omdb_key_fails_before_paid_llm_call():
    with patch("api_service.services.ai_search.ai_search_service.interpret_search_query", AsyncMock()) as llm:
        with pytest.raises(ValueError, match="OMDb API key"):
            await service().search("q", imdb_filter={"min_rating": 7, "min_votes": 1000})
        llm.assert_not_awaited()


@pytest.mark.asyncio
async def test_complete_history_exclusion_is_independent_of_llm_history():
    instance = service()
    tmdb = client()
    tmdb.rating_source = "tmdb"
    tmdb.omdb_client = None
    tmdb.__aenter__ = AsyncMock(return_value=tmdb)
    tmdb.__aexit__ = AsyncMock(return_value=False)
    tmdb.search_movie = AsyncMock(return_value=[movie(1, "Old watched film")])
    index = WatchedHistory([{"type": "movie", "tmdb": "1"}])
    interpretation = {"discover_params": {}, "suggested_titles": [{"title": "Old watched film"}]}
    with patch.object(instance, "_make_tmdb_client", return_value=tmdb), \
         patch.object(instance, "_get_history", AsyncMock()) as recent, \
         patch("api_service.db.database_manager.DatabaseManager") as db, \
         patch("api_service.services.ai_search.ai_search_service.interpret_search_query",
               AsyncMock(return_value=interpretation)) as llm:
        db.return_value.get_requested_tmdb_ids.return_value = set()
        result = await instance._search_single("q", "movie", ["me"], 12,
                                               use_history=False, watched_history=index)
    assert result["results"] == []
    recent.assert_not_awaited()
    assert llm.call_args.args[1] == []


def test_linked_user_cannot_override_history_scope():
    with Flask(__name__).test_request_context(), patch(
            "api_service.blueprints.ai_search.routes.DatabaseManager") as db:
        g.current_user = {"id": "1", "role": "user"}
        db.return_value.get_user_media_profiles.return_value = [{
            "provider": "jellyfin", "external_user_id": "mine", "verified": True}]
        assert _history_users({"SELECTED_SERVICE": "jellyfin"}, ["someone-else"]) == ["mine"]
        db.return_value.get_user_media_profiles.return_value = []
        with pytest.raises(ValueError, match="Link your media account"):
            _history_users({"SELECTED_USERS": ["admin"]}, ["someone-else"])


def test_unlinked_admin_uses_selected_users_not_everyone():
    with Flask(__name__).test_request_context(), patch(
            "api_service.blueprints.ai_search.routes.DatabaseManager") as db:
        g.current_user = {"id": "1", "role": "admin"}
        db.return_value.get_user_media_profiles.return_value = []
        assert _history_users({"SELECTED_USERS": ["mine"]}, []) == ["mine"]
        with pytest.raises(ValueError, match="Select media users"):
            _history_users({}, [])


@pytest.mark.parametrize("field,value", [("imdb_min_rating", -1), ("imdb_min_rating", 11),
    ("imdb_min_rating", float("nan")), ("imdb_min_rating", True),
    ("imdb_min_votes", 0.5), ("imdb_min_votes", -1)])
def test_invalid_thresholds_rejected(field, value):
    with pytest.raises(ValueError):
        _imdb_filter_options({"filter_imdb": True, field: value})


@pytest.mark.asyncio
async def test_route_passes_checkbox_and_thresholds_to_service():
    body = {"query": "thriller", "use_history": False, "exclude_watched": False,
            "entire_watch_history": True, "filter_imdb": True,
            "imdb_min_rating": 7.2, "imdb_min_votes": 1000}
    with Flask(__name__).test_request_context(json=body), \
         patch("api_service.blueprints.ai_search.routes.get_llm_client", return_value=object()), \
         patch("api_service.blueprints.ai_search.routes.AiSearchService") as factory:
        factory.return_value.search = AsyncMock(return_value={"results": []})
        _, status = await ai_search_query.__wrapped__()
        assert status == 200
        assert factory.return_value.search.call_args.kwargs["entire_watch_history"] is True
        assert factory.return_value.search.call_args.kwargs["imdb_filter"] == {"min_rating": 7.2, "min_votes": 1000}


@pytest.mark.asyncio
async def test_omdb_quota_error_is_not_treated_as_an_unrated_movie():
    session = MagicMock()
    session.get.return_value = response({"Response": "False", "Error": "Request limit reached!"})
    with patch.object(OmdbClient, "_get_session", AsyncMock(return_value=session)):
        with pytest.raises(ValueError, match="quota"):
            await OmdbClient("fake").get_rating("tt123", strict=True)


def test_jellyfin_link_uses_supported_authorization_header():
    assert _jellyfin_auth_headers()["Authorization"].startswith('MediaBrowser Client="SuggestArr"')
