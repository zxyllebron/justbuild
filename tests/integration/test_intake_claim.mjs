// In-memory tests only: no HTTP, browser, credentials, printer, or disk writes.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
const filename = new URL('../../src/justbuild/web/intake-claim.js', import.meta.url);
const source = await readFile(filename, 'utf8');
const { claimLocalTask } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const token = 'a1'.repeat(32), selected = 'b2'.repeat(16);
function fixture(hash, overrides = {}) {
  const events = [], notices = [];
  const options = {
    location: { hash, pathname: '/review', search: `?job=${selected}&mode=review` }, selected,
    history: { replaceState(...args) { events.push(['replace', ...args]); } },
    api: async (...args) => { events.push(['api', ...args]); return { claimed: true }; },
    toast: message => { notices.push(message); }, ...overrides,
  };
  return { events, notices, options, run: () => claimLocalTask(options) };
}
let tests = 0;
async function check(name, run) { await run(); tests++; }
await check('valid claim clears the complete fragment before one exact POST', async () => {
  const f = fixture(`#display=transparent&claim=${token}&extra=remove-me`);
  assert.equal(await f.run(), true);
  assert.equal(f.events.length, 2);
  assert.deepEqual(f.events[0], ['replace', null, '', `/review?job=${selected}&mode=review`]);
  assert.deepEqual(f.events[1], ['api', `/api/jobs/${selected}/claim`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Justbuild-Intent': 'intake-claim' },
    body: JSON.stringify({ token }),
  }]);
  assert.deepEqual(f.notices, []);
  assert(!JSON.stringify(f.events[0]).includes(token));
});
await check('missing claim leaves history untouched and sends nothing', async () => {
  for (const hash of ['', '#', '#display=transparent', '#Claim=' + token]) {
    const f = fixture(hash); assert.equal(await f.run(), false); assert.deepEqual(f.events, []); assert.deepEqual(f.notices, []);
  }
});
await check('invalid token or task still removes fragment and never posts', async () => {
  for (const bad of ['', 'secret', token.toUpperCase(), token + 'a', token.slice(1), token + '%0A']) {
    const f = fixture(`#claim=${bad}&keep=none`); assert.equal(await f.run(), false);
    assert.equal(f.events.length, 1); assert.equal(f.events[0][0], 'replace'); assert.equal(f.notices.length, 1);
    assert(!f.notices.join('').includes(bad || token));
  }
  for (const bad of ['', selected.toUpperCase(), selected.slice(1), selected + '/', null]) {
    const f = fixture(`#claim=${token}`, { selected: bad }); assert.equal(await f.run(), false);
    assert.equal(f.events.length, 1); assert.equal(f.events[0][0], 'replace'); assert.equal(f.notices.length, 1);
  }
});
await check('duplicate claim parameters are ambiguous and never transmitted', async () => {
  const f = fixture(`#claim=${token}&claim=${token}`); assert.equal(await f.run(), false);
  assert.equal(f.events.length, 1); assert.equal(f.notices.length, 1);
});
await check('URLSearchParams decoding cannot bypass clear-before-request', async () => {
  const f = fixture(`#%63laim=${token}`); assert.equal(await f.run(), true);
  assert.equal(f.events[0][0], 'replace'); assert.equal(f.events[1][0], 'api');
});
await check('API failures have one attempt and one fixed redacted message', async () => {
  const f = fixture(`#claim=${token}`);
  f.options.api = async (...args) => { f.events.push(['api', ...args]); throw Error(`refused ${token}`); };
  assert.equal(await f.run(), false); assert.equal(f.events.filter(x => x[0] === 'api').length, 1);
  assert.equal(f.events[0][0], 'replace'); assert.deepEqual(f.notices, ['任务链接无法接管，请重新获取链接。']);
  assert(!f.notices.join('').includes(token));
});
await check('failed sanitization prevents all network transmission and is safe', async () => {
  const f = fixture(`#claim=${token}`); f.options.history.replaceState = () => { throw Error(token); };
  assert.equal(await f.run(), false); assert.deepEqual(f.events, []); assert.equal(f.notices.length, 1);
  assert(!f.notices[0].includes(token));
  f.options.toast = () => { throw Error('display unavailable'); };
  assert.equal(await f.run(), false);
});
process.stdout.write(JSON.stringify({ tests, passed: true }) + '\n');
