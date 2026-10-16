# Ranking de estudantes

Existem duas classificações independentes: **Casos Básicos** e **Casos Clínicos**. A landing page apresenta os 15 primeiros de cada uma. No topo da área autenticada, o estudante vê sua posição em ambas, mesmo quando está fora dos 15 primeiros.

## Entrada e cálculo

São necessários **cinco casos diferentes** com avaliações válidas dentro da categoria. Antes disso, a indicação é **Em formação**, acompanhada da quantidade que falta. Uma única nota 80 não coloca um iniciante à frente de alguém já elegível com média 45.

Todas as tentativas válidas contam, mas repetir um caso não aumenta seu peso. Primeiro se calcula a média das notas daquele caso; depois, a média dessas médias entre os casos distintos. Em notação: para cada caso `c`, `m_c = soma das notas / número de tentativas`; para `N` casos distintos, `média = soma(m_c) / N`.

Exemplo: em um caso, notas 60 e 80 produzem média 70. Se os outros quatro casos tiveram 50, 60, 80 e 90, a média do ranking é `(70 + 50 + 60 + 80 + 90) / 5 = 70`. Novas tentativas podem modificar a média daquele caso, mas não somam um novo caso nem multiplicam seu peso.

A classificação usa a média arredondada em duas casas decimais, com arredondamento decimal convencional. Médias iguais compartilham a mesma posição: **1º, 1º, 3º**. Quantidade de tentativas e velocidade não desempatam. O Top 15 limita a exibição a 15 participantes, inclusive quando há empate na última posição; a posição pessoal continua disponível.

## Quais resultados entram

- Consultas encerradas com nota numérica válida e consistente com a avaliação.
- A categoria é determinada pela trilha do caso salvo na consulta.
- Demonstrações, avaliações pendentes e resultados sem proveniência de IA verificável ficam fora.
- Avaliações históricas aceitas são aproveitadas sem alterar suas notas. Notas antigas e atuais podem usar versões diferentes da rubrica; o ranking não elimina essa diferença metodológica.
- Contas inativas não aparecem na classificação pública.

As notas são estimativas educacionais da IA. O ranking acompanha a prática na plataforma; não certifica competência clínica nem compara formalmente a dificuldade dos casos.

## Atualização e privacidade

Os resultados por consulta e as médias por estudante/categoria são persistidos. Uma conclusão válida ou retomada bem-sucedida atualiza os agregados na mesma transação do resultado. A posição resulta da comparação com as médias elegíveis atuais; não depende de uma posição antiga gravada.

A interface recebe os dados ao abrir a página e os atualiza a cada 60 segundos enquanto a aba está visível, além de atualizar ao retornar à aba. A leitura não faz novas chamadas de IA. Em falha de rede, a última apresentação permanece até a próxima atualização.

O ranking público mostra primeiro nome e iniciais, nunca e-mail, conversas, diagnósticos ou identificador interno. O endpoint entrega somente as posições pessoais da conta autenticada, além do Top 15 público.

Para instalações existentes, execute `flask --app app upgrade-db` em manutenção. Para reconstruir os agregados depois de restaurar ou corrigir o histórico, execute `flask --app app rebuild-rankings`, também em manutenção. Os registros originais são preservados.
