import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { speechChunks, kokoroPhonemes, createKokoroPt, createKokoroModel, selectKokoroDevice, LocalVoiceError } from './kokoro-pt.js';

test('fragmentation preserves every word, punctuation and Brazilian decimal without silent truncation', () => {
  const text = ('Bom dia, doutora. Ontem medi 37,5 graus e fiquei preocupado. A dor vai e volta quando eu caminho. ').repeat(20).trim();
  const parts = speechChunks(text);
  assert.equal(parts.join(' '), text);
  assert.ok(parts.length > 1);
  assert.ok(parts.every(part => part.length <= 220));
});

test('natural opening clauses start earlier and neighboring chunks preserve the entire response', () => {
  const first = 'Estou com dor na barriga desde ontem.';
  const text = `${first} Ela vai e volta, piora quando ando e melhora um pouco quando fico quieta. Não tive febre nem vomitei.`;
  const chunks = speechChunks(text);
  assert.equal(chunks[0], `${first} Ela vai e volta,`);
  assert.equal(chunks.join(' '), text);
  assert.ok(chunks.every(chunk => chunk.length <= 90));
  const long = 'A dor começou há três dias, bem aqui no peito, e parece um aperto que aparece quando caminho mais rápido. Quando paro e descanso, ela diminui.';
  const bounded = speechChunks(long);
  assert.ok(bounded[0].length <= 90);
  assert.equal(bounded.join(' '), long);
  assert.ok(!bounded[0].endsWith('rápi'), 'a word must never be split');
});

test('a very short greeting is not emitted as a tiny separate audio block', () => {
  const chunks = speechChunks('Olá. A dor começou ontem à tarde e vai e volta. Não tive febre.');
  assert.ok(chunks[0].startsWith('Olá. A dor começou ontem à tarde e vai e volta.'));
  assert.equal(chunks.join(' '), 'Olá. A dor começou ontem à tarde e vai e volta. Não tive febre.');
  assert.equal(speechChunks('Não tive febre nem vômitos.').length, 1);
});

test('invalid or oversized speech is rejected instead of spoken partially', () => {
  for (const text of ['', ' ', null, 'x'.repeat(4001), 'x'.repeat(221), 'fala\0inválida']) {
    assert.throws(() => speechChunks(text), error => error instanceof LocalVoiceError && error.code === 'INVALID_TEXT');
  }
});

test('Portuguese IPA affricates and nasal vowels retain Kokoro vocabulary', () => {
  assert.equal(kokoroPhonemes(['d', 'ʒ', 'i', ' ', 't', 'ʃ', 'i', ' ', 'ɐ', '\u0303']), 'ʤi ʧi ɐ̃');
  assert.equal(kokoroPhonemes(['a', 'ɪ', '.', 'o', 'ʊ']), 'I. O');
  assert.throws(() => kokoroPhonemes(undefined), LocalVoiceError);
});

test('corrupt voice assets fail before inference and receive only fixed asset URLs', async () => {
  const requested = [];
  let inferenceCalls = 0;
  const model = () => { inferenceCalls++; };
  const transformers = {
    StyleTextToSpeech2Model: { from_pretrained: async () => model },
    AutoTokenizer: { from_pretrained: async () => () => {} },
    Tensor: class {},
  };
  await assert.rejects(createKokoroPt({ transformers, createPhonemizer: async () => ({ callMain() {} }),
    phonemizerBaseUrl: 'https://example.invalid/espeak/',
    fetcher: async url => { requested.push(url); return new Response(new Uint8Array(8)); },
  }), error => error instanceof LocalVoiceError && error.code === 'VOICE_INTEGRITY_FAILED');
  assert.equal(inferenceCalls, 0);
  assert.equal(requested.length, 3);
  assert.ok(requested.every(url => /^https:\/\/huggingface\.co\/onnx-community\/Kokoro-82M-v1\.0-ONNX\/resolve\/[a-f0-9]{40}\/voices\/p[fm]_[a-z]+\.bin$/u.test(url)));
});

test('GPU selection requires a usable adapter and safely handles denied GPU access', async () => {
  assert.equal(await selectKokoroDevice({}), 'wasm');
  assert.equal(await selectKokoroDevice({ gpu: { requestAdapter: async () => null } }), 'wasm');
  assert.equal(await selectKokoroDevice({ gpu: { requestAdapter: async () => { throw new Error('denied'); } } }), 'wasm');
  assert.equal(await selectKokoroDevice({ gpu: { requestAdapter: async () => ({ isFallbackAdapter: true }) } }), 'wasm');
  assert.equal(await selectKokoroDevice({ gpu: { requestAdapter: async () => ({}) } }), 'webgpu');
});

const ids = { data: new BigInt64Array([0n, 1n, 2n, 0n]), dims: [1, 4] };
const style = new Float32Array(256);
function fakeRuntime({ load, run, dispose = () => {} } = {}) {
  class Tensor { constructor(type, data, dims) { Object.assign(this, { type, data, dims }); } }
  return {
    Tensor,
    AutoTokenizer: { from_pretrained: async () => () => ({ input_ids: ids }) },
    StyleTextToSpeech2Model: { from_pretrained: async (_name, options) => {
      await load?.(options);
      const model = async feeds => {
        assert.ok(feeds.input_ids instanceof Tensor, 'tensors must belong to the current runtime');
        await run?.(feeds);
        return { waveform: { data: new Float32Array([0.1, -0.1, 0]) } };
      };
      model.dispose = dispose;
      return model;
    } },
  };
}

test('GPU load rejection falls back once to an isolated WASM runtime with the same q8 model', async () => {
  const requests = [];
  const model = await createKokoroModel({ device: 'webgpu', loadTransformers: async (device, flags) => {
    requests.push([device, flags.fresh]);
    return fakeRuntime({ load: options => {
      assert.equal(options.dtype, 'q8');
      assert.equal(options.device, device);
      if (device === 'webgpu') throw new Error('GPU operator unavailable');
    } });
  } });
  assert.equal(model.device, 'wasm');
  assert.deepEqual(requests, [['webgpu', false], ['wasm', true]]);
  assert.equal((await model.run(ids, style, 1)).length, 3);
});

test('first inference failure retries in fresh WASM and rebuilds all input tensors', async () => {
  const calls = [];
  const model = await createKokoroModel({ device: 'webgpu', loadTransformers: async device => fakeRuntime({
    run: () => { calls.push(device); if (device === 'webgpu') throw new Error('device lost'); },
  }) });
  assert.equal((await model.run(ids, style, 1)).length, 3);
  assert.deepEqual(calls, ['webgpu', 'wasm']);
  assert.equal(model.device, 'wasm');
  await model.run(ids, style, 1);
  assert.deepEqual(calls, ['webgpu', 'wasm', 'wasm']);
});

test('failure after streamed audio never retries or repeats a chunk; next request uses WASM', async () => {
  const calls = [];
  const model = await createKokoroModel({ device: 'webgpu', loadTransformers: async device => fakeRuntime({
    run: () => { calls.push(device); if (calls.length === 2) throw new Error('device lost mid-utterance'); },
  }) });
  await model.run(ids, style, 1);
  await assert.rejects(model.run(ids, style, 1, { mayRetry: false }), error => error.code === 'TTS_INTERRUPTED');
  assert.deepEqual(calls, ['webgpu', 'webgpu']);
  assert.equal(model.device, 'wasm');
  await model.tokenize('nova fala');
  await model.run(ids, style, 1);
  assert.deepEqual(calls, ['webgpu', 'webgpu', 'wasm']);
});

test('WASM failure is surfaced without an infinite retry or a fabricated waveform', async () => {
  const calls = [];
  const model = await createKokoroModel({ device: 'webgpu', loadTransformers: async device => fakeRuntime({
    run: () => { calls.push(device); throw new Error('inference failed'); },
  }) });
  await assert.rejects(model.run(ids, style, 1), /inference failed/);
  assert.deepEqual(calls, ['webgpu', 'wasm']);
});

// Isolate fake asset digests in a VM: production still verifies the pinned
// hashes, while these tests exercise buffers/lifecycle without downloading.
const bridgeSource = (await readFile(new URL('./kokoro-pt.js', import.meta.url), 'utf8'))
  .replace(/^export /gmu, '') + '; globalThis.bridge = { createKokoroPt, PORTUGUESE_VOICES };';
function bridgeHarness({ phonemizerReady, onModelLoad } = {}) {
  const spoken = [], events = [];
  const scope = { URL, Float32Array, BigInt64Array, Uint8Array, Response, Intl, setTimeout, clearTimeout, fetch,
    crypto: { subtle: { digest: async (_name, bytes) => {
      const voice = Object.values(scope.bridge.PORTUGUESE_VOICES)[new Uint8Array(bytes)[0]];
      return Uint8Array.from(voice.sha256.match(/../g), byte => parseInt(byte, 16)).buffer;
    } } },
  };
  vm.runInNewContext(bridgeSource, scope);
  const options = {
    transformers: fakeRuntime({ load: onModelLoad, run: () => events.push('model') }),
    phonemizerBaseUrl: 'https://example.invalid/espeak/',
    createPhonemizer: async config => {
      await phonemizerReady;
      return { callMain: args => {
        const text = JSON.parse(args[args.indexOf('--input') + 1])[0].text;
        spoken.push(text); config.print(JSON.stringify({ phonemes: ['o', 'l', 'a'] }));
      } };
    },
    fetcher: async url => {
      const id = url.split('/').at(-1).replace('.bin', '');
      const bytes = new Uint8Array(522240);
      bytes[0] = Object.keys(scope.bridge.PORTUGUESE_VOICES).indexOf(id);
      return new Response(bytes);
    },
  };
  return { ready: scope.bridge.createKokoroPt(options), spoken, events };
}

test('phonemizer initialization does not delay independent model and voice loading', async () => {
  let release, modelStarted = false;
  const phonemizerReady = new Promise(resolve => { release = resolve; });
  const harness = bridgeHarness({ phonemizerReady, onModelLoad: () => { modelStarted = true; } });
  await new Promise(setImmediate);
  assert.equal(modelStarted, true);
  release();
  const engine = await harness.ready;
  await engine.dispose();
});

test('streaming transfers every sample once without constructing an unused full waveform', async () => {
  const harness = bridgeHarness(), engine = await harness.ready;
  const text = 'Estou com dor na barriga desde ontem. Ela vai e volta, piora quando ando e melhora um pouco quando fico quieta. Não tive febre nem vomitei.';
  const chunks = [];
  const streamed = await engine.synthesize(text, { returnSamples: false, onChunk: chunk => {
    chunks.push(structuredClone(chunk, { transfer: [chunk.samples.buffer] }));
  } });
  assert.equal(streamed.samples.length, 0);
  assert.equal(harness.spoken.join(' '), text);
  assert.deepEqual(chunks.map(chunk => chunk.index), [0, 1, 2]);
  assert.ok(chunks.every(chunk => chunk.total === chunks.length));
  const collected = await engine.synthesize(text);
  const flattened = chunks.flatMap(chunk => [...chunk.samples]);
  assert.deepEqual(flattened, [...collected.samples]);
  assert.equal(collected.samples.length, 3 * 3 + 2 * 1920);
});

test('warmup executes only fixed text silently and leaves the next real response intact', async () => {
  const harness = bridgeHarness(), engine = await harness.ready;
  await engine.warmup();
  assert.deepEqual(harness.spoken, ['Olá.']);
  const result = await engine.synthesize('Não tive febre.');
  assert.deepEqual(harness.spoken, ['Olá.', 'Não tive febre.']);
  assert.equal(result.samples.length, 3);
  assert.equal(harness.events.length, 2);
});
