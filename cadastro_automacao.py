# -*- coding: utf-8 -*-
"""
Automação v2 — CADASTRA no Educacenso os alunos com status `nao_encontrado`
do censo_resultado.csv e, em seguida, faz o vínculo à turma (o sistema cai
direto no formulário de vínculo após o cadastro).

Fluxo mapeado ao vivo em 18/07/2026 (cadastro manual observado):
  pesquisar CPF (nada) -> filtros detalhados: nome + data de nascimento
  (nada) -> botão #botao-cadastrar -> /aluno/dados-cadastrais
  -> CPF, filiações, Sexo, Cor/Raça, Nacionalidade -> UF/Município de
  nascimento -> dois grupos Sim/Não = "Não" -> Continuar -> Sim
  -> residência: Brasil/DF/Brasília/Urbana/sem localização diferenciada
  -> Enviar -> Sim -> /aluno/vincular (formulário que a v1 já preenche)

Rodar:
  python cadastro_automacao.py [max_cadastros]
  (ex.: "python cadastro_automacao.py 1" para testar com um aluno só)

Progresso em cadastro_resultado.csv (retomável).
"""
import csv
import os
import sys

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from censo_automacao import (
    URL_BASE, PASTA, PASTA_PERFIL, PASTA_ERROS, TIMEOUT, TURNOS_VALIDOS,
    ARQ_RESULTADO, fazer_login, carregar_alunos, chave, pesquisa_encontrou,
    clicar_botao_js, preencher_formulario_vinculo)

ARQ_CADASTRO = os.path.join(PASTA, "cadastro_resultado.csv")

# Regras fixas decididas pelo usuário (18/07/2026): residência igual p/ todos
RESIDENCIA_PAIS = "Brasil"
RESIDENCIA_UF = "DF"
RESIDENCIA_MUNICIPIO = "Brasília"
RESIDENCIA_ZONA = "Urbana"
RESIDENCIA_DIFERENCIADA = "Não está em área de localização diferenciada"

SEXO_OPCAO = {"F": "Feminino", "FEMININO": "Feminino",
              "M": "Masculino", "MASCULINO": "Masculino"}
COR_OPCAO = {"BRANCA": "Branca", "PRETA": "Preta", "PARDA": "Parda",
             "AMARELA": "Amarela", "INDIGENA": "Indígena",
             "INDÍGENA": "Indígena"}


def escolher_select_por_rotulo(page, rotulo, opcao):
    """Abre o mat-select do mat-form-field cujo rótulo contém `rotulo` e
    escolhe a opção de texto `opcao` (comparação sem acentos/maiúsculas).
    Os selects do cadastro têm id dinâmico do Angular, então a busca é pelo
    rótulo visível; cliques JS nativos como no restante do projeto."""
    aberto = page.evaluate(
        """(rotulo) => {
            const vis = el => el.offsetParent !== null;
            const ff = [...document.querySelectorAll('mat-form-field')]
                .filter(vis)
                .find(f => f.textContent.includes(rotulo));
            const sel = ff && ff.querySelector('mat-select');
            if (!sel) return false;
            sel.click();
            return true;
        }""",
        rotulo,
    )
    if not aberto:
        raise RuntimeError(f"Select com rótulo '{rotulo}' não encontrado.")
    page.wait_for_selector(".cdk-overlay-pane mat-option", timeout=8000)
    page.wait_for_timeout(300)

    escolhido = page.evaluate(
        """(opcao) => {
            const norm = t => t.normalize('NFD')
                .replace(/[\\u0300-\\u036f]/g, '').toUpperCase().trim();
            const ops = [...document.querySelectorAll('mat-option')];
            const alvo = ops.find(o => norm(o.textContent) === norm(opcao))
                || ops.find(o => norm(o.textContent).includes(norm(opcao)));
            if (!alvo) return false;
            alvo.click();
            return true;
        }""",
        opcao,
    )
    if not escolhido:
        raise RuntimeError(f"Opção '{opcao}' não encontrada em '{rotulo}'.")
    page.wait_for_timeout(600)


def preencher_input(page, seletor, valor, espera_ms=TIMEOUT):
    """Foca o input via JS nativo (clique simulado é engolido por overlay
    neste site — aprendizado nº 1 do projeto) e digita tecla a tecla pelo
    teclado, que é o que as máscaras dos campos exigem."""
    page.wait_for_selector(seletor, state="visible", timeout=espera_ms)
    ok = page.evaluate(
        """(sel) => {
            const el = document.querySelector(sel);
            if (!el || el.offsetParent === null) return false;
            el.focus();
            const setter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value').set;
            setter.call(el, '');
            el.dispatchEvent(new Event('input', {bubbles: true}));
            return true;
        }""",
        seletor,
    )
    if not ok:
        raise RuntimeError(f"Input '{seletor}' não focável.")
    page.keyboard.type(valor, delay=40)
    page.wait_for_timeout(200)


def preencher_por_placeholder(page, trecho, valor):
    """Digita no input cujo placeholder contém `trecho` (formulário de
    cadastro, onde os ids são dinâmicos do Angular)."""
    preencher_input(page, f"input[placeholder*='{trecho}']", valor)


def preencher_por_placeholder_js(page, trecho, valor):
    """Seta o valor via setter nativo + eventos do Angular. Obrigatório para
    campos com acentos: keyboard.type PERDIA os acentos (18/07/2026 — três
    filiações entraram no sistema como 'JOS', 'JOO' e 'NO CONSTA')."""
    seletor = f"input[placeholder*='{trecho}']"
    page.wait_for_selector(seletor, state="visible", timeout=TIMEOUT)
    ok = page.evaluate(
        """(args) => {
            const el = document.querySelector(args.sel);
            if (!el || el.offsetParent === null) return false;
            const setter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value').set;
            setter.call(el, args.valor);
            for (const ev of ['input', 'change', 'blur'])
                el.dispatchEvent(new Event(ev, {bubbles: true}));
            return true;
        }""",
        {"sel": seletor, "valor": valor},
    )
    if not ok:
        raise RuntimeError(f"Input '{seletor}' não encontrado.")
    page.wait_for_timeout(300)


SEM_FILIACAO = {"NAO CONSTA", "NAO DECLARADO", "NAO DECLARADA",
                "NAO INFORMADO", "SEM INFORMACAO", "-", "N/D", "ND", "X",
                "XX", "XXX"}


def filiacao_valida(valor):
    """Nome de filiação de verdade — marcadores tipo 'NÃO CONSTA' não são."""
    return bool(valor) and sem_acento(valor) not in SEM_FILIACAO


def responder_nao_pendentes(page):
    """Marca "Não" em todo grupo Sim/Não visível ainda sem resposta
    (no cadastro observado eram dois grupos, ambos respondidos "Não")."""
    return page.evaluate(
        """() => {
            const vis = el => el.offsetParent !== null;
            let n = 0;
            for (const g of [...document.querySelectorAll('mat-radio-group')]
                    .filter(vis)) {
                const radios = [...g.querySelectorAll('mat-radio-button')];
                const nao = radios.find(r => r.textContent.trim() === 'Não');
                if (!nao) continue;
                const jaMarcado = radios.some(r => {
                    const i = r.querySelector('input');
                    return i && i.checked;
                });
                if (jaMarcado) continue;
                const input = nao.querySelector('input');
                if (input) { input.click(); n++; }
            }
            return n;
        }""")


# Capitais são pares cidade+UF inequívocos; demais cidades precisam vir
# como "CIDADE-UF" na planilha (há homônimas em vários estados)
CAPITAL_UF = {
    "BRASILIA": "DF", "SAO PAULO": "SP", "RIO DE JANEIRO": "RJ",
    "BELO HORIZONTE": "MG", "SALVADOR": "BA", "FORTALEZA": "CE",
    "RECIFE": "PE", "MANAUS": "AM", "BELEM": "PA", "GOIANIA": "GO",
    "CURITIBA": "PR", "PORTO ALEGRE": "RS", "FLORIANOPOLIS": "SC",
    "VITORIA": "ES", "NATAL": "RN", "JOAO PESSOA": "PB", "MACEIO": "AL",
    "ARACAJU": "SE", "TERESINA": "PI", "SAO LUIS": "MA", "PALMAS": "TO",
    "CUIABA": "MT", "CAMPO GRANDE": "MS", "PORTO VELHO": "RO",
    "RIO BRANCO": "AC", "BOA VISTA": "RR", "MACAPA": "AP",
}


def sem_acento(texto):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", texto)
                   if unicodedata.category(c) != "Mn").upper().strip()


def abrir_filtros_detalhados(page):
    """Expande o painel "Filtros de pesquisa detalhada" e CONFERE que o
    campo de nome apareceu; o clique às vezes não expande de primeira
    (visto ao vivo em 18/07/2026), então tenta algumas vezes."""
    for _ in range(5):
        page.evaluate(
            """() => {
                const cab = [...document.querySelectorAll(
                    'mat-expansion-panel-header, mat-panel-title')]
                    .find(t => t.textContent.includes('Filtros de pesquisa'));
                if (cab) cab.click();
            }""")
        page.wait_for_timeout(900)
        aberto = page.evaluate(
            "() => { const el = document.getElementById('nomePessoaFisica');"
            " return !!el && el.offsetParent !== null; }")
        if aberto:
            return
    raise RuntimeError("Painel 'Filtros de pesquisa detalhada' não abriu.")


def pesquisar_por_nome(page, nome_busca, nascimento):
    """Executa a pesquisa detalhada por nome + data de nascimento.

    Retorna True se encontrou registro, False se não encontrou e None se o
    painel de filtros não estabilizou. O painel às vezes recolhe sozinho
    após re-render do Angular, então abre+digita em loop até os valores
    persistirem; os valores entram via setter nativo + eventos do Angular
    (teclado/clique simulados falham neste site)."""
    for tentativa in range(4):
        try:
            abrir_filtros_detalhados(page)
            page.wait_for_timeout(1200)
            valores = page.evaluate(
                """(dados) => {
                    const setar = (id, valor) => {
                        const el = document.getElementById(id);
                        if (!el || el.offsetParent === null) return '';
                        const setter = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value').set;
                        setter.call(el, valor);
                        for (const ev of ['input', 'change', 'blur'])
                            el.dispatchEvent(new Event(ev, {bubbles: true}));
                        return el.value;
                    };
                    return [setar('nomePessoaFisica', dados.nome),
                            setar('dataNascimento', dados.data)];
                }""",
                {"nome": nome_busca, "data": nascimento},
            )
            page.wait_for_timeout(600)
            if valores[0].strip() and valores[1].strip():
                page.evaluate(
                    "document.getElementById('botao-pesquisar').click()")
                return pesquisa_encontrou(page)
            print(f"(tentativa {tentativa + 1}: valores={valores!r})",
                  end=" ", flush=True)
        except (PWTimeout, RuntimeError) as e:
            print(f"(tentativa {tentativa + 1}: {type(e).__name__})",
                  end=" ", flush=True)
    return None


def naturalidade_uf_municipio(naturalidade):
    """"SAO JOSE DO BELMONTE-PE" -> ("PE", "SAO JOSE DO BELMONTE");
    capital sem UF -> UF conhecida; cidade ambígua sem UF -> None."""
    nat = naturalidade.strip()
    if "-" in nat:
        cidade, uf = nat.rsplit("-", 1)
        if len(uf.strip()) == 2:
            return uf.strip(), cidade.strip()
    cidade = sem_acento(nat)
    if cidade in CAPITAL_UF:
        return CAPITAL_UF[cidade], nat
    return None, None


def ir_para_pesquisa(page):
    page.goto(URL_BASE + "/aluno/pesquisar", wait_until="domcontentloaded")
    campo_cpf = page.get_by_placeholder("CPF", exact=True)
    try:
        campo_cpf.wait_for(state="visible", timeout=TIMEOUT)
    except PWTimeout:
        page.goto(URL_BASE + "/inicio", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        page.goto(URL_BASE + "/aluno/pesquisar", wait_until="domcontentloaded")
        campo_cpf.wait_for(state="visible", timeout=TIMEOUT)
    page.wait_for_timeout(1500)
    return campo_cpf


def passo(msg):
    """Marca no console em que etapa o fluxo está (diagnóstico de timeouts)."""
    print(f"[{msg}]", end=" ", flush=True)


def cadastrar_aluno(page, a):
    cpf, nome, nascimento = a["cpf"], a["nome"], a["nascimento"]

    passo("pesq_cpf")
    # 1) pesquisa por CPF — se agora existir, é caso pra v1 (vincular)
    campo_cpf = ir_para_pesquisa(page)
    campo_cpf.click()
    campo_cpf.press("Control+a")
    campo_cpf.press("Delete")
    campo_cpf.press_sequentially(cpf, delay=40)
    page.get_by_role("button", name="Pesquisar").click()
    if pesquisa_encontrou(page):
        return "ja_existe_rodar_v1"

    if not nascimento:
        return "sem_data_nascimento_na_planilha"
    if not SEXO_OPCAO.get(a["sexo"]):
        return f"sexo_invalido({a['sexo']})"
    if a.get("uf"):   # coluna UF da planilha tem prioridade
        uf_nasc, municipio_nasc = a["uf"], a["naturalidade"]
    else:
        uf_nasc, municipio_nasc = naturalidade_uf_municipio(a["naturalidade"])
    if not uf_nasc:
        return f"naturalidade_sem_uf({a['naturalidade']})"

    # 2) pesquisa detalhada por nome + nascimento (necessária pro botão
    #    "Cadastrar aluno(a)" aparecer quando não há resultados).
    # Com o CPF digitado o campo de nome não fica editável — recarrega a
    # tela de pesquisa antes de usar os filtros detalhados.
    passo("filtros")
    ir_para_pesquisa(page)
    page.wait_for_timeout(2000)   # SPA assenta após a recarga
    # O painel de filtros às vezes recolhe sozinho logo depois de aberto
    # (re-render do Angular pós-recarga); repete abrir+digitar até os dois
    # valores persistirem. Ids fixos validados no mapeamento ao vivo.
    passo("nome_data")
    # 2a) nome completo sem acentos (o validador do site recusa acentos —
    # "Informação inválida" — e a pesquisa nem roda)
    achou_nome = pesquisar_por_nome(page, sem_acento(nome), nascimento)
    if achou_nome is None:
        return "erro_painel_filtros_instavel"
    if achou_nome:
        return "achado_por_nome_verificar_manual"

    # 2b) proteção contra duplicidade: o último sobrenome pode ser nome de
    # casada; se a pessoa existir com o nome de solteira, revisar manualmente
    partes = nome.split()
    if len(partes) > 2:
        achou_curto = pesquisar_por_nome(
            page, sem_acento(" ".join(partes[:-1])), nascimento)
        if achou_curto:
            return "achado_sem_ultimo_sobrenome_verificar_manual"
        # refaz a busca com o nome completo para o cadastro herdar o certo
        if pesquisar_por_nome(page, sem_acento(nome), nascimento):
            return "achado_por_nome_verificar_manual"

    passo("botao_cadastrar")
    # 3) botão cadastrar -> formulário de dados cadastrais
    achou = page.evaluate(
        "() => { const b = document.getElementById('botao-cadastrar');"
        " if (!b) return false; b.click(); return true; }")
    if not achou:
        page.screenshot(path=os.path.join(
            PASTA_ERROS, f"sem_botao_{cpf}.png"), full_page=True)
        with open(os.path.join(PASTA_ERROS, f"sem_botao_{cpf}.txt"),
                  "w", encoding="utf-8") as f:
            f.write(page.locator("body").inner_text())
        return "botao_cadastrar_nao_apareceu"
    page.wait_for_selector("input[placeholder*='2 - Número do CPF']",
                           timeout=TIMEOUT)
    page.wait_for_timeout(1500)

    passo("form_dados")
    # 4) dados cadastrais — nome e nascimento vêm preenchidos da pesquisa
    preencher_por_placeholder(page, "2 - Número do CPF", cpf)
    # Ao digitar o CPF o site consulta a Receita Federal e AUTO-PREENCHE a
    # filiação 1 (descoberto em 18/07/2026: sobrescrever duplicava o nome na
    # 5b e o site recusava). Espera o autofill e só completa o que faltar.
    page.wait_for_timeout(4000)

    # o campo 3 herda o nome da busca (sem acentos); restaura o nome com
    # acentos da planilha — a menos que a Receita tenha posto outro nome
    val_nome = page.evaluate(
        "() => { const el = document.querySelector("
        "\"input[placeholder*='3 - Nome completo']\");"
        " return el ? el.value.trim() : ''; }")
    if val_nome != nome and sem_acento(val_nome) == sem_acento(nome):
        preencher_por_placeholder_js(page, "3 - Nome completo", nome)

    def ler_filiacoes():
        return page.evaluate(
            "() => ['5a', '5b'].map(p => { const el = document.querySelector("
            "`input[placeholder*='${p} - Nome completo']`);"
            " return el ? el.value.trim() : ''; })")

    val5a, val5b = ler_filiacoes()
    if not val5a and filiacao_valida(a["mae"]):
        preencher_por_placeholder_js(page, "5a - Nome completo da filiação 1",
                                     a["mae"])
        val5a, val5b = ler_filiacoes()
    candidatos_5b = [n for n in (a["pai"], a["mae"])
                     if filiacao_valida(n)
                     and sem_acento(n) != sem_acento(val5a)]
    if not val5b and candidatos_5b:
        preencher_por_placeholder_js(page, "5b - Nome completo da filiação 2",
                                     candidatos_5b[0])
    filiacoes = ler_filiacoes()
    print(f"(5a={filiacoes[0]!r} 5b={filiacoes[1]!r})", end=" ", flush=True)
    passo("selects")
    escolher_select_por_rotulo(page, "6 - Sexo", SEXO_OPCAO[a["sexo"]])
    escolher_select_por_rotulo(page, "7 - Cor/Raça",
                               COR_OPCAO.get(a["cor"], "Não declarada"))
    escolher_select_por_rotulo(page, "8 - Nacionalidade", "Brasileira")
    page.wait_for_timeout(1500)  # UF/Município de nascimento aparecem

    passo("uf_nasc")
    escolher_select_por_rotulo(page, "UF de nascimento", uf_nasc)
    page.wait_for_timeout(1500)  # municípios carregam após a UF
    escolher_select_por_rotulo(page, "Município de nascimento", municipio_nasc)

    passo("radios")
    respondidos = responder_nao_pendentes(page)
    print(f"(grupos Sim/Não = Não: {respondidos})", end=" ", flush=True)
    page.wait_for_timeout(800)

    passo("continuar")
    clicar_botao_js(page, "Continuar")
    page.wait_for_timeout(1500)
    corpo = page.locator("body").inner_text()
    if "Existem erros impeditivos" in corpo:
        # captura as linhas de erro do banner de validação
        detalhe = " | ".join(
            l.strip() for l in corpo.splitlines()
            if l.strip() and ("não permitido" in l or "obrigatório" in l
                              or l.strip().startswith(("5", "6", "7", "8",
                                                       "9", "1")))
        )[:200]
        return f"erro_validacao({detalhe})"
    clicar_botao_js(page, "Sim")           # diálogo de confirmação
    page.wait_for_timeout(2500)
    if "Campo obrigatório" in page.locator("body").inner_text():
        return "erro_campos_obrigatorios_cadastro"

    passo("residencia")
    # 5) residência (regra fixa: igual para todos os alunos)
    page.wait_for_selector("mat-form-field:has-text('16 - País')",
                           timeout=TIMEOUT)
    escolher_select_por_rotulo(page, "16 - País de residência", RESIDENCIA_PAIS)
    page.wait_for_timeout(1200)
    escolher_select_por_rotulo(page, "18 - UF", RESIDENCIA_UF)
    page.wait_for_timeout(1500)  # municípios carregam após a UF
    escolher_select_por_rotulo(page, "19 - Município", RESIDENCIA_MUNICIPIO)
    escolher_select_por_rotulo(page, "20 - Localização/Zona", RESIDENCIA_ZONA)
    escolher_select_por_rotulo(page, "21 - Localização diferenciada",
                               RESIDENCIA_DIFERENCIADA)

    passo("enviar")
    clicar_botao_js(page, "Enviar")
    page.wait_for_timeout(1500)
    clicar_botao_js(page, "Sim")           # confirmação do envio

    passo("vinculo")
    # 6) o sistema cai direto no formulário de vínculo
    try:
        page.wait_for_url("**/aluno/vincular**", timeout=30_000)
    except PWTimeout:
        return "cadastro_enviado_mas_nao_caiu_no_vinculo"
    page.wait_for_timeout(1500)
    try:
        clicar_botao_js(page, "Sim")   # diálogo de confirmação ao entrar
        page.wait_for_timeout(1200)    # no vínculo (visto na demonstração)
    except RuntimeError:
        pass                           # nem sempre aparece

    if a["turno"] not in TURNOS_VALIDOS:
        return f"cadastrado_sem_turma({a['turno']})"
    status_vinculo = preencher_formulario_vinculo(
        page, f"{a['ano']} {a['turno']}", cpf)
    return f"cadastrado_{status_vinculo}"   # ex.: cadastrado_vinculado


# ------------------------- PRINCIPAL -------------------------
def carregar_cadastro_feito():
    feitos = {}
    if os.path.exists(ARQ_CADASTRO):
        with open(ARQ_CADASTRO, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                feitos[f"{r['cpf']}|{r['ano']}"] = r["status"]
    return feitos


def gravar_cadastro(linhas):
    with open(ARQ_CADASTRO, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["cpf", "nome", "turno", "ano", "status"])
        w.writeheader()
        w.writerows(linhas)


def main():
    max_cadastros = int(sys.argv[1]) if len(sys.argv) > 1 else 10**9

    if not os.path.exists(ARQ_RESULTADO):
        sys.exit("censo_resultado.csv não existe — rode a v1 primeiro.")
    with open(ARQ_RESULTADO, encoding="utf-8") as f:
        nao_encontrados = {f"{r['cpf']}|{r['ano']}"
                           for r in csv.DictReader(f)
                           if r["status"] == "nao_encontrado"}

    feitos = carregar_cadastro_feito()
    ok = {"cadastrado_vinculado", "ja_existe_rodar_v1"}
    alunos_alvo = [a for a in carregar_alunos() if chave(a) in nao_encontrados]
    alvo = [a for a in alunos_alvo if feitos.get(chave(a)) not in ok]
    print(f"\n[plano] {len(nao_encontrados)} nao_encontrado no CSV da v1, "
          f"{len(alvo)} pendentes de cadastro"
          + (f" (limite desta execução: {max_cadastros})" if max_cadastros < 10**9
             else "") + ".\n")
    if not alvo:
        print("Nada a fazer.")
        return

    # o CSV guarda TODOS os alunos alvo (não só os desta execução), para
    # não perder statuses de rodadas anteriores ao regravar o arquivo
    resultado = [{"cpf": a["cpf"], "nome": a["nome"], "turno": a["turno"],
                  "ano": a["ano"], "status": feitos.get(chave(a), "pendente")}
                 for a in alunos_alvo]
    por_chave = {f"{r['cpf']}|{r['ano']}": r for r in resultado}

    os.makedirs(PASTA_ERROS, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PASTA_PERFIL, headless=False,
            args=["--start-maximized"], no_viewport=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(TIMEOUT)
        try:
            fazer_login(page)
            cadastrados = 0
            for i, a in enumerate(alvo, 1):
                if cadastrados >= max_cadastros:
                    break
                print(f"[{i}/{len(alvo)}] ({a['ano']}) {a['nome']} "
                      f"({a['cpf']}, {a['turno']})...", end=" ", flush=True)
                try:
                    status = cadastrar_aluno(page, a)
                except PWTimeout:
                    status = "erro_timeout"
                except Exception as e:
                    status = f"erro({type(e).__name__})"
                if status.startswith("erro"):
                    try:
                        page.screenshot(path=os.path.join(
                            PASTA_ERROS, f"cadastro_{a['cpf']}.png"))
                    except Exception:
                        pass
                if status.startswith(("cadastr", "erro")):
                    cadastrados += 1   # conta tentativas reais de cadastro
                print(status)
                por_chave[f"{a['cpf']}|{a['ano']}"]["status"] = status
                gravar_cadastro(resultado)
        finally:
            gravar_cadastro(resultado)
            resumo = {}
            for r in resultado:
                resumo[r["status"]] = resumo.get(r["status"], 0) + 1
            print(f"\nResultado salvo em: {ARQ_CADASTRO}\nResumo: {resumo}")
            ctx.close()


if __name__ == "__main__":
    main()
