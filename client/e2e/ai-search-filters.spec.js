import { expect, test } from '@playwright/test';

test('AI Search checkboxes persist, submit independently, and label IMDb results', async ({ page }) => {
  // All providers are mocked. This test neither calls OpenAI nor accesses real history.
  const token = 'eyJhbGciOiJub25lIn0.eyJzdWIiOiIxIiwidXNlcm5hbWUiOiJhZG1pbiIsInJvbGUiOiJhZG1pbiJ9.';
  let searchBody;
  await page.addInitScript(() => localStorage.setItem('suggestarr_tour_done', '1'));
  await page.route('http://localhost:5000/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body = {};
    if (path === '/api/auth/status') body = { auth_setup_complete: true, app_setup_complete: true };
    if (path === '/api/auth/login' || path === '/api/auth/refresh') body = { access_token: token };
    if (path === '/api/auth/me') body = { id: 1, username: 'admin', role: 'admin' };
    if (path === '/api/config/fetch') body = { AUTH_MODE: 'enabled' };
    if (path === '/api/config/status') body = { setup_completed: true, is_complete: true };
    if (path === '/api/ai-search/status') body = { available: true };
    if (path === '/api/ai-search/feedback') body = { feedback: [] };
    if (path === '/api/ai-search/query') {
      searchBody = route.request().postDataJSON();
      body = { status: 'success', results: [{ id: 1, title: 'Verified movie', media_type: 'movie',
        rating: 6.5, imdb_rating: 7.8, imdb_votes: 5000, release_date: '2020-01-01' }], total: 1 };
    }
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(body), headers: {
      'access-control-allow-origin': 'http://127.0.0.1:5173',
      'access-control-allow-credentials': 'true',
    } });
  });
  await page.goto('/login');
  await page.getByLabel('Username').fill('admin');
  await page.getByLabel('Password').fill('test-password');
  await page.getByRole('button', { name: 'Sign In' }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
  await page.getByRole('button', { name: /AI Search/ }).click();
  await page.getByRole('button', { name: 'Advanced Options' }).click();
  const history = page.getByRole('checkbox', { name: /Use entire watch history/ });
  await expect(history).toBeChecked();
  await history.uncheck();
  await history.check();
  await page.getByRole('checkbox', { name: /Filter by IMDb rating/ }).check();
  await page.getByLabel('Minimum IMDb rating (0–10)').fill('7.2');
  await page.getByLabel('Minimum IMDb votes').fill('1000');
  await page.locator('.toggle-option').filter({ hasText: 'Use viewing history' }).locator('.toggle-switch').click();
  await page.getByPlaceholder(/Describe what you want to watch/).fill('A thriller');
  await page.locator('.search-controls').getByRole('button', { name: /Search/ }).click();
  await expect(page.getByText('Verified movie', { exact: true })).toBeVisible();
  expect(searchBody).toMatchObject({ entire_watch_history: true, use_history: false,
    exclude_watched: true, filter_imdb: true, imdb_min_rating: 7.2, imdb_min_votes: 1000 });
  await expect(page.getByText(/7\.8 IMDb/)).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem('suggestarr_ai_imdb_rating'))).toBe('7.2');
  await page.reload();
  await page.getByRole('button', { name: /AI Search/ }).click();
  await page.getByRole('button', { name: 'Advanced Options' }).click();
  await expect(page.getByRole('checkbox', { name: /Filter by IMDb rating/ })).toBeChecked();
  await expect(page.getByLabel('Minimum IMDb rating (0–10)')).toHaveValue('7.2');
});
