# Paciente simulado e memória da conversa

O paciente representa uma pessoa, com linguagem cotidiana, emoções e contexto. A DeepSeek recebe o perfil utilizável inteiro, os turnos anteriores e a memória da simulação. A classificação de temas serve para organizar a avaliação; não limita quais campos o paciente pode consultar para responder.

O modelo pode elaborar descrições plausíveis de sensações, intensidade, impacto na rotina e preocupações quando essas informações não constam do caso. As elaborações devem ser compatíveis com idade, perspectiva do informante, cronologia, sintomas e antecedentes documentados. Não podem substituir um fato explícito, inventar exames ou resultados, anunciar o diagnóstico final ou criar tratamento e comorbidades importantes para justificar a história. O gabarito e os critérios de avaliação não são enviados ao personagem.

Também pode estabelecer detalhes sociais ou biográficos fictícios de baixa especificidade quando ainda não definidos: preferência religiosa, naturalidade, estado civil, moradia, escolaridade, lazer, recursos e apoio. Perguntas gerais sobre a vida fazem parte da entrevista mesmo sem relação direta com o diagnóstico. Uma nova informação social é registrada na memória e deve continuar coerente nas respostas seguintes; não é contada como um novo detalhe clínico. Isso não permite mudar identidade ou dados já estabelecidos, inventar doenças sob um rótulo social nem criar endereços exatos, documentos ou contatos. Crenças e costumes não devem ser deduzidos de nome, sexo ou origem, e uma religião não implica automaticamente restrições ao tratamento.

As respostas têm extensão proporcional à pergunta: uma pergunta fechada pode receber uma resposta breve; um convite para contar a história deve permitir desenvolvimento. Cumprimentos, acolhimento, preocupações e pedidos para explicar melhor fazem parte da conversa. A falta de um campo preenchido não obriga o sistema a retornar uma frase fixa de desconhecimento. Quando o texto transcrito realmente impede compreender um dado clínico, o paciente pede esclarecimento.

Conhecimento cotidiano não precisa estar escrito na ficha. Diante de uma falsa alternativa como “xixi líquido ou seco”, o personagem deve responder à parte evidente com bom senso e esclarecer a intenção da pergunta, sem dar uma aula nem tratar a alternativa absurda como um fato possível. Isso não permite presumir volume normal, ausência de dor ou outro dado individual: esses detalhes continuam sujeitos à ficha e à memória da simulação. Uma verdade geral não é registrada como novo sintoma do paciente.

## Continuidade e armazenamento

A memória fica na tabela `consultation_simulation`, separada de `clinical_case` e de `consultation.case_snapshot`. O estado contém a versão do formato, o ponto em que o novo modo começou e os detalhes elaborados com tema e número do turno. A aplicação acrescenta esses detalhes ao estado anterior; não deixa o navegador reescrever a memória.

Resposta e estado são salvos na mesma transação, sob o bloqueio da consulta. Um envio repetido com o mesmo identificador reutiliza a resposta já salva. Uma falha da IA ou perda do bloqueio não grava uma resposta ou memória parcial. O histórico completo disponível, até 40 turnos, acompanha a memória; o personagem não perde um detalhe apenas porque ele foi mencionado há mais de oito perguntas.

Consultas anteriores podem continuar no modo novo sem apagar suas mensagens. O sistema registra a partir de qual turno houve elaboração, sem atribuir retroativamente origem documental às respostas antigas. Não há tradução, reimportação nem reescrita das fichas reais para ativar a conversa.

## Avaliação e referência

O avaliador recebe a ficha original, as ações registradas pelo aluno, as falas do paciente e a memória de elaboração identificada separadamente. Um detalhe criado para a simulação não vira fato do artigo, critério obrigatório da rubrica ou evidência de que o estudante perguntou algo. O raciocínio deve ser analisado considerando o que foi efetivamente informado ao aluno; uma inconsistência do personagem precisa ser apontada como limitação da simulação.

O resultado apresenta os detalhes elaborados em uma seção própria. O diagnóstico e os exames de referência permanecem os da ficha original. Essa separação permite revisar a conversa e distinguir a fonte clínica da dramatização educacional.

No roteiro de Perguntas Básicas de Graduação, religião ou preferência religiosa integra a identificação geral (`I09`), respeitando autodeclaração, ausência de religião, recusa e contexto já conhecido. Aprofundar crenças ou práticas que influem no cuidado é um objetivo separado e condicional (`S07`); perguntar apenas a religião não cumpre automaticamente esse aprofundamento. Nenhuma pergunta específica é pré-requisito técnico para encerrar a consulta. Veja [critérios e limites](CASOS_BASICOS.md#perguntas-básicas-de-graduação).

As verificações de formato e persistência não constituem validação clínica automática de cada elaboração. Testes reais, falhas e limites estão documentados em [Testes DeepSeek](TESTES_DEEPSEEK.md).

## Atualização da instalação

Depois de atualizar os arquivos, execute `flask --app app upgrade-db` no ambiente Python da aplicação e reinicie o Passenger. Esse comando cria a nova tabela de modo idempotente. O fluxo existente de atualização do acervo continua preservando edições e exclusões. Uma instalação nova usa `init-db` normalmente.

O modo de demonstração continua explícito e usa regras locais; ele não demonstra a qualidade da conversa com DeepSeek. A voz continua lendo a mesma resposta textual salva na consulta, sem custo adicional de serviço de fala.
