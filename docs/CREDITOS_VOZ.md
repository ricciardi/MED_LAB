# Voz local: componentes, fontes e limites

O MED LAB usa reconhecimento e síntese no navegador após ativação pelo estudante. Não há chamada a uma API paga de fala nem envio da gravação ao servidor. A conversa por texto continua usando a DeepSeek conforme a configuração da instalação. Downloads, processamento e uso de memória ocorrem no dispositivo; esta arquitetura evita instalar um serviço de inferência no cPanel.

## Componentes usados

| Componente | Versão ou revisão utilizada | Origem e licença |
| --- | --- | --- |
| Whisper tiny multilíngue | ONNX `ff4177021cc41f7db950912b73ea4fdf7d01d8e7`; encoder fp32 e decoder merged q8 | [Conversão ONNX Community](https://huggingface.co/onnx-community/whisper-tiny), baseada no [Whisper da OpenAI, MIT](https://github.com/openai/whisper/blob/main/LICENSE). |
| Kokoro-82M v1.0 | ONNX `1939ad2a8e416c0acfeecc08a694d14ef25f2231`, q8 | [Conversão ONNX Community](https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX), baseada no [modelo de hexgrad, Apache-2.0](https://huggingface.co/hexgrad/Kokoro-82M). |
| Transformers.js para Whisper | `4.3.1` | [Hugging Face, Apache-2.0](https://github.com/huggingface/transformers.js/blob/4.3.1/LICENSE). |
| ONNX Runtime Web para Whisper | `1.31.0-dev.20260914-8d85527a0`, fixada pelo runtime distribuído | [Microsoft, MIT](https://github.com/microsoft/onnxruntime/blob/8d85527a0/LICENSE). |
| Transformers.js para Kokoro | `3.8.1`, em `vendor/transformers-tts.js` | [Hugging Face, Apache-2.0](https://github.com/huggingface/transformers.js/blob/3.8.1/LICENSE). |
| ONNX Runtime Web para Kokoro | `1.22.0-dev.20250409-89f8206ba4`, em `ort-tts/` | [Microsoft, MIT](https://github.com/microsoft/onnxruntime/blob/89f8206ba4/LICENSE). |
| Piper phonemizer para WebAssembly | `@diffusionstudio/piper-wasm@1.0.0` | [Wrapper e instruções de compilação](https://github.com/diffusionstudio/piper-wasm), incluindo [Piper phonemize](https://github.com/wide-video/piper-phonemize) e [eSpeak NG, GPLv3](https://github.com/rhasspy/espeak-ng/blob/master/COPYING). |
| Adaptação de fonemas para Kokoro | Ponte própria em `frontend/kokoro-pt.js` | Conversões baseadas em [Misaki/EspeakG2P](https://github.com/hexgrad/misaki/blob/main/misaki/espeak.py), com [licença Apache-2.0](https://github.com/hexgrad/misaki/blob/main/LICENSE). |

As versões, URLs, tamanhos e SHA256 dos runtimes distribuídos estão em `scripts/voice_assets.json`. `scripts/prepare_voice_assets.py` confere esses arquivos e os coloca em `medlab/static/voice-local/`; as licenças acompanham os ativos na subpasta `licenses/`. Whisper e Kokoro usam runtimes separados para compatibilidade de inferência. Alterações de versão exigem rever o manifesto e testar os dois workers, em vez de atualizar uma URL de CDN sem conferência.

## Português brasileiro

O [catálogo oficial de Kokoro](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md) identifica `pf_dora` como feminina e `pm_alex`/`pm_santa` como masculinas, e indica `pt-br` para o fonemizador eSpeak. O MED LAB usa esses estilos, com áudio a 24 kHz. Os arquivos de estilo são conferidos por SHA256 antes de usar.

O pacote `kokoro-js@1.2.1` não expõe essas vozes em sua lista padrão e seu fonemizador é voltado a inglês. A aplicação utiliza uma ponte específica: eSpeak em WebAssembly converte português em fonemas, a adaptação de Misaki ajusta as unidades e o modelo ONNX recebe os IDs e o estilo da voz. Esta integração não equivale a afirmar que o pacote padrão já suporta português sem adaptação. Não há clonagem da voz de estudantes ou pacientes.

As vozes são sintéticas. Termos médicos, nomes próprios, abreviações, números e ritmo podem exigir melhorias. O reconhecimento também pode errar palavras e pontuação; por isso a transcrição é editável antes do envio. A interface mantém o texto da resposta disponível.

## Distribuição do fonemizador

O pacote do wrapper declara MIT, mas o binário WebAssembly incorpora eSpeak NG, distribuído sob GPLv3. O aviso MIT do wrapper não substitui essa licença. Ao redistribuir o binário, preserve os avisos, a licença e o acesso à fonte correspondente e às instruções de compilação. O [README do build utilizado](https://github.com/diffusionstudio/piper-wasm#readme) documenta Emscripten 3.1.47, o fork de eSpeak e Piper phonemize utilizados na compilação.

Os arquivos de origem abaixo acompanham os runtimes em `medlab/static/voice-local/sources/` e podem ser obtidos no domínio publicado pelo mesmo caminho com prefixo `/static/voice-local/sources/`:

| Arquivo entregue | Origem fixada e finalidade |
| --- | --- |
| `piper-phonemize-cfff8e52.tar.gz` | Fork `wide-video/piper-phonemize`, commit `cfff8e52ebaea37c7e953ae2d06b174acb827ac4`; inclui a opção `--input` usada pela ponte. |
| `espeak-ng-code-0f65aa30.tar.gz` | Fork `rhasspy/espeak-ng`, commit `0f65aa301e0d6bae5e172cc74197d32a6182200f`, fixado pelo CMake do fonemizador. |
| `espeak-ng-data-8593723f.tar.gz` | Mesmo fork, commit `8593723f10cfd9befd50de447f14bf0a9d2a14a4`, fonte dos dados de pronúncia na sequência de build documentada. |
| `piper-wasm-build-69522c83.md` | README do `gitHead` publicado pelo pacote npm; contém comandos Emscripten e as alterações com `sed`. |
| `piper-wasm-package-022edba0.json` | Metadados de empacotamento e declaração de licença do wrapper. |
| `misaki-espeak-fba12365.py` | Implementação original das adaptações de fonemas, commit `fba1236595f2d2bf21d414ba6e57d25256afada3`. |

As cópias de GPLv3, MIT e Apache-2.0 ficam em `medlab/static/voice-local/licenses/`. Preserve esses diretórios e o manifesto ao gerar outra distribuição. O mapeamento acima reúne as fontes e instruções do build upstream; **não foi demonstrada uma reconstrução byte a byte do binário pré-compilado**. O MED LAB preserva os bytes dos arquivos `.wasm` e `.data`. No JavaScript de integração original, acrescenta somente `export default createPiperPhonemize;` para importação como módulo. O manifesto registra o SHA256 de origem e o SHA256 do resultado dessa adaptação, executada por `scripts/prepare_voice_assets.py`.

As licenças do modelo, do runtime e do fonemizador são componentes diferentes. A gratuidade da execução não elimina as condições de redistribuição dos arquivos.

## Download e compatibilidade

Reserve aproximadamente 100 MB para o reconhecimento e mais 140 MB para a síntese, incluindo arquivos auxiliares. O encoder e o decoder Whisper selecionados somam 63.624.233 bytes; o runtime ONNX e o tokenizer completam o primeiro conjunto. O modelo Kokoro q8 tem 92.361.116 bytes. Seus arquivos auxiliares somam cerca de 41,4 MB, incluindo 18,1 MB de dados de pronúncia e 21,6 MB do runtime ONNX separado; os três estilos acrescentam aproximadamente 1,6 MB. O tamanho transferido depende de cache, backend selecionado e compressão do servidor.

WebGPU é uma opção somente para a transcrição Whisper, com alternativa WebAssembly. A síntese Kokoro executa sempre em WebAssembly, em uma thread, no processador do dispositivo. Nenhum desses caminhos exige GPU ou isolamento de origem no servidor. Os modelos podem consumir mais memória durante a execução do que seu tamanho de download. O cache é administrado pelo navegador e pode ser removido por falta de espaço, limpeza de dados ou políticas do dispositivo.

O microfone exige HTTPS e permissão do navegador. O áudio de entrada permanece local; somente a pergunta textual que o estudante confirma é enviada e persistida. Arquivos de modelos são obtidos do Hugging Face, e não contêm a gravação ou o conteúdo da consulta em suas requisições.

Uma execução em navegador no Mac transcreveu uma amostra sintética em português de 10,76 segundos em cerca de 1,3 segundo, com pequenas diferenças no texto. A síntese inicial com Dora, usando WebAssembly, gerou 7,9 segundos de áudio em aproximadamente 12,5 segundos, com o primeiro trecho após 9,3 segundos. São observações de uma amostra e de um dispositivo, sem caracterizar acurácia clínica ou fluidez garantida.

O teste em um Samsung real permanece pendente. A instituição deve conferir carregamento, captura, transcrição e reprodução nos aparelhos e navegadores usados pelos estudantes. Uma interface responsiva e uma execução em computador não comprovam desempenho de inferência em todos os celulares.

O teste local de Kokoro q8 com WebGPU chegou a produzir um arquivo de áudio, mas a verificação do conteúdo revelou fala incompatível com a frase solicitada. Esse caminho foi descartado e não é oferecido na aplicação. Concluir a inferência ou produzir uma forma de onda não comprova inteligibilidade nem preservação do texto. A síntese permanece em WASM, sem promessa de conversa instantânea ou desempenho igual no tablet.
