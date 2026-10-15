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
