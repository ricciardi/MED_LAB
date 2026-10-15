/** Browser-local Kokoro PT-BR worker. Asset preparation supplies pinned runtimes. */
import createPhonemizer from "./espeak/piper_phonemize.js";
import { createKokoroPt, LocalVoiceError } from "./kokoro-pt.js";

let runtimeSequence = 0;
async function loadTransformers(_device, { fresh = false } = {}) {
  // A separate module instance resets rejected Transformers/ORT promise chains.
  // Only fixed local asset paths are requested; text never enters a URL.
  const url = new URL("./vendor/transformers-tts.js", import.meta.url);
  url.searchParams.set("backend", "wasm");
  if (fresh) url.searchParams.set("runtime", String(++runtimeSequence));
  const transformers = await import(url.href);
  transformers.env.allowLocalModels = false;
  transformers.env.useBrowserCache = true;
  transformers.env.backends.onnx.wasm.numThreads = 1;
  transformers.env.backends.onnx.wasm.wasmPaths = new URL("./ort-tts/", import.meta.url).href;
  return transformers;
}

let enginePromise;
let activeId;
let activeController;
let queue = Promise.resolve();
const progress = info => postMessage({ type: "progress", id: activeId, ...info });
function load() {
  if (!enginePromise) {
    // Browser QA found unintelligible Kokoro q8 output on WebGPU. Waveform
    // validity does not establish speech quality. TTS always uses tested WASM;
    // incoming device preferences cannot override this. Whisper is independent.
    enginePromise = createKokoroPt({ loadTransformers, createPhonemizer,
      phonemizerBaseUrl: new URL("./espeak/", import.meta.url), device: "wasm", onProgress: progress,
    }).catch(error => { enginePromise = undefined; throw error; });
  }
  return enginePromise;
}

self.onmessage = ({ data }) => {
  if (!data || !["load", "synthesize", "cancel"].includes(data.type)) return;
  if (data.type === "cancel") { if (!data.id || data.id === activeId) activeController?.abort(); return; }
  queue = queue.then(async () => {
    activeId = data.id;
    activeController = new AbortController();
    try {
      const engine = await load();
      if (data.type === "load") postMessage({ type: "ready", id: data.id, device: engine.device });
      else {
        const result = await engine.synthesize(data.text, { voiceId: data.voiceId || "pf_dora", signal: activeController.signal, onProgress: progress,
          onChunk: data.stream ? chunk => postMessage({ type: "chunk", id: data.id, ...chunk }, [chunk.samples.buffer]) : undefined,
        });
        if (data.stream) postMessage({ type: "result", id: data.id, streamed: true, samples: new Float32Array(0), sampleRate: result.sampleRate, device: result.device });
        else postMessage({ type: "result", id: data.id, ...result }, [result.samples.buffer]);
      }
    } catch (error) {
      postMessage({ type: "error", id: data.id, code: error instanceof LocalVoiceError ? error.code : "TTS_UNAVAILABLE",
        message: error instanceof LocalVoiceError ? error.message : "Não foi possível preparar a voz neste navegador. Tente novamente ou continue por texto." });
    } finally { activeId = undefined; activeController = undefined; }
  });
};
