"""Public configuration for local browser speech; Python never handles audio."""

from pathlib import Path

MAX_AUDIO_SECONDS = 45
VOICES = (
    {"id": "pf_dora", "label": "Dora · feminina", "gender": "female"},
    {"id": "pm_alex", "label": "Alex · masculina", "gender": "male"},
    {"id": "pm_santa", "label": "Santa · masculina", "gender": "male"},
)


def case_gender(case):
    value = str(case.get("patientProfile", {}).get("gender", "")).strip().casefold()
    if value in {"feminino", "feminina", "female", "mulher", "menina", "sexo feminino", "f"}:
        return "female"
    if value in {"masculino", "masculina", "male", "homem", "menino", "sexo masculino", "m"}:
        return "male"
    return "unknown"


def voice_options(case):
    gender = case_gender(case)
    voices = [dict(v) for v in VOICES if gender == "unknown" or v["gender"] == gender]
    return {"gender": gender, "gender_locked": gender != "unknown", "voices": voices,
            "default_voice": voices[0]["id"], "max_recording_seconds": MAX_AUDIO_SECONDS}


class SpeechService:
    def __init__(self, config):
        self.enabled = bool(config.get("LOCAL_VOICE_ENABLED", True))
        self.asset_root = Path(config.get("LOCAL_VOICE_ASSET_ROOT") or
                               Path(__file__).parent / "static" / "voice-local")

    @property
    def available(self):
        required = ("whisper-worker.js", "kokoro-worker.js", "vendor/transformers.js",
                    "vendor/transformers-tts.js", "ort-tts/ort-wasm-simd-threaded.jsep.mjs",
                    "ort-tts/ort-wasm-simd-threaded.jsep.wasm",
                    "kokoro-pt.js", "espeak/piper_phonemize.js", "espeak/piper_phonemize.wasm",
                    "espeak/piper_phonemize.data", "ort/ort-wasm-simd-threaded.asyncify.mjs",
                    "ort/ort-wasm-simd-threaded.asyncify.wasm",
                    "ort/ort-wasm-simd-threaded.jsep.mjs", "ort/ort-wasm-simd-threaded.jsep.wasm")
        return self.enabled and all((self.asset_root / path).is_file() for path in required)

    def configuration(self, case):
        result = voice_options(case)
        result.update(provider="local", available=self.available,
            reason=("" if self.available else
                    "A voz está desativada pela equipe. Você pode continuar por texto." if not self.enabled else
                    "Os recursos de voz ainda estão sendo preparados. Você pode continuar por texto."),
            models_asr={"worker_url": "/static/voice-local/whisper-worker.js",
                        "download_mb": 175, "label": "Whisper base · português"},
            models_tts={"worker_url": "/static/voice-local/kokoro-worker.js",
                        "download_mb": 140, "label": "Kokoro · português brasileiro"})
        return result
