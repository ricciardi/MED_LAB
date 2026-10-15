/**
 * Browser-local Brazilian Portuguese Kokoro bridge.
 * Model/voice weights: Apache-2.0, onnx-community/Kokoro-82M-v1.0-ONNX.
 * G2P: @diffusionstudio/piper-wasm 1.0.0 wrapper (MIT), including eSpeak NG
 * (GPL-3.0). Keep its license and corresponding source with distributed assets.
 * IPA adaptation follows hexgrad/misaki EspeakG2P (Apache-2.0).
 * No consultation text is sent over the network; fetches contain asset URLs only.
 */
export const KOKORO_MODEL = "onnx-community/Kokoro-82M-v1.0-ONNX";
export const KOKORO_REVISION = "1939ad2a8e416c0acfeecc08a694d14ef25f2231";
export const SAMPLE_RATE = 24000;
export const PORTUGUESE_VOICES = Object.freeze({
  pf_dora: Object.freeze({ name: "Dora", gender: "female", sha256: "3da7b5b2d91847ebf5646f57631af6ececae3c29a89cd300f06edf9aa6cfe9ee" }),
  pm_alex: Object.freeze({ name: "Alex", gender: "male", sha256: "0175c753f59c54e7fd5a995bedef0c5ff2fb67e0043dd3dcb2ae74ec2acbeb2a" }),
  pm_santa: Object.freeze({ name: "Santa", gender: "male", sha256: "8b012db3185778afe2e45a62cbad69db73021774fe68dda634bcc748a982eede" }),
});

export class LocalVoiceError extends Error {
  constructor(code, message) { super(message); this.name = "LocalVoiceError"; this.code = code; }
}

/** Browser feature presence alone does not guarantee an available GPU adapter. */
export async function selectKokoroDevice(browserNavigator = globalThis.navigator) {
  if (typeof browserNavigator?.gpu?.requestAdapter !== "function") return "wasm";
  let timer;
  try {
    const adapter = await Promise.race([
      browserNavigator.gpu.requestAdapter({ powerPreference: "high-performance" }),
      new Promise(resolve => { timer = setTimeout(() => resolve(null), 4000); }),
    ]);
    return adapter && !adapter.isFallbackAdapter ? "webgpu" : "wasm";
  } catch { return "wasm"; }
  finally { clearTimeout(timer); }
}

/**
 * Separate model lifecycle permits a genuinely fresh WASM runtime after GPU
 * failure. Transformers 3.8 keeps rejected initialization/inference promises;
 * merely loading a second model into the same module would inherit the error.
 */
export async function createKokoroModel({ transformers, loadTransformers, device = "wasm", onProgress = () => {} }) {
  let runtime = transformers, model, tokenizer, currentDevice = device;
  let freshRuntime = false;
  const options = { revision: KOKORO_REVISION, progress_callback: info => onProgress({
    stage: "download", message: "Preparando a voz em português…", loaded: info.loaded,
    total: info.total, progress: info.progress,
  }) };
  const discardGpu = async () => {
    const previous = model;
    model = undefined;
    currentDevice = "wasm";
    freshRuntime = true;
    onProgress({ stage: "backend_fallback", device: "wasm", message: "Preparando uma alternativa de voz neste aparelho…" });
    try { await previous?.dispose(); } catch { /* A lost GPU may reject disposal too. */ }
  };
  const ensure = async () => {
    if (model) return;
    if (!runtime || freshRuntime) {
      if (typeof loadTransformers !== "function") throw new LocalVoiceError("RUNTIME_MISSING", "Os arquivos da voz local precisam ser preparados pelo administrador.");
      runtime = await loadTransformers(currentDevice, { fresh: freshRuntime });
      freshRuntime = false;
    }
    if (!runtime?.StyleTextToSpeech2Model || !runtime?.AutoTokenizer || !runtime?.Tensor) {
      throw new LocalVoiceError("RUNTIME_MISSING", "Os arquivos da voz local precisam ser preparados pelo administrador.");
    }
    try {
      [model, tokenizer] = await Promise.all([
        runtime.StyleTextToSpeech2Model.from_pretrained(KOKORO_MODEL, { ...options, dtype: "q8", device: currentDevice }),
        runtime.AutoTokenizer.from_pretrained(KOKORO_MODEL, options),
      ]);
    } catch (error) {
      if (currentDevice !== "webgpu" || typeof loadTransformers !== "function") throw error;
      await discardGpu();
      await ensure();
    }
  };
  await ensure();
  return {
    get device() { return currentDevice; },
    async tokenize(phonemes) { await ensure(); return tokenizer(phonemes, { truncation: false }); },
    async run(inputIds, style, speed, { mayRetry = true, cancelled = () => {} } = {}) {
      const invoke = async () => {
        await ensure();
        cancelled();
        // Rebuild every tensor in the selected runtime after a backend switch.
        const { Tensor } = runtime;
        const { waveform } = await model({
          input_ids: new Tensor("int64", inputIds.data, inputIds.dims),
          style: new Tensor("float32", style, [1, 256]),
          speed: new Tensor("float32", [speed], [1]),
        });
        const samples = Float32Array.from(waveform.data);
        if (!samples.length || samples.some(value => !Number.isFinite(value))) {
          throw new LocalVoiceError("INVALID_AUDIO", "A voz retornou um áudio inválido. Tente novamente.");
        }
        return samples;
      };
      try { return await invoke(); }
      catch (error) {
        if (currentDevice !== "webgpu" || typeof loadTransformers !== "function" || error?.code === "CANCELLED") throw error;
        cancelled();
        await discardGpu();
        if (!mayRetry) {
          // Earlier audio has already been emitted. Do not replay it; the next
          // user request will initialize WASM and start its own utterance.
          throw new LocalVoiceError("TTS_INTERRUPTED", "A fala foi interrompida. A próxima tentativa usará outro modo de processamento local.");
        }
        return invoke();
      }
    },
    async dispose() { await model?.dispose(); model = undefined; },
  };
}

/** Piper emits untied IPA code points, whereas Kokoro/Misaki uses these units. */
export function kokoroPhonemes(phonemes) {
  if (!Array.isArray(phonemes) || phonemes.some(p => typeof p !== "string")) {
    throw new LocalVoiceError("PHONEMIZER_FAILED", "Não foi possível preparar a pronúncia em português.");
  }
  const units = [
    ["aɪ", "I"], ["aʊ", "W"], ["dz", "ʣ"], ["dʒ", "ʤ"], ["eɪ", "A"],
    ["oʊ", "O"], ["əʊ", "Q"], ["ss", "S"], ["ts", "ʦ"], ["tʃ", "ʧ"], ["ɔɪ", "Y"],
  ];
  let value = phonemes.join("").replaceAll("\u0361", "").replaceAll("^", "");
  for (const [old, replacement] of units) value = value.replaceAll(old, replacement);
  return value.replaceAll("-", "").replace(/([.!?])(?=\S)/gu, "$1 ").trim();
}

/** Split at word boundaries; never silently truncate the patient's answer. */
export function speechChunks(text, maximum = 220) {
  if (typeof text !== "string" || !text.trim() || text.length > 4000 || /\0/u.test(text)) {
    throw new LocalVoiceError("INVALID_TEXT", "A fala deve conter entre 1 e 4.000 caracteres.");
  }
  const clean = text.replace(/\s+/gu, " ").trim();
  const sentences = typeof Intl.Segmenter === "function"
    ? [...new Intl.Segmenter("pt-BR", { granularity: "sentence" }).segment(clean)].map(s => s.segment.trim())
    : clean.match(/[^.!?]+[.!?]*/gu) || [clean];
  const chunks = [];
  let current = "";
  for (const sentence of sentences) {
    for (const word of sentence.split(/\s+/u)) {
      if (word.length > maximum) throw new LocalVoiceError("INVALID_TEXT", "A fala contém um trecho que não pode ser pronunciado.");
      if (current.length + word.length + 1 > maximum) { chunks.push(current); current = ""; }
      current += (current ? " " : "") + word;
    }
    // Keep short related sentences together for more stable Kokoro prosody.
    if (current.length >= 90) { chunks.push(current); current = ""; }
  }
  if (current) chunks.push(current);
  return chunks;
}

async function verifiedVoice(voiceId, fetcher) {
  const voice = PORTUGUESE_VOICES[voiceId];
  if (!voice) throw new LocalVoiceError("INVALID_VOICE", "Escolha uma voz de português brasileiro disponível.");
  const url = `https://huggingface.co/${KOKORO_MODEL}/resolve/${KOKORO_REVISION}/voices/${voiceId}.bin`;
  let cache, response;
  try {
    if (typeof caches !== "undefined") { cache = await caches.open("medlab-kokoro-voices-v1"); response = await cache.match(url); }
  } catch { /* A disabled cache must not prevent local speech. */ }
  if (!response) {
    response = await fetcher(url, { credentials: "omit", referrerPolicy: "no-referrer" });
    if (!response.ok) throw new LocalVoiceError("VOICE_DOWNLOAD_FAILED", "Não foi possível baixar a voz. Tente novamente.");
  }
  const bytes = await response.arrayBuffer();
  if (bytes.byteLength !== 522240) throw new LocalVoiceError("VOICE_INTEGRITY_FAILED", "O arquivo da voz está incompleto. Tente novamente.");
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  const hash = [...new Uint8Array(digest)].map(n => n.toString(16).padStart(2, "0")).join("");
  if (hash !== voice.sha256) {
    if (cache) await cache.delete(url).catch(() => {});
    throw new LocalVoiceError("VOICE_INTEGRITY_FAILED", "O arquivo da voz não passou na verificação. Tente novamente.");
  }
  if (cache) await cache.put(url, new Response(bytes)).catch(() => {});
  return new Float32Array(bytes);
}

/** Dependency injection keeps the same bridge testable in Node and in a worker. */
export async function createKokoroPt({ transformers, createPhonemizer, phonemizerBaseUrl,
  loadTransformers, device = "wasm", onProgress = () => {}, fetcher = fetch } = {}) {
  if (typeof createPhonemizer !== "function") {
    throw new LocalVoiceError("RUNTIME_MISSING", "Os arquivos da voz local precisam ser preparados pelo administrador.");
  }
  let phonemeResult;
  const phonemizer = await createPhonemizer({
    noInitialRun: true,
    locateFile: name => new URL(name, phonemizerBaseUrl).href,
    print: data => { try { phonemeResult = JSON.parse(data); } catch { phonemeResult = null; } },
    printErr: () => {},
  });
  onProgress({ stage: "loading", message: "Preparando a voz em português…" });
  const [model, loadedVoices] = await Promise.all([
    createKokoroModel({ transformers, loadTransformers, device, onProgress }),
    Promise.all(Object.keys(PORTUGUESE_VOICES).map(async id => [id, await verifiedVoice(id, fetcher)])),
  ]);
  const voices = new Map(loadedVoices);
  let busy = false;
  return {
    get device() { return model.device; },
    async synthesize(text, { voiceId = "pf_dora", speed = 1, signal, onProgress: progress = onProgress, onChunk } = {}) {
      if (!PORTUGUESE_VOICES[voiceId]) throw new LocalVoiceError("INVALID_VOICE", "Escolha uma voz de português brasileiro disponível.");
      if (typeof speed !== "number" || !Number.isFinite(speed) || speed < 0.7 || speed > 1.3) {
        throw new LocalVoiceError("INVALID_SPEED", "A velocidade da fala está fora do intervalo permitido.");
      }
      if (busy) throw new LocalVoiceError("VOICE_BUSY", "Aguarde a fala atual terminar.");
      const chunks = speechChunks(text);
      const cancelled = () => { if (signal?.aborted) throw new LocalVoiceError("CANCELLED", "Fala interrompida."); };
      const parts = [];
      busy = true;
      try {
        cancelled();
        if (!voices.has(voiceId)) voices.set(voiceId, await verifiedVoice(voiceId, fetcher));
        const data = voices.get(voiceId);
        for (let i = 0; i < chunks.length; i++) {
          cancelled();
          progress({ stage: "preparing_pronunciation", message: "Preparando a pronúncia…", index: i, total: chunks.length });
          phonemeResult = null;
          phonemizer.callMain(["-l", "pt-br", "--input", JSON.stringify([{ text: chunks[i] }]), "--espeak_data", "/espeak-ng-data"]);
          const phonemes = kokoroPhonemes(phonemeResult?.phonemes);
          const { input_ids } = await model.tokenize(phonemes);
          const count = input_ids.dims.at(-1);
          if (!Number.isInteger(count) || count <= 2 || count > 512) {
            throw new LocalVoiceError("PHONEME_LIMIT", "Um trecho da fala excedeu o limite de pronúncia. Tente uma resposta mais curta.");
          }
          const offset = Math.min(count - 2, 509) * 256;
          progress({ stage: "running_model", device: model.device, message: "Gerando a fala neste aparelho…", index: i, total: chunks.length, tokenCount: count });
          const samples = await model.run(input_ids, data.slice(offset, offset + 256), speed, { mayRetry: i === 0, cancelled });
          cancelled();
          if (parts.length) parts.push(new Float32Array(SAMPLE_RATE * 0.08));
          parts.push(samples);
          if (onChunk) {
            // Transferable copy: the caller can enqueue playback immediately.
            const chunk = new Float32Array(samples.length + (i ? SAMPLE_RATE * 0.08 : 0));
            chunk.set(samples, i ? SAMPLE_RATE * 0.08 : 0);
            onChunk({ samples: chunk, sampleRate: SAMPLE_RATE, index: i, total: chunks.length });
          }
          progress({ stage: "synthesizing", message: "Preparando a fala…", progress: (i + 1) / chunks.length * 100 });
        }
        const samples = new Float32Array(parts.reduce((total, part) => total + part.length, 0));
        let offset = 0;
        for (const part of parts) { samples.set(part, offset); offset += part.length; }
        return { samples, sampleRate: SAMPLE_RATE, device: model.device };
      } finally { busy = false; }
    },
    async dispose() { voices.clear(); await model.dispose(); },
  };
}
