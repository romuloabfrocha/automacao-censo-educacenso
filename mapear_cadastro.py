# -*- coding: utf-8 -*-
"""
Mapeia o formulário de CADASTRO de aluno do Educacenso (v2) — SEM enviar nada.

Pesquisa um CPF com status 'nao_encontrado' do censo_resultado.csv, clica no
botão de cadastrar e salva um relatório de todos os campos visíveis em
mapeamento_cadastro.txt + screenshot mapeamento_cadastro.png.
"""
import csv
import os
import sys

from playwright.sync_api import sync_playwright

from censo_automacao import (URL_BASE, PASTA, PASTA_PERFIL, TIMEOUT,
                             ARQ_RESULTADO, fazer_login)

ARQ_MAPA = os.path.join(PASTA, "mapeamento_cadastro.txt")
ARQ_PRINT = os.path.join(PASTA, "mapeamento_cadastro.png")

JS_DUMP = """() => {
    const vis = el => el.offsetParent !== null;
    const linhas = [];

    linhas.push('== BOTÕES VISÍVEIS ==');
    [...document.querySelectorAll('button')].filter(vis).forEach(b =>
        linhas.push('  [botao] ' + b.textContent.trim().slice(0, 80)));

    linhas.push('== INPUTS VISÍVEIS ==');
    [...document.querySelectorAll('input')].filter(vis).forEach(i =>
        linhas.push(`  [input] id=${i.id || '-'} name=${i.name || '-'} `
            + `type=${i.type} placeholder=${i.placeholder || '-'}`));

    linhas.push('== MAT-SELECTS VISÍVEIS ==');
    [...document.querySelectorAll('mat-select')].filter(vis).forEach(s => {
        const ff = s.closest('mat-form-field');
        const rotulo = ff ? ff.textContent.trim().slice(0, 80) : '?';
        linhas.push(`  [select] id=${s.id || '-'} rotulo=${rotulo}`);
    });

    linhas.push('== RADIOS/CHECKBOXES VISÍVEIS ==');
    [...document.querySelectorAll('mat-radio-button, mat-checkbox')]
        .filter(vis).forEach(r =>
            linhas.push('  [radio/check] ' + r.textContent.trim().slice(0, 80)));

    linhas.push('== RÓTULOS (mat-form-field) ==');
    [...document.querySelectorAll('mat-form-field')].filter(vis).forEach(f =>
        linhas.push('  [campo] ' + f.textContent.trim().slice(0, 100)));

    return linhas.join('\\n');
}"""


def cpf_nao_encontrado():
    with open(ARQ_RESULTADO, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["status"] == "nao_encontrado":
                return r["cpf"], r["nome"]
    sys.exit("Nenhum aluno com status nao_encontrado no CSV.")


def main():
    cpf, nome = cpf_nao_encontrado()
    print(f"[mapa] Usando CPF de {nome} ({cpf}) — nada será enviado.")

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PASTA_PERFIL, headless=False,
            args=["--start-maximized"], no_viewport=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(TIMEOUT)
        try:
            fazer_login(page)

            page.goto(URL_BASE + "/aluno/pesquisar", wait_until="domcontentloaded")
            campo = page.get_by_placeholder("CPF", exact=True)
            campo.wait_for(state="visible", timeout=TIMEOUT)
            page.wait_for_timeout(1500)
            campo.click()
            campo.press_sequentially(cpf, delay=40)
            page.get_by_role("button", name="Pesquisar").click()
            page.wait_for_timeout(3000)

            relatorio = ["===== TELA DE RESULTADO DA PESQUISA =====",
                         page.evaluate(JS_DUMP)]

            # procura o botão de cadastrar (texto pode variar)
            achou = page.evaluate(
                """() => {
                    const vis = el => el.offsetParent !== null;
                    const alvo = [...document.querySelectorAll('button, a')]
                        .filter(vis)
                        .find(b => /cadastrar/i.test(b.textContent));
                    if (!alvo) return null;
                    const txt = alvo.textContent.trim();
                    alvo.click();
                    return txt;
                }""")
            if achou:
                print(f"[mapa] Cliquei em: {achou!r}")
                page.wait_for_timeout(4000)
                relatorio += [f"\n===== FORMULÁRIO DE CADASTRO (botão: {achou}) "
                              f"=====\nURL: {page.url}\n", page.evaluate(JS_DUMP)]
            else:
                print("[mapa] Nenhum botão 'Cadastrar' encontrado — veja o dump.")

            page.screenshot(path=ARQ_PRINT, full_page=True)
            with open(ARQ_MAPA, "w", encoding="utf-8") as f:
                f.write("\n".join(relatorio))
            print(f"[mapa] Relatório: {ARQ_MAPA}\n[mapa] Print: {ARQ_PRINT}")
        finally:
            ctx.close()


if __name__ == "__main__":
    main()
