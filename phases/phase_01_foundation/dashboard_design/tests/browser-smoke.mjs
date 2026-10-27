/** Optional browser QA. Uses a separately installed playwright-core, not an app dependency. */
import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
const modulePath = process.env.PHARMAX_PLAYWRIGHT_MODULE;
assert.ok(modulePath, 'Set PHARMAX_PLAYWRIGHT_MODULE to a separately installed playwright-core/index.mjs');
const { chromium } = await import(pathToFileURL(resolve(modulePath)).href);
const origin = process.argv[2] || 'http://127.0.0.1:5174';
const output = resolve(process.argv[3] || '.');
const browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--enable-unsafe-swiftshader', '--use-angle=swiftshader', '--disable-dev-shm-usage'] });
const result = { checked_at: new Date().toISOString(), origin, checks: [], api_requests: [], blocked_external_origins: [], page_errors: [], inference_requests: 0 };
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, colorScheme: 'dark' });
  page.on('pageerror', error => result.page_errors.push(error.message));
  await page.route('**/*', route => {
    const request = route.request();
    if (!request.url().startsWith(origin + '/')) {
      result.blocked_external_origins.push(new URL(request.url()).origin); return route.abort();
    }
    if (request.url().includes('/api/')) {
      result.api_requests.push({ method: request.method(), path: new URL(request.url()).pathname });
      if (request.method() !== 'GET') return route.abort();
    }
    return route.continue();
  });
  await page.goto(origin, { waitUntil: 'networkidle', timeout: 120000 });
  await page.locator('.globe-stage canvas').first().waitFor({ state: 'visible', timeout: 60000 });
  const canvas = await page.locator('.globe-stage canvas').first().elementHandle();
  await page.screenshot({ path: resolve(output, 'overview-dark.png'), fullPage: true });
  await page.locator('.alerts-overlay .alert-risk').first().click();
  await page.getByRole('heading', { name: 'Red Sea disruption', exact: true }).waitFor();
  assert.equal(await page.evaluate(el => el.isConnected, canvas), true);
  await page.getByRole('button', { name: 'Review', exact: true }).click();
  await page.getByRole('button', { name: 'Approve locally', exact: true }).click();
  await page.getByRole('button', { name: 'Confirm locally', exact: true }).click();
  await page.getByRole('status').filter({ hasText: 'No route or shipment was changed' }).waitFor();
  result.checks.push('Actual WebGL globe renders; original risk view, local review/confirmation and persistent canvas work');
  await page.getByRole('button', { name: 'Full map', exact: true }).click();
  const listing = await (await page.request.get(origin + '/api/v1/investigations?limit=20')).json();
  const original = listing.items.find(c => c.scenario === 'red-sea' && !c.parent_id);
  const revised = listing.items.find(c => c.parent_id === original?.investigation_id);
  assert.ok(original && revised);
  await page.getByRole('button', { name: 'Investigations', exact: true }).click();
  await page.getByText('Backend connected · Saved cases', { exact: true }).waitFor();
  await page.getByRole('button', { name: new RegExp(original.investigation_id) }).click();
  await page.getByRole('table').waitFor();
  const originalRow = page.getByRole('row').filter({ hasText: /Central/ }).filter({ hasText: /disruption/ });
  assert.match(await originalRow.innerText(), /24/);
  assert.equal(await page.evaluate(el => el.isConnected, canvas), true);
  await page.screenshot({ path: resolve(output, 'investigation-desktop.png'), fullPage: true });
  await page.getByRole('button', { name: 'Back to investigations', exact: true }).click();
  await page.getByRole('button', { name: new RegExp(revised.investigation_id) }).click();
  await page.getByRole('table').waitFor();
  assert.match(await page.getByRole('row').filter({ hasText: /Central/ }).filter({ hasText: /disruption/ }).innerText(), /30/);
  await page.getByText('Case revision history', { exact: true }).click();
  await page.getByRole('button', { name: 'Open parent investigation', exact: true }).click();
  await page.getByRole('table').waitFor();
  assert.match(await page.getByRole('row').filter({ hasText: /Central/ }).filter({ hasText: /disruption/ }).innerText(), /24/);
  result.checks.push('Actual browser -> Vite -> operator API: original 24, revision 30, parent 24; IDs remain consistent');
  await page.getByRole('button', { name: 'Close panel', exact: true }).click();
  await page.getByRole('button', { name: 'Switch to light mode', exact: true }).click();
  assert.equal(await page.evaluate(() => localStorage.getItem('pharmax-theme')), 'light');
  await page.reload({ waitUntil: 'networkidle' });
  await page.getByRole('button', { name: 'Switch to dark mode', exact: true }).waitFor();
  await page.locator('.globe-stage canvas').first().waitFor({ state: 'visible' });
  await page.screenshot({ path: resolve(output, 'overview-light.png'), fullPage: true });
  result.checks.push('Light/dark theme control and persisted light preference work');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: 'Investigations', exact: true }).click();
  await page.getByText('Backend connected · Saved cases', { exact: true }).waitFor();
  await page.getByRole('button', { name: new RegExp(original.investigation_id) }).click();
  await page.getByRole('table').waitFor();
  const popup = await page.getByRole('dialog').boundingBox();
  assert.ok(popup && popup.x >= 0 && popup.x + popup.width <= 391);
  await page.screenshot({ path: resolve(output, 'investigation-mobile.png'), fullPage: true });
  const colors = await page.locator('.operator-cases').evaluate(el => ({
    theme: document.documentElement.dataset.theme,
    description: getComputedStyle(el.querySelector('.detail-copy')).color,
    value: getComputedStyle(el.querySelector('dd')).color,
    button: getComputedStyle(el.querySelector('.secondary-button')).color,
  }));
  assert.equal(colors.theme, 'light');
  assert.ok([colors.description, colors.value, colors.button].every(value =>
    Math.max(...value.match(/\d+/g).slice(0, 3).map(Number)) <= 130), 'Light-mode drawer text must remain readable');
  result.final_scoped_text_colors = colors;
  result.checks.push('390px mobile drawer fits the viewport, fetches the same saved case and has readable light-theme text');
  await page.getByRole('button', { name: 'Close panel', exact: true }).click();
  await page.route('**/api/v1/**', route => route.fulfill({ status: 503, contentType: 'application/json', body: '{"error":"QA_SERVICE_UNAVAILABLE"}' }));
  await page.getByRole('button', { name: 'Investigations', exact: true }).click();
  await page.getByRole('alert').filter({ hasText: 'Operator API unavailable' }).waitFor();
  assert.equal(await page.getByRole('table').count(), 0);
  result.checks.push('Browser-intercepted 503 shows an error, with no invented case/forecast fallback');
  assert.deepEqual(result.page_errors, []);
  assert.ok(result.api_requests.length >= 10 && result.api_requests.every(r => r.method === 'GET'));
  result.status = 'passed';
} finally {
  result.blocked_external_origins = [...new Set(result.blocked_external_origins)];
  await writeFile(resolve(output, 'browser-smoke.json'), JSON.stringify(result, null, 2) + '\n');
  await browser.close();
}
console.log('PASS: real browser globe, original interactions, API drawer, revision navigation, themes, mobile and offline states.');
