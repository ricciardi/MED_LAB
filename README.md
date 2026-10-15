# MED LAB

Plataforma de simulação clínica em português, com pacientes virtuais, avaliação do raciocínio e histórico de aprendizado. Aplicação **Python/Flask para hospedagem cPanel com Passenger**, com interface clara e responsiva para computador, tablet e celular.

O projeto transforma o núcleo de pesquisa EasyMED deste repositório em um produto web. Preserva o processo **identificação da intenção → paciente simulado → avaliação em seis dimensões**. O código de pesquisa continua em `EasyMED/` e `SPBench/`; a aplicação utiliza sua própria implementação em português, com validação de dados e integração DeepSeek.

## O que está implementado

- Landing page, cadastro, login, logout e painel do estudante.
- Biblioteca com oito casos fictícios em português, filtros e escolha aleatória que prioriza casos diferentes dos cinco mais recentes.
- Consulta com diálogo, exame físico, exames complementares, hipótese diagnóstica, diferenciais e plano terapêutico.
- Avaliação com nota, evidências, acertos, oportunidades de melhoria e próximos passos nas dimensões de anamnese, exame físico, exames complementares, raciocínio diagnóstico, plano terapêutico e desempenho global.
- Persistência de contas, diálogos, exames solicitados, respostas e avaliações. Cada consulta guarda uma cópia do caso usado, preservando o histórico quando o material é atualizado.
- Administração separada, sem link na navegação pública: gestão de alunos, edição de casos e geração por IA como rascunho, com revisão antes da publicação. Casos gerados usam o mesmo esquema e processo de avaliação dos casos iniciais.
- DeepSeek configurável no servidor, com chave fora do navegador, limites de uso, tratamento de falhas e modo de demonstração explícito.
- SQLite para instalações pequenas ou MySQL/MariaDB via PyMySQL, sem necessidade de Node, Redis ou serviços de fila.

## Executar localmente

Requer Python 3.10 ou superior.

```bash
git clone https://github.com/ricciardi/MED_LAB.git
cd MED_LAB
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python -c 'import secrets; print(secrets.token_hex(32))'
```

Edite `.env`: use o segredo gerado em `SECRET_KEY`, configure `DEEPSEEK_API_KEY` e mantenha `DEMO_MODE=false`. Para apresentação local sem chave, defina `DEMO_MODE=true`: as respostas usam regras fixas e as avaliações são identificadas como **demonstração sem IA**. Esse modo não gera casos novos. Em HTTP local, use `SESSION_COOKIE_SECURE=false`; em produção HTTPS, mantenha `true`.

```bash
flask --app app init-db
flask --app app create-admin
flask --app app run --host 127.0.0.1 --port 5055
```

Acesse `http://127.0.0.1:5055`. A administração padrão fica em `/gestao-medlab/login`; personalize `ADMIN_PATH` no ambiente. O endereço discreto complementa a autenticação e a verificação de permissão. Não existe senha administrativa padrão.

`init-db` importa os oito casos iniciais uma única vez, sem sobrescrever edições existentes. Os cinquenta perfis originais do SPBench permanecem como material de pesquisa: não são publicados automaticamente, pois não contêm todos os gabaritos exigidos pela aplicação.

## Publicar no cPanel

Siga [Instalação e operação no cPanel](docs/CPANEL.md), incluindo configuração do interpretador, HTTPS, variáveis, banco e backup. Use um domínio ou subdomínio dedicado, com a aplicação servida na raiz `/` e código fora de `public_html`.

```bash
python scripts/package_release.py
```

O pacote `dist/medlab-cpanel.zip` inclui o ponto WSGI `passenger_wsgi.py:application`, código, casos, testes, documentação e manifesto SHA256. Não inclui `.env`, senhas, banco local, ambiente virtual ou capturas de tela. A hospedagem deve disponibilizar Python 3.10+, Passenger e acesso HTTPS de saída à API.

## Configuração da IA

| Variável | Padrão / finalidade |
| --- | --- |
| `DEEPSEEK_API_KEY` | Chave privada da instalação |
| `AI_BASE_URL` | `https://api.deepseek.com` |
| `AI_MODEL` | `deepseek-flash`, configurável |
| `AI_TIMEOUT` | 40 segundos por chamada, até 45 |
| `MAX_DAILY_AI_REQUESTS` | 120 operações de diálogo/avaliação por aluno por dia; um diálogo pode fazer duas chamadas ao provedor |
| `DEMO_MODE` | `false`; `true` usa somente demonstração local, mesmo com uma chave configurada |

A integração usa [JSON Output da DeepSeek](https://api-docs.deepseek.com/guides/json_mode/) e modelo configurável conforme a [documentação do provedor](https://api-docs.deepseek.com/quick_start/pricing/). A geração envia apenas os dados do caso e a atividade simulada necessária; nome da conta, email e senha não fazem parte dos prompts. As respostas de IA passam por validação estrutural antes de serem salvas. Falhas não são substituídas silenciosamente por avaliações demonstrativas.

## Testes e arquitetura

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Os testes cobrem autenticação, autorização, isolamento entre usuários, CSRF, histórico, aleatoriedade, limites de uso, recuperação após falha, geração e publicação, esquema dos casos e contratos do provedor. Chamadas ao provedor são simuladas nos testes: uma chave válida é necessária para validar a API real no servidor.

Consulte [Arquitetura e decisões](docs/ARQUITETURA.md). A aplicação inclui senhas com hash, cookies protegidos, invalidação de sessões após troca de senha, limites persistentes no banco, proteção CSRF e bloqueio de operações simultâneas na mesma consulta.

A [pesquisa de bases clínicas reais](docs/BASES_CLINICAS.md) compara fontes para ampliar a biblioteca, registra verificações de licença e descreve a adaptação para pacientes virtuais e avaliação. A importação de centenas de casos ainda não foi executada.

## Escopo educacional

Os oito casos iniciais são fictícios, acompanhados de referências e rubricas didáticas. Ainda não receberam validação clínica humana. Casos gerados por IA são salvos como rascunhos e exigem revisão administrativa antes de chegar aos estudantes. A avaliação apoia o estudo; não representa certificação de competência ou orientação para atendimento de pacientes reais.

Esta versão não inclui recuperação de senha por email: o operador usa `flask --app app reset-password EMAIL` com entrada protegida no Terminal. A publicação, o banco MySQL e o funcionamento real da DeepSeek devem ser verificados na hospedagem de destino.

## Origem e atribuição

Baseado no [EasyMED](https://github.com/FreedomIntelligence/EasyMED), de FreedomIntelligence. O [README original](docs/README-original.md) preserva a documentação, atribuição e declaração de licença MIT do projeto de origem. Os módulos e perfis originais permanecem no repositório.

```bibtex
@article{zhang2025human,
  title={Human or LLM as Standardized Patients? A Comparative Study for Medical Education},
  author={Zhang, Bingquan and Liu, Xiaoxiao and Wang, Yuchi and Zhou, Lei and Xie, Qianqian and Wang, Benyou},
  journal={arXiv preprint arXiv:2511.14783},
  year={2025}
}
```
