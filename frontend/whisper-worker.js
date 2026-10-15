/**
 * MED LAB — local Portuguese speech recognition.
 *
 * Copy this module to /static/voice-local/whisper-worker.js together with the
 * pinned browser distribution of @huggingface/transformers@4.3.1. Create the worker and send `load`
 * only after the learner accepts the model download. Audio never becomes a URL
 * or a network request: `transcribe` accepts 16 kHz mono Float32Array samples.
 *
 * Request protocol:
 *   { type: 'load', id: string, device?: 'auto' | 'wasm' }
 *   { type: 'transcribe', id: string, audio: Float32Array, sampleRate: 16000 }
 *   { type: 'dispose', id: string }
 *
 * Replies preserve `id`: progress, ready, result, disposed, error.
 * `progress` describes observed downloads or processing stages; it is not an ETA.
 * To cancel an active model load/inference immediately, terminate this worker.
 */
import { env, pipeline } from '/static/voice-local/vendor/transformers.js';

export const WHISPER_MODEL = 'onnx-community/whisper-tiny';
export const WHISPER_REVISION = 'ff4177021cc41f7db950912b73ea4fdf7d01d8e7';
export const SAMPLE_RATE = 16000;
export const MAX_SECONDS = 45;
export const WHISPER_DTYPE = Object.freeze({ encoder_model: 'fp32', decoder_model_merged: 'q8' });

// Single-thread WASM works on shared hosting without cross-origin isolation.
// ONNX Runtime's .mjs and .wasm files are served by MED LAB, not an executable CDN.
env.allowLocalModels = false;
env.allowRemoteModels = true;
env.useBrowserCache = typeof self.caches !== 'undefined';
env.useFS = false;
env.useFSCache = false;
env.useWasmCache = false;
env.cacheKey = 'medlab-whisper-tiny-v1';
env.backends.onnx.wasm.numThreads = 1;
env.backends.onnx.wasm.proxy = false;
env.backends.onnx.wasm.wasmPaths = '/static/voice-local/ort/';

let transcriber = null;
let device = null;
let loading = false;
let transcribing = false;
let retired = false;

function reply(id, type, fields = {}) {
  self.postMessage({ id, type, ...fields });
}
function error(id, code, message) {
  reply(id, 'error', { code, message });
}
function progress(id, stage, message, detail = {}) {
  reply(id, 'progress', { stage, message, ...detail });
}
function reportFileProgress(id, info) {
  const detail = { status: String(info.status || 'progress') };
  if (typeof info.file === 'string') detail.file = info.file;
  for (const key of ['loaded', 'total', 'progress']) {
    if (Number.isFinite(info[key])) detail[key] = Math.max(0, info[key]);
  }
  progress(id, 'download', 'Baixando os arquivos da transcrição local.', detail);
}
async function chooseDevice(requested) {
  if (requested === 'wasm' || !self.navigator?.gpu) return 'wasm';
  try {
    const adapter = await self.navigator.gpu.requestAdapter({ powerPreference: 'low-power' });
    return adapter ? 'webgpu' : 'wasm';
  } catch {
    return 'wasm';
  }
}
async function createTranscriber(id, selectedDevice) {
  return pipeline('automatic-speech-recognition', WHISPER_MODEL, {
    revision: WHISPER_REVISION,
    device: selectedDevice,
    dtype: WHISPER_DTYPE,
    progress_callback: info => reportFileProgress(id, info),
  });
}
async function load(id, requestedDevice = 'auto') {
  if (retired) {
    error(id, 'DISPOSED', 'A transcrição local foi encerrada. Ative-a novamente para continuar.');
    return;
  }
  if (loading || transcribing) {
    error(id, 'BUSY', 'Aguarde a operação de voz em andamento.');
    return;
  }
  if (transcriber) {
    reply(id, 'ready', { device, model: WHISPER_MODEL, revision: WHISPER_REVISION, sampleRate: SAMPLE_RATE, maxSeconds: MAX_SECONDS, cache: env.useBrowserCache ? 'browser' : 'memory' });
    return;
  }
  loading = true;
  try {
    device = await chooseDevice(requestedDevice);
    progress(id, 'initializing', 'Preparando o modelo de transcrição no seu dispositivo.', { device });
    try {
      transcriber = await createTranscriber(id, device);
    } catch (firstError) {
      if (device !== 'webgpu') throw firstError;
      device = 'wasm';
      progress(id, 'fallback', 'A aceleração gráfica não está disponível para este modelo. Preparando o modo compatível.', { device });
      transcriber = await createTranscriber(id, device);
    }
    if (retired) {
      await transcriber?.dispose();
      transcriber = null;
      return;
    }
    reply(id, 'ready', { device, model: WHISPER_MODEL, revision: WHISPER_REVISION, sampleRate: SAMPLE_RATE, maxSeconds: MAX_SECONDS, cache: env.useBrowserCache ? 'browser' : 'memory' });
  } catch {
    transcriber = null;
    device = null;
    error(id, 'MODEL_LOAD_FAILED', 'Não foi possível preparar o modelo local. Verifique a conexão e o espaço disponível no navegador; você pode continuar por texto.');
  } finally {
    loading = false;
  }
}

export function validateAudio(audio, sampleRate) {
  if (!(audio instanceof Float32Array) || sampleRate !== SAMPLE_RATE) {
    throw new Error('O áudio precisa estar em mono, a 16 kHz, antes da transcrição.');
  }
  if (audio.length < 1600 || audio.length > SAMPLE_RATE * MAX_SECONDS) {
    throw new Error('Grave uma pergunta entre 0,1 e 45 segundos.');
  }
  let energy = 0;
  let peak = 0;
  for (let index = 0; index < audio.length; index++) {
    const sample = audio[index];
    if (!Number.isFinite(sample) || Math.abs(sample) > 1.01) {
      throw new Error('O áudio não pôde ser lido. Grave a pergunta novamente.');
    }
    energy += sample * sample;
    peak = Math.max(peak, Math.abs(sample));
  }
  return { silence: peak < 0.0005 && Math.sqrt(energy / audio.length) < 0.0001 };
}
async function transcribe(id, audio, sampleRate) {
  if (retired || !transcriber) {
    error(id, 'NOT_READY', 'Ative a transcrição local e aguarde o modelo ficar pronto antes de gravar.');
    return;
  }
  if (loading || transcribing) {
    error(id, 'BUSY', 'Aguarde a transcrição atual antes de enviar outra gravação.');
    return;
  }
  try {
    if (validateAudio(audio, sampleRate).silence) {
      error(id, 'NO_SPEECH', 'Não identificamos uma fala na gravação. Tente novamente, perto do microfone.');
      return;
    }
  } catch (cause) {
    error(id, 'INVALID_AUDIO', cause.message);
    return;
  }
  transcribing = true;
  progress(id, 'transcribing', 'Transcrevendo no seu dispositivo. O áudio permanece neste navegador.', { device });
  try {
    const options = {
      language: 'portuguese',
      task: 'transcribe',
      chunk_length_s: 30,
      stride_length_s: 5,
      return_timestamps: false,
      do_sample: false,
    };
    let result;
    try {
      result = await transcriber(audio, options);
    } catch (firstError) {
      // Some GPUs can load a graph but fail during inference. Reuse the same
      // cached model weights in WASM, then retry this local buffer once.
      if (device !== 'webgpu') throw firstError;
      await transcriber.dispose().catch(() => {});
      transcriber = null;
      device = 'wasm';
      progress(id, 'fallback', 'A aceleração gráfica foi interrompida. Continuando no modo compatível.', { device });
      transcriber = await createTranscriber(id, device);
      progress(id, 'transcribing', 'Transcrevendo no seu dispositivo, no modo compatível.', { device });
      result = await transcriber(audio, options);
    }
    const text = typeof result?.text === 'string' ? result.text.trim() : '';
    if (!text) {
      error(id, 'NO_SPEECH', 'Não identificamos uma pergunta. Tente novamente, perto do microfone.');
    } else if (text.length > 2000) {
      error(id, 'TEXT_TOO_LONG', 'A transcrição ultrapassou o limite da pergunta. Grave um trecho mais curto.');
    } else {
      reply(id, 'result', { text, device, language: 'pt-BR' });
    }
  } catch {
    error(id, transcriber ? 'TRANSCRIPTION_FAILED' : 'MODEL_LOAD_FAILED', 'A transcrição local não foi concluída. Tente uma gravação mais curta ou ative o modelo novamente.');
  } finally {
    transcribing = false;
  }
}
async function dispose(id) {
  if (loading || transcribing) {
    error(id, 'BUSY', 'Encerre o worker para cancelar a operação em andamento.');
    return;
  }
  retired = true;
  try { await transcriber?.dispose(); }
  finally { transcriber = null; reply(id, 'disposed'); }
}
self.addEventListener('message', event => {
  const data = event.data;
  if (!data || typeof data !== 'object' || typeof data.id !== 'string' || !data.id || data.id.length > 100) return;
  if (data.type === 'load') {
    load(data.id, data.device === 'wasm' ? 'wasm' : 'auto');
  } else if (data.type === 'transcribe') {
    transcribe(data.id, data.audio, data.sampleRate);
  } else if (data.type === 'dispose') {
    dispose(data.id).catch(() => error(data.id, 'DISPOSE_FAILED', 'Não foi possível liberar o modelo. Feche esta consulta para liberar a memória.'));
  } else {
    error(data.id, 'INVALID_REQUEST', 'A solicitação de voz não é válida.');
  }
});
