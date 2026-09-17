import test from 'node:test';
import assert from 'node:assert/strict';
import axios from 'axios';
import { aiSearch, listTraktJobUsers } from './api.js';

test('AI Search sends complete-history and IMDb options independently of personalization', async () => {
  const originalAdapter = axios.defaults.adapter;
  let payload;
  axios.defaults.adapter = async (config) => {
    payload = JSON.parse(config.data);
    return { data: { results: [] }, status: 200, statusText: 'OK', headers: {}, config };
  };
  try {
    await aiSearch('thriller', 'both', [], 12, false, true, false, {
      entireWatchHistory: true, filterImdb: true, imdbMinRating: 7.2, imdbMinVotes: 1000,
    });
    assert.equal(payload.use_history, false);
    assert.equal(payload.exclude_watched, true);
    assert.equal(payload.entire_watch_history, true);
    assert.equal(payload.filter_imdb, true);
    assert.equal(payload.imdb_min_rating, 7.2);
    assert.equal(payload.imdb_min_votes, 1000);
    await aiSearch('thriller');
    assert.equal(payload.entire_watch_history, false);
    assert.equal(payload.filter_imdb, false);
  } finally {
    axios.defaults.adapter = originalAdapter;
  }
});

test('Trakt job users are scoped by role', async () => {
  const originalAdapter = axios.defaults.adapter;
  const requested = [];
  axios.defaults.adapter = async (config) => {
    requested.push(config.url);
    const mediaUser = { external_user_id: 'mine', trakt: { connected: true } };
    return {
      data: config.url === '/api/trakt/me' ? { media_user: mediaUser } : { media_users: [mediaUser] },
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  };

  try {
    assert.deepEqual(await listTraktJobUsers('user'), [
      { external_user_id: 'mine', trakt: { connected: true } },
    ]);
    assert.equal((await listTraktJobUsers('admin')).length, 1);
    assert.deepEqual(requested, ['/api/trakt/me', '/api/trakt/media-users']);
  } finally {
    axios.defaults.adapter = originalAdapter;
  }
});
