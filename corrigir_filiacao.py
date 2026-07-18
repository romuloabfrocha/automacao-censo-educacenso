# -*- coding: utf-8 -*-
"""
Correção pontual (18/07/2026): conserta a filiação 2 de três alunos que
foram cadastrados com o texto mutilado pelo bug de acentos do keyboard.type
(corrigido em cadastro_automacao.py). Fluxo: pesquisar CPF -> botão editar
-> ajustar 5b -> Continuar -> Sim -> Enviar -> Sim.

5b vazia exige o checkbox "Não declarado" (o site recusa vazio puro);
usuário aprovou marcar o checkbox em 18/07/2026.
"""
import os

from playwright.sync_api import sync_playwright

from censo_automacao import (PASTA_PERFIL, PASTA_ERROS, TIMEOUT,
                             fazer_login, pesquisa_encontrou, clicar_botao_js)
from cadastro_automacao import ir_para_pesquisa, preencher_por_placeholder_js

CORRECOES = [
    # JAAZIEL e CLAUDIO já corrigidos em 18/07/2026
    ("81337256404", "SUZANA", ""),
]


def corrigir(page, cpf, apelido, novo_5b):
    campo_cpf = ir_para_pesquisa(page)
    campo_cpf.click()
    campo_cpf.press("Control+a")
    campo_cpf.press("Delete")
    campo_cpf.press_sequentially(cpf, delay=40)
    page.get_by_role("button", name="Pesquisar").click()
    if not pesquisa_encontrou(page):
        return "nao_encontrado"
    page.wait_for_timeout(1000)

    page.locator("button:has(mat-icon:text('edit'))").first.click()
    page.wait_for_selector("input[placeholder*='2 - Número do CPF']",
                           timeout=TIMEOUT)
    page.wait_for_timeout(2000)

    antes = page.evaluate(
        "() => { const el = document.querySelector("
        "\"input[placeholder*='5b - Nome completo']\");"
        " return el ? el.value : null; }")
    preencher_por_placeholder_js(page, "5b - Nome completo da filiação 2",
                                 novo_5b)
    print(f"(5b: {antes!r} -> {novo_5b!r})", end=" ", flush=True)
    if not novo_5b:
        # 5b vazia exige o checkbox "Não declarado" (índice 1 = filiação 2)
        marcado = page.evaluate(
            """() => {
                const caixas = [...document.querySelectorAll('mat-checkbox')]
                    .filter(el => el.offsetParent !== null
                        && el.textContent.trim() === 'Não declarado');
                const alvo = caixas[1];
                if (!alvo) return false;
                const input = alvo.querySelector('input');
                if (!input) return false;
                if (!input.checked) input.click();
                return true;
            }""")
        print(f"(Não declarado: {marcado})", end=" ", flush=True)
        page.wait_for_timeout(500)

    clicar_botao_js(page, "Continuar")
    page.wait_for_timeout(1500)
    corpo = page.locator("body").inner_text()
    if "Existem erros impeditivos" in corpo:
        detalhe = " | ".join(l.strip() for l in corpo.splitlines()
                             if "permitido" in l or "obrigatório" in l)[:120]
        return f"erro_validacao({detalhe})"
    try:
        clicar_botao_js(page, "Sim")
        page.wait_for_timeout(2500)
    except RuntimeError:
        pass

    clicar_botao_js(page, "Enviar")
    page.wait_for_timeout(1500)
    try:
        clicar_botao_js(page, "Sim")
        page.wait_for_timeout(2500)
    except RuntimeError:
        pass

    corpo = page.locator("body").inner_text()
    if "Existem erros impeditivos" in corpo or "Campo obrigatório" in corpo:
        return "erro_apos_enviar"
    return "corrigido"


def main():
    os.makedirs(PASTA_ERROS, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PASTA_PERFIL, headless=False,
            args=["--start-maximized"], no_viewport=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(TIMEOUT)
        try:
            fazer_login(page)
            for cpf, apelido, novo in CORRECOES:
                print(f"{apelido} ({cpf})...", end=" ", flush=True)
                try:
                    status = corrigir(page, cpf, apelido, novo)
                except Exception as e:
                    status = f"erro({type(e).__name__})"
                    try:
                        page.screenshot(path=os.path.join(
                            PASTA_ERROS, f"correcao_{cpf}.png"), full_page=True)
                    except Exception:
                        pass
                print(status)
        finally:
            ctx.close()


if __name__ == "__main__":
    main()
