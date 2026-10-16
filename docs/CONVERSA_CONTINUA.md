# Conversa contínua

Na consulta, clique em **Iniciar conversa contínua** e permita o microfone quando o navegador solicitar. Na primeira utilização, os modelos de reconhecimento e voz precisam ser baixados para o dispositivo. Aguarde aparecer **Escutando — faça sua pergunta**.

1. Faça uma pergunta e uma pausa de aproximadamente 1,6 segundo. O sistema encerra a gravação, transcreve com Whisper e envia o texto automaticamente à DeepSeek.
2. A última transcrição enviada permanece visível. O microfone fica desligado durante a transcrição, a espera pela resposta e a fala do paciente.
3. A resposta é salva no histórico. No modo contínuo, o texto aparece junto com o início do áudio; durante o preparo, o chat informa que está preparando a voz.
4. Depois do último trecho de áudio, o sistema volta a escutar. Aguarde o indicador antes de fazer outra pergunta.

**Parar conversa contínua** desliga a captura e interrompe a reprodução. Mudar de etapa ou sair da página também pausa o modo. Se a voz falhar, a resposta continua disponível em texto. Se um envio for interrompido, a pergunta é preservada; o servidor pode já ter concluído a resposta, portanto confira a conversa antes de tentar novamente.

O silêncio sem fala por 15 segundos pausa o microfone. Cada gravação tem limite de 45 segundos. A detecção usa a energia do áudio, conserva o início imediato da fala e tolera terminações de voz mais baixas. Ruído e pausas maiores que 1,6 segundo ainda podem dividir uma pergunta. Se o sinal estiver muito baixo ou saturado, o envio automático é pausado e o texto fica disponível para revisão; regrave se a transcrição não corresponder ao que você disse. Essa verificação mede o sinal, não certifica a correção das palavras. O modo manual **Ditar pergunta** continua disponível para revisar a transcrição antes do envio.

O áudio é processado no navegador. Somente o texto reconhecido segue para a conversa com a DeepSeek. Não há serviço de voz pago nem inferência de áudio no cPanel. Os downloads ficam sujeitos ao cache e ao espaço do navegador. Para uso remoto, o microfone exige HTTPS.

A alternância automática não elimina o tempo de processamento: há uma pausa para reconhecer a fala, obter a resposta e gerar a voz. A velocidade depende do dispositivo, do navegador e da extensão da resposta. Os testes em computador não demonstram o desempenho no Galaxy Tab S10 FE+.

A ativação agora inclui um aquecimento silencioso da síntese, e as respostas começam por trechos menores. O texto acompanha o instante agendado para iniciar a reprodução. Um pequeno buffer pode adiar esse início para reduzir interrupções entre trechos; não elimina o tempo de geração. Em um benchmark local no Mac, o primeiro trecho ficou pronto em 3,42 s com Dora e 3,01 s com Alex, ante 5,41 e 5,30 s. O início com o buffer final foi estimado por replay em 3,46 e 3,71 s; não houve nova gravação dessa reprodução nem teste em tablet. Respostas curtas continuaram próximas de dois segundos, e o tempo total de síntese não melhorou uniformemente.

As vozes disponíveis, os tamanhos dos modelos e os resultados de áudio estão documentados em [Créditos e limites da voz local](CREDITOS_VOZ.md). Os testes reais da IA estão em [Testes DeepSeek](TESTES_DEEPSEEK.md).

O conteúdo da fala usa o [paciente simulado com memória](PACIENTE_SIMULADO.md): respostas podem desenvolver experiências plausíveis relacionadas ao caso, preservando fatos documentados e detalhes já estabelecidos na consulta. A mesma memória é usada por texto e por voz; trocar o modo de entrada não reinicia o personagem.

## Encontrar e solicitar exames

Na biblioteca, **Com resultados complementares** seleciona casos com achados textuais utilizáveis; **Com imagens disponíveis** seleciona apenas imagens vinculadas e liberadas para exibição. Os filtros também valem para o sorteio, e as contagens de tipo/dificuldade acompanham as demais escolhas. Dificuldade **estimada** descreve a extensão do roteiro, não a gravidade da doença ou sua competência.

Durante a consulta, abra a aba **Exames**, que informa a quantidade disponível, e clique em **Solicitar**. O resultado aparece logo abaixo, com imagem quando houver. Pedir um exame ao paciente por voz ou texto não aciona esse botão. Alguns resultados estão agrupados sob nomes gerais; uma ficha pode não documentar o exame procurado. No snapshot local conferido, 13.985 casos têm complementares úteis e sete têm 15 imagens disponíveis; imagens em preparação não entram nessa contagem.

## Verificação integrada em 15/10/2026

Um ciclo no navegador usou detecção de pausa, gravação, Whisper e Kokoro reais com entrada WAV sintética e resposta DeepSeek previamente registrada, em banco isolado e sem microfone físico. Houve uma mensagem, um turno e uma reprodução, sem duplicação. A captura terminou antes do envio; a escuta reabriu 353 ms após o fim do áudio e pausou após 15 segundos sem fala. Reiniciar e parar pelo botão também deixou zero faixas de captura ativas. O console não apresentou erros. Esse replay verifica a integração e não mede nova latência de API nem valida o microfone físico ou o Samsung.

## Concluir a consulta e receber a avaliação

Você decide quando reuniu informações suficientes. No chat, use **Concluir consulta e receber avaliação**. A captura e a reprodução param, e a etapa de conclusões pede sua hipótese diagnóstica, os diferenciais que considerar e a conduta proposta. Você ainda pode voltar à conversa antes de enviar.

O botão **Enviar conclusões e receber avaliação** solicita a avaliação por IA. O resultado reúne notas por dimensão, comentários disponíveis sobre suas perguntas e decisões, pontos fortes, oportunidades de melhoria e comparação da hipótese/conduta com a referência do caso. O caso não termina automaticamente pelo silêncio ou por um tempo de consulta. O limite técnico de 40 perguntas apenas impede novos turnos; a avaliação depende do envio das conclusões.

A avaliação considera o que o estudante efetivamente registrou. Falas espontâneas do paciente não comprovam perguntas específicas, e pedir um exame não comprova execução ou interpretação. Quando um comentário não passa pela conferência de evidências, essa parte fica identificada como limitada; comentários conferidos de outras dimensões podem ser preservados se as notas forem consistentes. As conclusões e o histórico permanecem salvos se a avaliação falhar.

Em **Como sua nota foi calculada**, confira a nota da consulta inteira e a de anamnese separadamente. A escala é de 0 a 100, com média simples das cinco dimensões avaliáveis: anamnese, exame físico, exames complementares, raciocínio diagnóstico e plano terapêutico. Uma dimensão não avaliável (`null`) fica fora do cálculo; zero é uma nota e permanece na média. Por exemplo, `(25 + 0 + 0 + 0 + 0) ÷ 5 = 5/100`. O desempenho global não entra outra vez. Notas antigas permanecem intactas; se houver inconsistência no registro, a página avisa.

**Para sua próxima consulta** carrega automaticamente um parecer complementar, separado da nota, com exemplos de perguntas, explicações sobre raciocínio e conduta e motivos para solicitar ou dispensar exames. Aguarde a preparação; se houver erro, use **Tentar novamente**. O parecer é salvo e reaparece ao voltar ao resultado, sem nova chamada à IA; se ainda não foi salvo, reabrir a página retoma a solicitação. Essa etapa não muda sua nota. No modo demonstração, não é gerado um parecer complementar.

O parecer tem orçamento de 50 segundos e até duas chamadas à IA, incluindo uma correção de formato, sujeito às cotas de uso. É orientação educacional e pode conter imprecisões, sem certificar competência ou revisão clínica. Os [limites e o registro de consumo](CPANEL.md#resultado-e-parecer-complementar) estão no guia de operação.
