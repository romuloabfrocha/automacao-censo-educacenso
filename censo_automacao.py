# -*- coding: utf-8 -*-
"""
Automação Educacenso (v2 - Playwright) — vincula alunos a turmas pela planilha.

Fluxo por aluno:
  Pesquisar CPF -> (já tem vínculo? pula) -> Vincular -> turma conforme ABA+TURNO
  -> carga 400 -> "Não recebe" -> Transporte: Utiliza/Estadual/Rodoviário/Ônibus
  -> Enviar

Instalação (uma vez só):
  pip install -r requirements.txt
  playwright install chromium
  copiar .env.example -> .env e preencher as credenciais

Rodar:
  python censo_automacao.py

- O progresso fica em censo_resultado.csv (retomável: rode de novo que continua).
- A sessão de login fica salva na pasta "perfil_chrome" (login só na 1ª vez).
- Se um aluno der erro, o script salva a tela em "erros/<cpf>.png" e continua.
"""

import csv
import os
import sys

import openpyxl
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ========================= CONFIGURAÇÃO =========================
# Credenciais e dados da escola ficam no arquivo .env (nunca no código!).
# Copie .env.example para .env e preencha os seus valores.
from dotenv import load_dotenv
load_dotenv()

LOGIN_CPF = os.environ.get("CENSO_LOGIN_CPF", "")
LOGIN_SENHA = os.environ.get("CENSO_LOGIN_SENHA", "")

PLANILHA = os.environ.get("CENSO_PLANILHA", "DADOSCENSO2026.xlsx")

# aba (trecho do nome) -> ano usado para achar a turma no dropdown
# a opção do dropdown precisa conter "<ano> <turno>":
#   "2025 MATUTINO" casa com "38195337 - 2º 2025 MATUTINO"
#   "2026 NOTURNO"  casa com "1º 2026 NOTURNO"
ABAS = {
    "2025": "2025",
    "2026": "2026",
}
TURNOS_VALIDOS = {"MATUTINO", "INTEGRAL", "NOTURNO"}  # EAD -> sem_turma

CODIGO_ESCOLA = os.environ.get("CENSO_CODIGO_ESCOLA", "")
CARGA_HORARIA = os.environ.get("CENSO_CARGA_HORARIA", "400")

URL_BASE = "https://educacenso.inep.gov.br"

PASTA = os.path.dirname(os.path.abspath(__file__))
ARQ_RESULTADO = os.path.join(PASTA, "censo_resultado.csv")
PASTA_PERFIL = os.path.join(PASTA, "perfil_chrome")
PASTA_ERROS = os.path.join(PASTA, "erros")

TIMEOUT = 30_000  # ms
# ================================================================


def escolher_mat_select_js(page, select_id, texto_opcao, debug_tag=None):
    """
    Abre um mat-select (pelo id fixo no HTML) e escolhe a mat-option cujo
    texto contém `texto_opcao`, usando cliques JavaScript NATIVOS
    (elemento.click()) em vez de cliques de mouse simulados do Playwright.

    Descoberto por inspeção ao vivo do site: o clique de mouse do Playwright
    (que move o cursor até a coordenada exata na tela) estava sendo
    engolido por outro elemento sobreposto naquela posição — o painel do
    dropdown nunca abria. .click() nativo via JS ignora esse problema de
    sobreposição por completo (confirmado funcionando na inspeção manual).
    """
    aberto = page.evaluate(
        "id => { const el = document.getElementById(id);"
        " if (!el) return false; el.click(); return true; }",
        select_id,
    )
    if not aberto:
        raise RuntimeError(f"Select #{select_id} não encontrado na página.")

    page.wait_for_selector(".cdk-overlay-pane mat-option", timeout=8000)
    page.wait_for_timeout(300)

    escolhido = page.evaluate(
        """(texto) => {
            const opcoes = [...document.querySelectorAll('mat-option')];
            const alvo = opcoes.find(o => o.textContent.includes(texto));
            if (!alvo) return false;
            alvo.click();
            return true;
        }""",
        texto_opcao,
    )
    if not escolhido:
        if debug_tag:
            try:
                disponiveis = page.evaluate(
                    "[...document.querySelectorAll('mat-option')]"
                    ".map(o => o.textContent.trim())")
                os.makedirs(PASTA_ERROS, exist_ok=True)
                with open(os.path.join(PASTA_ERROS, f"{debug_tag}_opcoes.txt"),
                          "w", encoding="utf-8") as f:
                    f.write(f"Procurando: {texto_opcao!r}\nOpções disponíveis:\n"
                            + "\n".join(disponiveis))
            except Exception:
                pass
        raise RuntimeError(f"Opção '{texto_opcao}' não encontrada em #{select_id}.")
    page.wait_for_timeout(500)


def clicar_radio_ou_checkbox_js(page, texto):
    """
    Clique JS nativo para mat-radio-button/mat-checkbox. Dois detalhes
    descobertos em teste ao vivo no sistema (17/07/2026):
    1. O clique precisa ser no <input> INTERNO do componente — clicar no
       elemento externo não marca nada.
    2. A página tem um painel de temas invisível cheio de radios — filtrar
       por `offsetParent !== null` garante que só elementos visíveis contam.
    """
    ok = page.evaluate(
        """(texto) => {
            const candidatos = [...document.querySelectorAll(
                'mat-radio-button, mat-checkbox')]
                .filter(el => el.offsetParent !== null);
            const alvo = candidatos.find(el =>
                el.textContent.trim() === texto
                || el.textContent.trim().includes(texto));
            if (!alvo) return false;
            const input = alvo.querySelector('input');
            if (!input) return false;
            input.click();
            return true;
        }""",
        texto,
    )
    if not ok:
        raise RuntimeError(f"Rádio/checkbox '{texto}' não encontrado na página.")


def clicar_botao_js(page, texto_exato):
    """Clique JS nativo num <button> VISÍVEL pelo texto exato."""
    ok = page.evaluate(
        """(texto) => {
            const botoes = [...document.querySelectorAll('button')]
                .filter(b => b.offsetParent !== null);
            const alvo = botoes.find(b => b.textContent.trim() === texto);
            if (!alvo) return false;
            alvo.click();
            return true;
        }""",
        texto_exato,
    )
    if not ok:
        raise RuntimeError(f"Botão '{texto_exato}' não encontrado na página.")


def pesquisa_encontrou(page, espera_ms=15_000):
    """Espera o resultado da pesquisa de aluno aparecer; True se achou registro.

    A espera fixa de 3s usada antes gerava falsos "nao_encontrado" quando o
    site demorava a responder (confirmado em 18/07/2026: 6 alunos marcados
    como nao_encontrado na véspera existiam no sistema)."""
    try:
        page.wait_for_function(
            "() => /Foi encontrado|Foram encontrados/"
            ".test(document.body.innerText)",
            timeout=espera_ms)
        return True
    except PWTimeout:
        return False


# ------------------------- PLANILHA -------------------------
def _celula(row, idx, i):
    """Valor da coluna de cabeçalho `i` (ou "" se ausente); datas viram dd/mm/aaaa."""
    if i not in idx or idx[i] >= len(row) or row[idx[i]] is None:
        return ""
    v = row[idx[i]]
    if hasattr(v, "strftime"):
        return v.strftime("%d/%m/%Y")
    return str(v).strip()


def carregar_alunos():
    wb = openpyxl.load_workbook(PLANILHA)
    alunos = []
    for trecho, ano in ABAS.items():
        nomes = [s for s in wb.sheetnames if trecho in s]
        if not nomes:
            print(f"[aviso] Nenhuma aba contendo '{trecho}' — pulando.")
            continue
        ws = wb[nomes[0]]
        cab = [str(c.value).strip().upper() if c.value else "" for c in ws[1]]
        idx = {nome: i for i, nome in enumerate(cab)}
        for obrig in ("CPF", "NOME", "TURNO"):
            if obrig not in idx:
                sys.exit(f"[planilha] Aba {ws.title!r} sem a coluna {obrig!r}.")
        qtd = 0
        for row in ws.iter_rows(min_row=2, values_only=True):
            cpf_bruto = _celula(row, idx, "CPF")
            if not cpf_bruto:
                continue
            cpf = cpf_bruto.replace(".", "").replace("-", "").zfill(11)
            alunos.append({
                "cpf": cpf,
                "nome": _celula(row, idx, "NOME"),
                "turno": _celula(row, idx, "TURNO").upper(),
                "ano": ano, "aba": ws.title,
                # campos extras usados no cadastro de aluno novo (v2)
                "nascimento": _celula(row, idx, "DT. NASCIMENTO"),
                "mae": _celula(row, idx, "MAE"),
                "pai": _celula(row, idx, "PAI"),
                "sexo": _celula(row, idx, "SEXO").upper(),
                "cor": _celula(row, idx, "COR").upper(),
                "naturalidade": _celula(row, idx, "NATURALIDADE").upper(),
            })
            qtd += 1
        print(f"[planilha] Aba {ws.title!r}: {qtd} alunos -> turmas {ano}")
    return alunos


def chave(a):
    return f"{a['cpf']}|{a['ano']}"


def carregar_resultado():
    feitos = {}
    if os.path.exists(ARQ_RESULTADO):
        with open(ARQ_RESULTADO, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                feitos[f"{r['cpf']}|{r.get('ano', '')}"] = r["status"]
    return feitos


def gravar_resultado(linhas):
    with open(ARQ_RESULTADO, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["cpf", "nome", "turno", "ano", "aba", "status"])
        w.writeheader()
        w.writerows(linhas)


# ------------------------- LOGIN -------------------------
def esta_logado(page):
    try:
        return page.get_by_text("Escola selecionada").count() > 0
    except Exception:
        return False


def fazer_login(page):
    # Vai direto numa rota protegida: se não houver sessão, o próprio site
    # redireciona sozinho para a tela de login do SSO (gerando um state/nonce
    # novo e válido). Isso evita depender dos botões "Acesse"/"Efetuar o
    # Login" da home, que às vezes ficam escondidos/duplicados no HTML.
    page.goto(URL_BASE + "/matricula-inicial/inicio", wait_until="domcontentloaded")
    page.wait_for_timeout(3000)

    if esta_logado(page):
        print("[login] Sessão já ativa (perfil salvo).")
        return

    # espera cair no SSO; se não cair de primeira, tenta mais uma vez
    try:
        page.wait_for_url("**acesso.inep.gov.br**", timeout=15_000)
    except PWTimeout:
        page.goto(URL_BASE + "/", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        page.goto(URL_BASE + "/matricula-inicial/inicio", wait_until="domcontentloaded")
        try:
            page.wait_for_url("**acesso.inep.gov.br**", timeout=20_000)
        except PWTimeout:
            pass

    if "acesso.inep.gov.br" in page.url:
        print("[login] Preenchendo credenciais no SSO...")
        cpf_input = page.locator("input[type='tel'], #username, input[name='username']").first
        cpf_input.click()
        cpf_input.press_sequentially(LOGIN_CPF, delay=50)

        senha_input = page.locator("input[type='password']").first
        senha_input.click()
        senha_input.press_sequentially(LOGIN_SENHA, delay=50)
        senha_input.press("Tab")           # tira o foco -> habilita o botão
        page.wait_for_timeout(1500)

        botao = page.get_by_role("button", name="Acessar")
        try:
            botao.click(timeout=8000)
        except Exception:
            senha_input.press("Enter")     # alternativa: submete com Enter

    # aguarda o Educacenso logado
    try:
        page.wait_for_selector("text=Escola selecionada", timeout=90_000)
        print("[login] OK.")
    except PWTimeout:
        print("\n[login] Não consegui logar sozinho.")
        print("  -> Faça o login MANUALMENTE na janela do Chrome.")
        input("  -> Quando estiver dentro do sistema, aperte ENTER aqui... ")
        if not esta_logado(page):
            sys.exit("[login] Ainda não logado. Rode novamente.")
        print("[login] OK (manual).")
    page.wait_for_timeout(2000)


# --------------------- FLUXO POR ALUNO ---------------------
def processar_aluno(page, aluno):
    cpf, turno, ano = aluno["cpf"], aluno["turno"], aluno["ano"]

    # 1) tela de pesquisa
    page.goto(URL_BASE + "/aluno/pesquisar", wait_until="domcontentloaded")
    campo_cpf = page.get_by_placeholder("CPF", exact=True)
    try:
        campo_cpf.wait_for(state="visible", timeout=TIMEOUT)
    except PWTimeout:
        # a rota direta às vezes trava no spinner; recarrega pela home
        page.goto(URL_BASE + "/inicio", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        page.goto(URL_BASE + "/aluno/pesquisar", wait_until="domcontentloaded")
        campo_cpf.wait_for(state="visible", timeout=TIMEOUT)
    page.wait_for_timeout(1500)  # Angular termina de montar a tela

    # 2) digita o CPF (com máscara: tecla a tecla)
    campo_cpf.click()
    campo_cpf.press("Control+a")
    campo_cpf.press("Delete")
    campo_cpf.press_sequentially(cpf, delay=40)

    # 3) pesquisar
    page.get_by_role("button", name="Pesquisar").click()
    if not pesquisa_encontrou(page):
        return "nao_encontrado"
    page.wait_for_timeout(500)
    corpo = page.locator("body").inner_text()
    if "Pessoa possui vínculo de aluno nesta escola" in corpo:
        return "ja_vinculado"

    if turno not in TURNOS_VALIDOS:
        return f"sem_turma({turno})"
    turma_chave = f"{ano} {turno}"

    # 4) botão vincular (ícone how_to_reg)
    page.locator("button:has(mat-icon:text('how_to_reg'))").first.click()

    # 5) formulário de vínculo
    return preencher_formulario_vinculo(page, turma_chave, cpf)


def preencher_formulario_vinculo(page, turma_chave, cpf=""):
    """Preenche e envia o formulário "Vincular aluno" (usado tanto no fluxo
    de vínculo da v1 quanto após o cadastro de aluno novo na v2, quando o
    sistema cai direto nesta tela)."""
    campo_escola = page.get_by_placeholder("Código da escola")
    campo_escola.wait_for(state="visible", timeout=TIMEOUT)
    page.wait_for_timeout(1000)
    # o sistema costuma preencher a escola sozinho; só digita se necessário
    if campo_escola.input_value().strip() != CODIGO_ESCOLA:
        campo_escola.click()
        campo_escola.press("Control+a")
        campo_escola.press("Delete")
        campo_escola.press_sequentially(CODIGO_ESCOLA, delay=40)
    page.wait_for_timeout(1500)

    # dropdown de turma (id fixo "turma" no HTML) — clique JS nativo
    escolher_mat_select_js(page, "turma", turma_chave, debug_tag=f"{cpf}_turma")
    page.wait_for_timeout(1500)  # campos extras aparecem

    # carga horária: valor setado via JS nativo com eventos do Angular
    # (método validado ao vivo no sistema em 17/07/2026)
    ok_carga = page.evaluate(
        """(valor) => {
            const campos = [...document.querySelectorAll('mat-form-field')];
            const alvo = campos.find(c =>
                c.textContent.includes('Carga horária integralizada'));
            if (!alvo) return false;
            const input = alvo.querySelector('input');
            if (!input) return false;
            const setter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value').set;
            setter.call(input, valor);
            input.dispatchEvent(new Event('input', {bubbles: true}));
            input.dispatchEvent(new Event('change', {bubbles: true}));
            input.dispatchEvent(new Event('blur', {bubbles: true}));
            return true;
        }""",
        CARGA_HORARIA,
    )
    if not ok_carga:
        raise RuntimeError("Campo de carga horária não encontrado.")

    # atendimento hospitalar/domiciliar -> Não recebe
    # (id fixo "codigoEspacoEscolarizacao" no HTML) — clique JS nativo
    escolher_mat_select_js(page, "codigoEspacoEscolarizacao", "Não recebe",
                            debug_tag=f"{cpf}_hospitalar")

    # transporte -> Utiliza (radio/checkbox têm id dinâmico do Angular;
    # busca pelo texto visível via clique JS nativo)
    clicar_radio_ou_checkbox_js(page, "Utiliza")
    page.wait_for_timeout(1500)   # Angular demora a renderizar o campo 6a
    # poder público -> Estadual
    clicar_radio_ou_checkbox_js(page, "Estadual")
    page.wait_for_timeout(1500)   # idem para o campo 6b
    # tipo de veículo -> Rodoviário
    clicar_radio_ou_checkbox_js(page, "Rodoviário")
    page.wait_for_timeout(1500)   # idem para as sub-opções
    # sub-opção -> Ônibus
    clicar_radio_ou_checkbox_js(page, "Ônibus")
    page.wait_for_timeout(800)

    # 6) enviar
    clicar_botao_js(page, "Enviar")
    page.wait_for_timeout(3500)

    corpo = page.locator("body").inner_text()
    if "Campo obrigatório" in corpo:
        return "erro_campos_obrigatorios"
    return "vinculado"


# ------------------------- PRINCIPAL -------------------------
def main():
    if not (LOGIN_CPF and LOGIN_SENHA and CODIGO_ESCOLA):
        sys.exit("[config] Faltam dados no .env — copie .env.example para .env "
                 "e preencha CENSO_LOGIN_CPF, CENSO_LOGIN_SENHA e "
                 "CENSO_CODIGO_ESCOLA.")
    os.makedirs(PASTA_ERROS, exist_ok=True)
    alunos = carregar_alunos()
    feitos = carregar_resultado()

    ok = {"vinculado", "ja_vinculado"}
    pendentes = [a for a in alunos if feitos.get(chave(a)) not in ok]
    print(f"\n[plano] {len(alunos)} alunos no total, {len(pendentes)} pendentes.\n")
    if not pendentes:
        print("Nada a fazer.")
        return

    resultado = [{"cpf": a["cpf"], "nome": a["nome"], "turno": a["turno"],
                  "ano": a["ano"], "aba": a["aba"],
                  "status": feitos.get(chave(a), "pendente")} for a in alunos]
    por_chave = {f"{r['cpf']}|{r['ano']}": r for r in resultado}

    with sync_playwright() as p:
        # perfil persistente: o login fica salvo entre execuções
        ctx = p.chromium.launch_persistent_context(
            PASTA_PERFIL, headless=False,
            args=["--start-maximized"], no_viewport=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(TIMEOUT)

        try:
            fazer_login(page)

            for i, a in enumerate(pendentes, 1):
                print(f"[{i}/{len(pendentes)}] ({a['ano']}) {a['nome']} "
                      f"({a['cpf']}, {a['turno']})...", end=" ", flush=True)
                try:
                    status = processar_aluno(page, a)
                except PWTimeout:
                    status = "erro_timeout"
                    page.screenshot(path=os.path.join(PASTA_ERROS, f"{a['cpf']}.png"))
                except Exception as e:
                    status = f"erro({type(e).__name__})"
                    try:
                        page.screenshot(path=os.path.join(PASTA_ERROS, f"{a['cpf']}.png"))
                    except Exception:
                        pass
                print(status)
                por_chave[chave(a)]["status"] = status
                gravar_resultado(resultado)

        finally:
            gravar_resultado(resultado)
            print(f"\nResultado salvo em: {ARQ_RESULTADO}")
            resumo = {}
            for r in resultado:
                resumo[r["status"]] = resumo.get(r["status"], 0) + 1
            print("Resumo:", resumo)
            print(f"Screenshots de erros (se houver): {PASTA_ERROS}")
            ctx.close()


if __name__ == "__main__":
    main()
