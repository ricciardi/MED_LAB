import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

// Exercise the actual worker message handler with only its heavy dependencies
// substituted. No model download or GPU is required for this policy regression.
const source = (await readFile(new URL('./kokoro-worker.js', import.meta.url), 'utf8'))
  .replace(/^import .*;\n/gmu, '')
  .replaceAll('import.meta.url', JSON.stringify('https://example.invalid/voice-local/kokoro-worker.js'));

for (const type of ['load', 'synthesize']) {
  for (const requestedDevice of [undefined, 'auto', 'webgpu', 'wasm']) {
    test(`${type} uses WASM even when device=${String(requestedDevice)} and a GPU is available`, async () => {
      const configurations = [], messages = [];
      let adapterRequests = 0;
      const scope = {
        self: {}, URL, AbortController, Float32Array,
        navigator: { gpu: { requestAdapter: async () => { adapterRequests++; return {}; } } },
        createPhonemizer: () => {},
        LocalVoiceError: class extends Error {},
        createKokoroPt: async options => {
          configurations.push(options);
          return { device: options.device, synthesize: async () => ({
            samples: new Float32Array([0.1]), sampleRate: 24000, device: options.device,
          }) };
        },
        postMessage: message => messages.push(message),
      };
      vm.runInNewContext(source, scope);
      scope.self.onmessage({ data: { type, id: 1, device: requestedDevice, text: 'Bom dia, doutora.' } });
      await new Promise(setImmediate);
      assert.equal(configurations.length, 1);
      assert.equal(configurations[0].device, 'wasm');
      assert.equal(adapterRequests, 0);
      assert.equal(messages.at(-1).type, type === 'load' ? 'ready' : 'result');
      assert.equal(messages.at(-1).device, 'wasm');
    });
  }
}

for (const [name, isolated, shared, cores, expected] of [
  ['not isolated', false, true, 10, 1],
  ['SharedArrayBuffer unavailable', true, false, 10, 1],
  ['unknown hardware', true, true, undefined, 1],
  ['one core', true, true, 1, 1],
  ['two cores', true, true, 2, 2],
  ['three cores', true, true, 3, 2],
  ['four cores', true, true, 4, 4],
  ['many cores capped', true, true, 32, 4],
]) {
  test(`thread selection: ${name}`, () => {
    const scope = { self: {}, URL, AbortController, Float32Array,
      crossOriginIsolated: isolated, SharedArrayBuffer: shared ? SharedArrayBuffer : undefined,
      navigator: { hardwareConcurrency: cores },
    };
    vm.runInNewContext(source + '; globalThis.observedThreads = chooseWasmThreads();', scope);
    assert.equal(scope.observedThreads, expected);
  });
}

test('a threaded load failure retries once in single-thread mode and reports its effective configuration', async () => {
  const messages = []; let attempts = 0;
  const scope = { self: {}, URL, AbortController, Float32Array,
    crossOriginIsolated: true, SharedArrayBuffer, navigator: { hardwareConcurrency: 10 },
    createPhonemizer: () => {}, LocalVoiceError: class extends Error {},
    createKokoroPt: async () => { attempts++; if (attempts === 1) throw new Error('thread initialization failed'); return { device: 'wasm' }; },
    postMessage: message => messages.push(message),
  };
  vm.runInNewContext(source, scope);
  scope.self.onmessage({ data: { type: 'load', id: 'load' } });
  await new Promise(setImmediate);
  assert.equal(attempts, 2);
  assert.equal(messages.at(-1).type, 'ready');
  assert.equal(messages.at(-1).threads, 1);
  assert.equal(messages.filter(x => x.stage === 'thread_fallback').length, 1);
  scope.self.onmessage({ data: { type: 'load', id: 'reuse' } });
  await new Promise(setImmediate);
  assert.equal(attempts, 2);
});

test('both initialization failures produce one safe error without an unbounded retry loop', async () => {
  const messages = []; let attempts = 0;
  const scope = { self: {}, URL, AbortController, Float32Array,
    crossOriginIsolated: true, SharedArrayBuffer, navigator: { hardwareConcurrency: 10 },
    createPhonemizer: () => {}, LocalVoiceError: class extends Error {},
    createKokoroPt: async () => { attempts++; throw new Error('private runtime details'); },
    postMessage: message => messages.push(message),
  };
  vm.runInNewContext(source, scope);
  scope.self.onmessage({ data: { type: 'load', id: 'load' } });
  await new Promise(setImmediate);
  assert.equal(attempts, 2);
  assert.equal(messages.at(-1).type, 'error');
  assert.equal(messages.at(-1).code, 'TTS_UNAVAILABLE');
  assert.ok(!messages.at(-1).message.includes('private runtime details'));
});
