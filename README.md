# Automação Censo Escolar — Educacenso

Script em Python + Playwright que **vincula alunos às turmas** de uma escola no
[Educacenso](https://educacenso.inep.gov.br) a partir de uma planilha Excel,
evitando o cadastro manual aluno por aluno.

## Como funciona

Para cada aluno da planilha, o script:

1. Pesquisa o CPF em `/aluno/pesquisar`
2. Se o aluno já tem vínculo na escola → pula (`ja_vinculado`)
3. Se não foi encontrado → registra `nao_encontrado`
4. Caso contrário, abre o formulário "Vincular aluno", seleciona a turma
   (ano da aba + turno da planilha) e preenche os campos fixos
5. Salva o resultado em `censo_resultado.csv` — o progresso é **retomável**:
   rode de novo e ele continua de onde parou

## Instalação

```bash
pip install -r requirements.txt
playwright install chromium
```

## Configuração

1. Copie `.env.example` para `.env` e preencha CPF/senha de acesso e o código
   INEP da escola. **O `.env` nunca deve ser commitado.**
2. Monte sua planilha seguindo o `MODELO_PLANILHA.xlsx`:
   - Uma aba por ano letivo (o nome da aba deve conter o ano, ex.: "1º 2026")
   - Colunas usadas pelo script: **CPF** (col. B), **NOME** (col. C),
     **TURNO** (col. D — MATUTINO, NOTURNO ou INTEGRAL)
   - A col. A (**STATUS CENSO**) é livre para acompanhamento manual — o
     script não a lê
   - As turmas no Educacenso devem conter "\<ano\> \<turno\>" no nome
     (ex.: "1º 2026 MATUTINO")

## Uso

```bash
python censo_automacao.py
```

- Uma janela do Chrome abre; o login fica salvo na pasta `perfil_chrome/`
  (só precisa logar na primeira vez)
- Se o login automático falhar, faça login manualmente na janela e aperte
  ENTER no terminal
- Erros geram screenshot em `erros/<cpf>.png`

## Estrutura dos resultados (`censo_resultado.csv`)

| status | significado |
|---|---|
| `vinculado` | vínculo criado com sucesso |
| `ja_vinculado` | aluno já tinha vínculo na escola |
| `nao_encontrado` | CPF não encontrado no Educacenso |
| `sem_turma(...)` | turno sem turma correspondente (ex.: EAD) — resolver manualmente |
| `erro_*` | falha durante o preenchimento (ver screenshot em `erros/`) |

## Avisos

- Os campos fixos do formulário (carga horária, transporte etc.) refletem a
  realidade da escola original — revise `processar_aluno()` antes de usar
- Use por sua conta e risco; confira sempre os vínculos criados no sistema
