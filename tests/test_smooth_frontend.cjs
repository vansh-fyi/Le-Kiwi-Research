// Exercises the shipped page's event handlers against a running simulation.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { performance } = require('node:perf_hooks');

async function main() {
  const url = new URL(process.argv[2]);
  assert(['localhost', '127.0.0.1'].includes(url.hostname));
  const token = new URLSearchParams(url.hash.slice(1)).get('token');
  const status = async () => {
    const response = await fetch(new URL('/status', url), { headers: { 'X-Control-Token': token } });
    assert(response.ok);
    return response.json();
  };
  assert.equal((await status()).telemetry.dry_run, true, 'This test only operates a simulation');
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      value: id === 'mode' ? 'both' : id === 'profile' ? '1' : '',
      textContent: '', disabled: false, tagName: 'DIV', listeners: {},
      classList: { toggle() {} },
      addEventListener(event, callback) { this.listeners[event] = callback; },
    });
    return elements.get(id);
  }
  const windows = {}, documents = {}, intervals = [];
  const document = {
    hidden: false, getElementById: element, querySelectorAll: () => [],
    addEventListener(event, callback) { documents[event] = callback; },
  };
  const context = vm.createContext({
    location: { hash: url.hash }, document, URLSearchParams, AbortSignal, performance,
    window: { addEventListener(event, callback) { windows[event] = callback; } },
    setInterval(callback) { intervals.push(callback); },
    fetch(path, options) { return fetch(new URL(path, url), options); },
  });
  const html = fs.readFileSync('scripts/smooth_control.html', 'utf8');
  vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1], context);
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const heartbeat = async () => { await intervals[0](); await sleep(50); };
  const event = (key, code = '') => ({ key, code, target: { tagName: 'DIV' }, preventDefault() {} });
  async function enable() {
    await element('enable').listeners.click();
    for (let i = 0; i < 10; i++) {
      await heartbeat();
      if ((await status()).ready) { await heartbeat(); return; }
    }
    assert.fail('Simulation did not become ready');
  }
  // Initial status fetch: force the simulated interval beyond the poll debounce.
  await sleep(510);
  await heartbeat();
  assert.equal(element('enable').disabled, false);
  await enable();
  const before = (await status()).telemetry.positions.arm_shoulder_pan;
  windows.keydown(event('w', 'KeyW'));
  windows.keydown(event('r', 'KeyR'));
  for (let i = 0; i < 24; i++) await heartbeat();
  const held = await status();
  assert.deepEqual(held.keys.sort(), ['r', 'w']);
  assert(held.telemetry.base[0] > 0);
  assert(held.telemetry.positions.arm_shoulder_pan > before);
  windows.keyup(event('w', 'KeyW'));
  windows.keyup(event('r', 'KeyR'));
  await heartbeat();
  assert.deepEqual((await status()).keys, []);
  windows.keydown(event(' ', 'Space'));
  await sleep(80);
  assert.equal((await status()).enabled, false);
  await enable();
  windows.keydown(event('w', 'KeyW'));
  await heartbeat();
  windows.blur();
  await sleep(80);
  assert.equal((await status()).enabled, false);
  await enable();
  windows.keydown({...event('Meta', 'MetaLeft'), metaKey: true});
  await sleep(80);
  assert.equal((await status()).enabled, false);
  await enable();
  document.hidden = true;
  documents.visibilitychange();
  await sleep(80);
  assert.equal((await status()).enabled, false);
  console.log('Frontend integration passed: combined hold, key release, Space stop, focus loss, modifier shortcut, hidden tab.');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
