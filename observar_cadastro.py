# -*- coding: utf-8 -*-
"""
Modo observador (v2): abre o Educacenso logado e GRAVA os passos que o usuário
faz manualmente (cliques, campos preenchidos, mudanças de tela) em
observacao_cadastro.log. Não automatiza nada — só assiste.

Uso: python observar_cadastro.py  ->  faça o cadastro na janela  ->  feche a
janela do Chrome para encerrar e salvar o log.
"""
import datetime
import os

from playwright.sync_api import sync_playwright

from censo_automacao import URL_BASE, PASTA, PASTA_PERFIL, TIMEOUT, fazer_login
from mapear_cadastro import JS_DUMP

ARQ_LOG = os.path.join(PASTA, "observacao_cadastro.log")

JS_RECORDER = """(() => {
  if (window.__recInstalado) return;
  window.__recInstalado = true;
  window.__eventos = window.__eventos || [];
  const info = el => {
    if (!el || !el.closest) return '?';
    const ff = el.closest('mat-form-field');
    const rotulo = ff ? ff.textContent.trim().slice(0, 60) : '';
    const comp = el.closest('button, a, mat-option, mat-radio-button,'
      + ' mat-checkbox, mat-select, mat-slide-toggle') || el;
    const txt = (comp.textContent || '').trim().slice(0, 60);
    return `<${comp.tagName.toLowerCase()}> id=${comp.id || '-'} `
      + `texto="${txt}" rotulo="${rotulo}"`;
  };
  document.addEventListener('click',
    e => window.__eventos.push('[click] ' + info(e.target)), true);
  document.addEventListener('change', e => {
    const t = e.target;
    if (t && t.tagName === 'INPUT')
      window.__eventos.push(`[valor] id=${t.id || '-'} `
        + `placeholder="${t.placeholder || ''}" type=${t.type} `
        + `valor="${t.value}"`);
  }, true);
})();"""


def log(f, msg):
    linha = f"{datetime.datetime.now():%H:%M:%S} {msg}"
    print(linha, flush=True)
    f.write(linha + "\n")
    f.flush()


def main():
    with open(ARQ_LOG, "w", encoding="utf-8") as f, sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PASTA_PERFIL, headless=False,
            args=["--start-maximized"], no_viewport=True)
        ctx.add_init_script(JS_RECORDER)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(TIMEOUT)

        fazer_login(page)
        page.goto(URL_BASE + "/aluno/pesquisar", wait_until="domcontentloaded")
        page.evaluate(JS_RECORDER)
        log(f, "== OBSERVANDO: faça o cadastro manualmente; feche a janela "
               "do Chrome quando terminar ==")

        url_atual = ""
        try:
            while True:
                eventos = page.evaluate(
                    "() => { const e = window.__eventos || [];"
                    " window.__eventos = []; return e; }")
                if page.url != url_atual:
                    url_atual = page.url
                    log(f, f"[tela] {url_atual}")
                    log(f, "----- campos visíveis -----\n"
                           + page.evaluate(JS_DUMP)
                           + "\n---------------------------")
                for e in eventos:
                    log(f, e)
                page.evaluate(JS_RECORDER)  # reinstala após reload/navegação
                page.wait_for_timeout(1200)
        except Exception:
            log(f, "== Janela fechada — fim da observação ==")


if __name__ == "__main__":
    main()
