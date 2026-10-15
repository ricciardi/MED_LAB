# Conversa contínua

Na consulta, clique em **Iniciar conversa contínua** e permita o microfone quando o navegador solicitar. Na primeira utilização, os modelos de reconhecimento e voz precisam ser baixados para o dispositivo. Aguarde aparecer **Escutando — faça sua pergunta**.

1. Faça uma pergunta e uma pausa de aproximadamente um segundo. O sistema encerra a gravação, transcreve com Whisper e envia o texto automaticamente à DeepSeek.
2. A última transcrição enviada permanece visível. O microfone fica desligado durante a transcrição, a espera pela resposta e a fala do paciente.
3. A resposta é salva no histórico. No modo contínuo, o texto aparece junto com o início do áudio; durante o preparo, o chat informa que está preparando a voz.
4. Depois do último trecho de áudio, o sistema volta a escutar. Aguarde o indicador antes de fazer outra pergunta.

**Parar conversa contínua** desliga a captura e interrompe a reprodução. Mudar de etapa ou sair da página também pausa o modo. Se a voz falhar, a resposta continua disponível em texto. Se um envio for interrompido, a pergunta é preservada; o servidor pode já ter concluído a resposta, portanto confira a conversa antes de tentar novamente.

O silêncio sem fala por 15 segundos pausa o microfone. Cada gravação tem limite de 45 segundos. A detecção usa a energia do áudio e pode ser afetada por ruído ou pausas longas durante uma pergunta. O modo manual **Ditar pergunta** continua disponível para revisar a transcrição antes do envio.

O áudio é processado no navegador. Somente o texto reconhecido segue para a conversa com a DeepSeek. Não há serviço de voz pago nem inferência de áudio no cPanel. Os downloads ficam sujeitos ao cache e ao espaço do navegador. Para uso remoto, o microfone exige HTTPS.

A alternância automática não elimina o tempo de processamento: há uma pausa para reconhecer a fala, obter a resposta e gerar a voz. A velocidade depende do dispositivo, do navegador e da extensão da resposta. Os testes em computador não demonstram o desempenho no Galaxy Tab S10 FE+.

As vozes disponíveis, os tamanhos dos modelos e os resultados de áudio estão documentados em [Créditos e limites da voz local](CREDITOS_VOZ.md). Os testes reais da IA estão em [Testes DeepSeek](TESTES_DEEPSEEK.md).

## Verificação integrada em 15/10/2026

Um ciclo no navegador usou detecção de pausa, gravação, Whisper e Kokoro reais com entrada WAV sintética e resposta DeepSeek previamente registrada, em banco isolado e sem microfone físico. Houve uma mensagem, um turno e uma reprodução, sem duplicação. A captura terminou antes do envio; a escuta reabriu 353 ms após o fim do áudio e pausou após 15 segundos sem fala. Reiniciar e parar pelo botão também deixou zero faixas de captura ativas. O console não apresentou erros. Esse replay verifica a integração e não mede nova latência de API nem valida o microfone físico ou o Samsung.
