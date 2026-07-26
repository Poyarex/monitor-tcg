"""
Monitor TCG — versao de arquivo unico, pronta para hospedar na nuvem
(Render, Railway, etc). Mesma logica da versao local, so que tudo num
arquivo so (mais facil de colar direto no site do GitHub pelo celular,
sem precisar enviar pasta nenhuma).

Rodar localmente: python app.py
Rodar em producao: gunicorn app:app

Protegido por login (variavel de ambiente ACESSOS, uma senha por pessoa)
porque, ao contrario da versao local, esta fica num endereco publico na
internet.
"""
import base64
import json
import os
import smtplib
import threading
import uuid
from datetime import datetime, timezone
from email.mime.text import MIMEText
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from flask import Flask, Response, flash, redirect, render_template, request, session, url_for
from jinja2 import DictLoader

# ============================================================================
# TELAS, VISUAL E ICONE — embutidos como texto (gerados automaticamente
# logo abaixo desta secao; nao precisa mexer aqui)
# ============================================================================

TEMPLATES = {'base.html': '<!DOCTYPE html>\n<html lang="pt-BR">\n<head>\n  <meta charset="utf-8">\n  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n  <title>{% block titulo %}Monitor TCG{% endblock %}</title>\n\n  <!-- identidade de app no celular (iPhone e Android) -->\n  <meta name="theme-color" content="#14231f">\n  <link rel="manifest" href="/manifest.json">\n  <link rel="apple-touch-icon" href="/icone.png">\n  <meta name="apple-mobile-web-app-capable" content="yes">\n  <meta name="mobile-web-app-capable" content="yes">\n  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">\n  <meta name="apple-mobile-web-app-title" content="Monitor TCG">\n\n  <link rel="preconnect" href="https://fonts.googleapis.com">\n  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n  <link href="https://fonts.googleapis.com/css2?family=Antonio:wght@400;600;700&family=Archivo:wght@400;500;600&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">\n  <link rel="stylesheet" href="/estilo.css">\n</head>\n<body>\n\n  <header class="topo">\n    <div class="topo-interno">\n      <a class="marca" href="{{ url_for(\'painel\') }}">\n        <span class="marca-nome">Monitor TCG</span>\n        <span class="marca-sub">estoque e preço, de olho por você</span>\n      </a>\n\n      <nav class="menu">\n        <a href="{{ url_for(\'painel\') }}" {% if request.endpoint == \'painel\' %}class="ativo"{% endif %}>Painel</a>\n        <a href="{{ url_for(\'novo_produto\') }}" {% if request.endpoint == \'novo_produto\' %}class="ativo"{% endif %}>Adicionar</a>\n        <a href="{{ url_for(\'ajustes\') }}" {% if request.endpoint == \'ajustes\' %}class="ativo"{% endif %}>Ajustes</a>\n        <a href="{{ url_for(\'sair\') }}" class="menu-sair">{{ session.nome }} · sair</a>\n      </nav>\n    </div>\n  </header>\n\n  <main class="conteudo">\n    {% with mensagens = get_flashed_messages(with_categories=true) %}\n      {% if mensagens %}\n        {% for categoria, texto in mensagens %}\n          <p class="recado recado-{{ categoria }}">{{ texto }}</p>\n        {% endfor %}\n      {% endif %}\n    {% endwith %}\n\n    {% block conteudo %}{% endblock %}\n  </main>\n\n  <footer class="rodape">\n    <span>Monitor TCG · acesso restrito</span>\n  </footer>\n\n  <script>\n    // Registra a peça que permite "Adicionar à Tela de Início" no celular.\n    if ("serviceWorker" in navigator) {\n      navigator.serviceWorker.register("/sw.js");\n    }\n  </script>\n\n</body>\n</html>\n', 'painel.html': '{% extends "base.html" %}\n{% block titulo %}Painel — Monitor TCG{% endblock %}\n\n{% block conteudo %}\n\n  <section class="barra-resumo">\n    <div class="contagens">\n      <span class="contagem contagem-ok">{{ resumo.disponiveis }} à venda</span>\n      <span class="contagem">{{ resumo.esgotados }} esgotados</span>\n      {% if resumo.com_erro %}<span class="contagem contagem-erro">{{ resumo.com_erro }} sem resposta</span>{% endif %}\n    </div>\n\n    <form method="post" action="{{ url_for(\'checar_agora\') }}">\n      <button class="botao-principal" type="submit">Checar agora</button>\n    </form>\n  </section>\n\n  <p class="nota-intervalo">\n    Checagem automática a cada {{ config.intervalo_minutos }} minutos enquanto esta janela estiver aberta.\n  </p>\n\n  {% if not produtos %}\n    <div class="vazio">\n      <h2>Nenhum produto na mira ainda</h2>\n      <p>Cole o link de um produto e o app passa a vigiar o estoque e o preço dele.</p>\n      <a class="botao-principal" href="{{ url_for(\'novo_produto\') }}">Adicionar o primeiro</a>\n    </div>\n  {% endif %}\n\n  <div class="fichario">\n    {% for produto in produtos %}\n      {% set s = produto.situacao %}\n      <article class="carta\n        {% if s.em_estoque is sameas true %}carta-disponivel\n        {% elif s.em_estoque is sameas false %}carta-esgotada\n        {% elif s %}carta-erro\n        {% else %}carta-nova{% endif %}">\n\n        <div class="carta-topo">\n          <h2 class="carta-nome">{{ produto.nome }}</h2>\n          <span class="carta-loja">{{ produto.loja }}</span>\n        </div>\n\n        <div class="carta-corpo">\n          {% if s.em_estoque is sameas true %}\n            <span class="selo selo-disponivel">Disponível</span>\n          {% elif s.em_estoque is sameas false %}\n            <span class="selo selo-esgotado">Esgotado</span>\n          {% elif s %}\n            <span class="selo selo-erro">Sem resposta</span>\n          {% else %}\n            <span class="selo selo-novo">Ainda não checado</span>\n          {% endif %}\n\n          <p class="carta-preco">{{ s.preco | reais }}</p>\n\n          {% if s.detalhe %}<p class="carta-detalhe">{{ s.detalhe }}</p>{% endif %}\n        </div>\n\n        <div class="carta-rodape">\n          <span class="carimbo">{{ s.checado_em | hora }}</span>\n          <span class="carta-acoes">\n            <a href="{{ produto.url }}" target="_blank" rel="noopener">abrir loja</a>\n            <a href="{{ url_for(\'editar_produto\', produto_id=produto.id) }}">editar</a>\n            <form method="post" action="{{ url_for(\'remover_produto\', produto_id=produto.id) }}"\n                  onsubmit="return confirm(\'Tirar {{ produto.nome }} da lista?\')">\n              <button type="submit" class="link-botao">remover</button>\n            </form>\n          </span>\n        </div>\n      </article>\n    {% endfor %}\n  </div>\n\n  {% if historico %}\n    <section class="historico">\n      <h2>Últimos avisos</h2>\n      <ul>\n        {% for item in historico %}\n          <li>\n            <span class="carimbo">{{ item.quando | hora }}</span>\n            <span><strong>{{ item.nome }}</strong> — {{ item.resumo }}</span>\n          </li>\n        {% endfor %}\n      </ul>\n    </section>\n  {% endif %}\n\n{% endblock %}\n', 'produto.html': '{% extends "base.html" %}\n{% block titulo %}{{ "Editar produto" if produto else "Adicionar produto" }} — Monitor TCG{% endblock %}\n\n{% block conteudo %}\n\n  <div class="painel-form">\n    <h1 class="titulo-tela">{{ "Editar produto" if produto else "Adicionar produto" }}</h1>\n    <p class="subtitulo-tela">\n      Cole o link da página do produto. Os campos avançados já vêm preenchidos\n      sozinhos — mexa neles só se a checagem não funcionar.\n    </p>\n\n    <form method="post" class="formulario">\n\n      <label>\n        <span class="rotulo">Link do produto</span>\n        <input type="url" name="url" required placeholder="https://..."\n               value="{{ produto.url if produto else \'\' }}">\n        <span class="ajuda">A página do produto em si, não a home da loja.</span>\n      </label>\n\n      <label>\n        <span class="rotulo">Como você quer chamar</span>\n        <input type="text" name="nome" required placeholder="Ex.: Booster Box Escuridão Absoluta"\n               value="{{ produto.nome if produto else \'\' }}">\n      </label>\n\n      <label>\n        <span class="rotulo">Loja</span>\n        <input type="text" name="loja" placeholder="preenchido pelo link"\n               value="{{ produto.loja if produto else \'\' }}">\n      </label>\n\n      <details class="avancado" {% if produto %}open{% endif %}>\n        <summary>Ajuste fino (opcional)</summary>\n\n        <label>\n          <span class="rotulo">Método de checagem</span>\n          <select name="metodo">\n            <option value="html" {% if produto and produto.metodo == \'html\' %}selected{% endif %}>\n              Ler a página (serve para a maioria)\n            </option>\n            <option value="vtex" {% if produto and produto.metodo == \'vtex\' %}selected{% endif %}>\n              API VTEX (Copag B2B)\n            </option>\n            <option value="woocommerce" {% if produto and produto.metodo == \'woocommerce\' %}selected{% endif %}>\n              API WooCommerce (Super TCG)\n            </option>\n          </select>\n          <span class="ajuda">Deixe em branco no cadastro novo que o app escolhe pelo link.</span>\n        </label>\n\n        <label>\n          <span class="rotulo">Termo de busca</span>\n          <input type="text" name="termo_busca" placeholder="usado nos métodos de API"\n                 value="{{ produto.termo_busca if produto else \'\' }}">\n          <span class="ajuda">Se a API não achar o produto, tente um trecho mais curto do nome.</span>\n        </label>\n\n        <label>\n          <span class="rotulo">Trecho do preço na página</span>\n          <input type="text" name="seletor_preco" placeholder="ex.: .preco-produto"\n                 value="{{ produto.seletor_preco if produto else \'\' }}">\n          <span class="ajuda">\n            Só para o método "ler a página", quando você quiser acompanhar o preço:\n            clique com o botão direito no preço do site → Inspecionar → copie a classe.\n          </span>\n        </label>\n      </details>\n\n      <div class="acoes-form">\n        <button class="botao-principal" type="submit">\n          {{ "Salvar alterações" if produto else "Adicionar à lista" }}\n        </button>\n        <a class="botao-secundario" href="{{ url_for(\'painel\') }}">Cancelar</a>\n      </div>\n    </form>\n  </div>\n\n{% endblock %}\n', 'ajustes.html': '{% extends "base.html" %}\n{% block titulo %}Ajustes — Monitor TCG{% endblock %}\n\n{% block conteudo %}\n\n  <div class="painel-form">\n    <h1 class="titulo-tela">Ajustes</h1>\n    <p class="subtitulo-tela">\n      Com que frequência checar e para onde mandar os avisos.\n    </p>\n\n    <form method="post" class="formulario">\n\n      <fieldset>\n        <legend>Ritmo</legend>\n\n        <label>\n          <span class="rotulo">Checar a cada quantos minutos</span>\n          <input type="number" name="intervalo_minutos" min="1" value="{{ config.intervalo_minutos }}">\n          <span class="ajuda">Abaixo de 10 minutos algumas lojas podem começar a recusar as consultas.</span>\n        </label>\n\n        <label>\n          <span class="rotulo">Desistir de uma loja depois de quantos segundos</span>\n          <input type="number" name="timeout_segundos" min="5" value="{{ config.timeout_segundos }}">\n        </label>\n      </fieldset>\n\n      <fieldset>\n        <legend>Discord</legend>\n\n        <label class="linha-checkbox">\n          <input type="checkbox" name="discord_ativo" value="sim"\n                 {% if config.avisos.discord_ativo %}checked{% endif %}>\n          <span>Mandar avisos no Discord</span>\n        </label>\n\n        <label>\n          <span class="rotulo">Link do webhook</span>\n          <input type="text" name="discord_webhook" placeholder="https://discord.com/api/webhooks/..."\n                 value="{{ config.avisos.discord_webhook }}">\n          <span class="ajuda">\n            No Discord: botão direito no canal → Editar Canal → Integrações → Webhooks →\n            Novo Webhook → Copiar URL.\n          </span>\n        </label>\n      </fieldset>\n\n      <fieldset>\n        <legend>Telegram</legend>\n\n        <label class="linha-checkbox">\n          <input type="checkbox" name="telegram_ativo" value="sim"\n                 {% if config.avisos.telegram_ativo %}checked{% endif %}>\n          <span>Mandar avisos no Telegram</span>\n        </label>\n\n        <label>\n          <span class="rotulo">Token do bot</span>\n          <input type="text" name="telegram_token" value="{{ config.avisos.telegram_token }}">\n          <span class="ajuda">Crie o bot conversando com o @BotFather no Telegram.</span>\n        </label>\n\n        <label>\n          <span class="rotulo">Seu chat ID</span>\n          <input type="text" name="telegram_chat_id" value="{{ config.avisos.telegram_chat_id }}">\n          <span class="ajuda">Descubra o seu conversando com o @userinfobot.</span>\n        </label>\n      </fieldset>\n\n      <fieldset>\n        <legend>Email</legend>\n\n        <label class="linha-checkbox">\n          <input type="checkbox" name="email_ativo" value="sim"\n                 {% if config.avisos.email_ativo %}checked{% endif %}>\n          <span>Mandar avisos por email</span>\n        </label>\n\n        <div class="dupla">\n          <label>\n            <span class="rotulo">Servidor</span>\n            <input type="text" name="email_servidor" value="{{ config.avisos.email_servidor }}">\n          </label>\n          <label>\n            <span class="rotulo">Porta</span>\n            <input type="text" name="email_porta" value="{{ config.avisos.email_porta }}">\n          </label>\n        </div>\n\n        <label>\n          <span class="rotulo">Sua conta</span>\n          <input type="text" name="email_usuario" value="{{ config.avisos.email_usuario }}">\n        </label>\n\n        <label>\n          <span class="rotulo">Senha</span>\n          <input type="password" name="email_senha" value="{{ config.avisos.email_senha }}">\n          <span class="ajuda">No Gmail, gere uma "senha de app" — a senha normal da conta não funciona.</span>\n        </label>\n\n        <label>\n          <span class="rotulo">Mandar para</span>\n          <input type="text" name="email_destino" value="{{ config.avisos.email_destino }}">\n        </label>\n      </fieldset>\n\n      <div class="acoes-form">\n        <button class="botao-principal" type="submit">Salvar ajustes</button>\n      </div>\n    </form>\n\n    <form method="post" action="{{ url_for(\'testar_avisos\') }}" class="teste">\n      <button class="botao-secundario" type="submit">Enviar mensagem de teste</button>\n      <span class="ajuda">Salve os ajustes antes de testar.</span>\n    </form>\n\n    <p class="alerta-privacidade">\n      Esses dados ficam guardados no servidor da hospedagem, não no seu celular.\n      Cada pessoa entra com a própria senha (variável <code>ACESSOS</code> no\n      Render) — pra tirar o acesso de alguém, remova só a senha dela ali, sem\n      mexer na de mais ninguém.\n    </p>\n  </div>\n\n{% endblock %}\n', 'entrar.html': '<!DOCTYPE html>\n<html lang="pt-BR">\n<head>\n  <meta charset="utf-8">\n  <meta name="viewport" content="width=device-width, initial-scale=1">\n  <title>Entrar — Monitor TCG</title>\n  <link rel="apple-touch-icon" href="/icone.png">\n  <link rel="stylesheet" href="/estilo.css">\n</head>\n<body class="corpo-login">\n  <div class="tela-login">\n    <form method="post" class="cartao-login">\n      <img src="/icone.png" alt="" class="login-icone">\n      <h1 class="login-titulo">Monitor TCG</h1>\n      <p class="login-subtitulo">Acesso restrito — só quem tem senha entra.</p>\n      {% if erro %}<p class="recado recado-erro">{{ erro }}</p>{% endif %}\n      <input type="hidden" name="proximo" value="{{ proximo }}">\n      <label>\n        <span class="rotulo">Senha</span>\n        <input type="password" name="senha" autofocus required>\n      </label>\n      <button class="botao-principal" type="submit">Entrar</button>\n    </form>\n  </div>\n</body>\n</html>\n'}
CSS = '/* ==========================================================================\n   Monitor TCG — aparência do app\n\n   A ideia visual: a tela é o feltro de uma mesa de jogo, e cada produto\n   vigiado é uma carta apoiada nela. Produto disponível ganha o brilho\n   dourado de carta foil; esgotado fica opaco, como carta virada.\n\n   Se quiser mudar as cores do app inteiro, mexa só no bloco :root abaixo —\n   todo o resto do arquivo se serve dali.\n   ========================================================================== */\n\n:root {\n  /* ---- cores ---- */\n  --feltro:        #14231f;   /* fundo da página (feltro da mesa) */\n  --feltro-claro:  #1c302a;   /* faixas e caixas sobre o feltro */\n  --feltro-borda:  #2a463d;\n  --carta:         #f4efe2;   /* papel da carta */\n  --carta-sombra:  #ddd5c0;\n  --tinta:         #14120d;   /* texto escuro sobre a carta */\n  --tinta-fraca:   #6b6455;\n  --ouro:          #e0a93b;   /* destaque: disponível, raridade, foco */\n  --ouro-escuro:   #a87d24;\n  --sereno:        #9fb3aa;   /* texto discreto sobre o feltro */\n  --alerta:        #d4695c;   /* erro, sem resposta */\n\n  /* ---- tipos ---- */\n  --display: "Antonio", "Arial Narrow", sans-serif;   /* títulos */\n  --corpo:   "Archivo", system-ui, sans-serif;        /* texto comum */\n  --dados:   "Space Mono", "Courier New", monospace;  /* preços e horários */\n\n  /* ---- medidas ---- */\n  --raio: 10px;\n  --largura-max: 1100px;\n}\n\n* { box-sizing: border-box; }\n\nbody {\n  margin: 0;\n  min-height: 100vh;\n  font-family: var(--corpo);\n  color: var(--carta);\n  /* trama sutil do feltro, feita só com gradientes */\n  background-color: var(--feltro);\n  background-image:\n    repeating-linear-gradient(45deg,  rgba(255,255,255,.014) 0 2px, transparent 2px 4px),\n    repeating-linear-gradient(-45deg, rgba(0,0,0,.05)        0 2px, transparent 2px 4px);\n  display: flex;\n  flex-direction: column;\n}\n\n/* foco pelo teclado: sempre visível, em qualquer botão ou link */\n:focus-visible {\n  outline: 2px solid var(--ouro);\n  outline-offset: 2px;\n  border-radius: 3px;\n}\n\n/* ============================================================ cabeçalho == */\n\n.topo {\n  background: var(--feltro-claro);\n  border-bottom: 1px solid var(--feltro-borda);\n  box-shadow: 0 12px 28px rgba(0,0,0,.28);\n}\n\n.topo-interno {\n  max-width: var(--largura-max);\n  margin: 0 auto;\n  padding: 18px 24px;\n  display: flex;\n  align-items: baseline;\n  justify-content: space-between;\n  gap: 20px;\n  flex-wrap: wrap;\n}\n\n.marca { text-decoration: none; display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }\n\n.marca-nome {\n  font-family: var(--display);\n  font-size: 30px;\n  font-weight: 700;\n  letter-spacing: .06em;\n  text-transform: uppercase;\n  color: var(--carta);\n}\n\n.marca-sub { color: var(--sereno); font-size: 13px; }\n\n.menu { display: flex; gap: 22px; }\n\n.menu a {\n  color: var(--sereno);\n  text-decoration: none;\n  font-size: 14px;\n  font-weight: 500;\n  padding-bottom: 3px;\n  border-bottom: 2px solid transparent;\n}\n\n.menu a:hover { color: var(--carta); }\n.menu a.ativo { color: var(--ouro); border-bottom-color: var(--ouro); }\n\n/* ============================================================== conteúdo == */\n\n.conteudo {\n  flex: 1;\n  width: 100%;\n  max-width: var(--largura-max);\n  margin: 0 auto;\n  padding: 28px 24px 60px;\n}\n\n.recado {\n  padding: 12px 16px;\n  border-radius: var(--raio);\n  border-left: 3px solid var(--ouro);\n  background: var(--feltro-claro);\n  font-size: 14px;\n}\n\n.recado-erro { border-left-color: var(--alerta); }\n.recado-neutro { border-left-color: var(--sereno); }\n\n/* ============================================================== resumo ==== */\n\n.barra-resumo {\n  display: flex;\n  align-items: center;\n  justify-content: space-between;\n  gap: 16px;\n  flex-wrap: wrap;\n  padding-bottom: 14px;\n  border-bottom: 1px solid var(--feltro-borda);\n}\n\n.contagens { display: flex; gap: 18px; flex-wrap: wrap; }\n\n.contagem {\n  font-family: var(--dados);\n  font-size: 13px;\n  color: var(--sereno);\n}\n\n.contagem-ok { color: var(--ouro); }\n.contagem-erro { color: var(--alerta); }\n\n.nota-intervalo {\n  color: var(--sereno);\n  font-size: 13px;\n  margin: 12px 0 26px;\n}\n\n/* ============================================================== botões ==== */\n\n.botao-principal {\n  font-family: var(--display);\n  font-size: 15px;\n  font-weight: 600;\n  letter-spacing: .08em;\n  text-transform: uppercase;\n  color: var(--tinta);\n  background: var(--ouro);\n  border: none;\n  border-radius: var(--raio);\n  padding: 11px 22px;\n  cursor: pointer;\n  text-decoration: none;\n  display: inline-block;\n}\n\n.botao-principal:hover { background: #eab949; }\n\n.botao-secundario {\n  font-family: var(--corpo);\n  font-size: 14px;\n  color: var(--sereno);\n  background: transparent;\n  border: 1px solid var(--feltro-borda);\n  border-radius: var(--raio);\n  padding: 10px 18px;\n  cursor: pointer;\n  text-decoration: none;\n  display: inline-block;\n}\n\n.botao-secundario:hover { color: var(--carta); border-color: var(--sereno); }\n\n/* ====================================================== grade de cartas === */\n\n.fichario {\n  display: grid;\n  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));\n  gap: 20px;\n}\n\n.carta {\n  position: relative;\n  overflow: hidden;\n  background: var(--carta);\n  color: var(--tinta);\n  border-radius: var(--raio);\n  border: 1px solid var(--carta-sombra);\n  box-shadow: 0 10px 22px rgba(0,0,0,.30);\n  display: flex;\n  flex-direction: column;\n  min-height: 210px;\n}\n\n.carta-topo {\n  padding: 14px 16px 10px;\n  border-bottom: 1px solid var(--carta-sombra);\n}\n\n.carta-nome {\n  font-family: var(--display);\n  font-size: 19px;\n  font-weight: 600;\n  line-height: 1.15;\n  margin: 0 0 5px;\n}\n\n.carta-loja {\n  font-family: var(--dados);\n  font-size: 11px;\n  color: var(--tinta-fraca);\n}\n\n.carta-corpo { padding: 16px; flex: 1; }\n\n.selo {\n  display: inline-block;\n  font-family: var(--display);\n  font-size: 13px;\n  font-weight: 600;\n  letter-spacing: .12em;\n  text-transform: uppercase;\n  padding: 5px 11px;\n  border-radius: 4px;\n}\n\n.selo-disponivel { background: var(--ouro); color: var(--tinta); }\n.selo-esgotado   { background: #e2ddd0; color: #57503f; }\n.selo-erro       { background: var(--alerta); color: #fff; }\n.selo-novo       { background: #e2ddd0; color: #57503f; }\n\n.carta-preco {\n  font-family: var(--dados);\n  font-size: 25px;\n  font-weight: 700;\n  margin: 12px 0 6px;\n}\n\n.carta-detalhe {\n  font-size: 12px;\n  color: var(--tinta-fraca);\n  margin: 0;\n  line-height: 1.45;\n}\n\n.carta-rodape {\n  border-top: 1px solid var(--carta-sombra);\n  padding: 9px 16px;\n  display: flex;\n  align-items: center;\n  justify-content: space-between;\n  gap: 10px;\n  flex-wrap: wrap;\n  background: rgba(0,0,0,.03);\n}\n\n.carimbo { font-family: var(--dados); font-size: 11px; color: var(--tinta-fraca); }\n\n.carta-acoes { display: flex; align-items: center; gap: 12px; }\n.carta-acoes form { display: inline; }\n\n.carta-acoes a,\n.link-botao {\n  font-family: var(--corpo);\n  font-size: 12px;\n  color: var(--tinta-fraca);\n  text-decoration: none;\n  background: none;\n  border: none;\n  padding: 0;\n  cursor: pointer;\n  border-bottom: 1px solid transparent;\n}\n\n.carta-acoes a:hover,\n.link-botao:hover { color: var(--tinta); border-bottom-color: var(--ouro-escuro); }\n\n/* --- o brilho de carta foil: só aparece em produto disponível --- */\n\n.carta-disponivel { border-color: var(--ouro-escuro); }\n\n.carta-disponivel::after {\n  content: "";\n  position: absolute;\n  inset: 0;\n  pointer-events: none;\n  background: linear-gradient(115deg,\n    transparent 30%,\n    rgba(224,169,59,.20) 45%,\n    rgba(255,255,255,.32) 50%,\n    rgba(224,169,59,.20) 55%,\n    transparent 70%);\n  background-size: 260% 260%;\n  animation: foil 5.5s ease-in-out infinite;\n}\n\n@keyframes foil {\n  0%, 100% { background-position: 130% 0; }\n  50%      { background-position: -30% 0; }\n}\n\n.carta-esgotada { opacity: .72; }\n.carta-erro { border-color: var(--alerta); }\n\n/* quem prefere menos movimento não vê o brilho animado */\n@media (prefers-reduced-motion: reduce) {\n  .carta-disponivel::after { animation: none; opacity: .35; }\n}\n\n/* ============================================================== vazio ===== */\n\n.vazio {\n  text-align: center;\n  padding: 60px 24px;\n  border: 1px dashed var(--feltro-borda);\n  border-radius: var(--raio);\n}\n\n.vazio h2 { font-family: var(--display); font-weight: 600; margin: 0 0 8px; }\n.vazio p { color: var(--sereno); margin: 0 0 20px; }\n\n/* ============================================================ histórico === */\n\n.historico { margin-top: 46px; }\n\n.historico h2 {\n  font-family: var(--display);\n  font-size: 15px;\n  font-weight: 600;\n  letter-spacing: .12em;\n  text-transform: uppercase;\n  color: var(--sereno);\n  border-bottom: 1px solid var(--feltro-borda);\n  padding-bottom: 8px;\n}\n\n.historico ul { list-style: none; margin: 0; padding: 0; }\n\n.historico li {\n  display: flex;\n  gap: 14px;\n  padding: 9px 0;\n  border-bottom: 1px solid rgba(255,255,255,.05);\n  font-size: 13px;\n  color: var(--sereno);\n}\n\n.historico .carimbo { color: var(--ouro); flex-shrink: 0; }\n\n/* ========================================================== formulários === */\n\n.painel-form { max-width: 620px; }\n\n.titulo-tela {\n  font-family: var(--display);\n  font-size: 30px;\n  font-weight: 600;\n  letter-spacing: .03em;\n  margin: 0 0 6px;\n}\n\n.subtitulo-tela { color: var(--sereno); font-size: 14px; margin: 0 0 28px; line-height: 1.5; }\n\n.formulario label { display: block; margin-bottom: 20px; }\n\n.rotulo {\n  display: block;\n  font-size: 13px;\n  font-weight: 600;\n  letter-spacing: .03em;\n  margin-bottom: 7px;\n}\n\n.formulario input[type="text"],\n.formulario input[type="url"],\n.formulario input[type="password"],\n.formulario input[type="number"],\n.formulario select {\n  width: 100%;\n  font-family: var(--corpo);\n  font-size: 14px;\n  color: var(--carta);\n  background: var(--feltro-claro);\n  border: 1px solid var(--feltro-borda);\n  border-radius: var(--raio);\n  padding: 11px 13px;\n}\n\n.formulario input:focus,\n.formulario select:focus {\n  outline: 2px solid var(--ouro);\n  outline-offset: 1px;\n}\n\n.ajuda {\n  display: block;\n  font-size: 12px;\n  color: var(--sereno);\n  margin-top: 6px;\n  line-height: 1.5;\n}\n\n.formulario .linha-checkbox {\n  display: flex;\n  align-items: flex-start;\n  gap: 10px;\n  font-size: 14px;\n}\n\n.formulario .linha-checkbox input { margin-top: 3px; accent-color: var(--ouro); }\n\n.dupla { display: flex; gap: 14px; }\n.dupla label { flex: 1; }\n\nfieldset {\n  border: 1px solid var(--feltro-borda);\n  border-radius: var(--raio);\n  padding: 20px;\n  margin: 0 0 24px;\n}\n\nlegend {\n  font-family: var(--display);\n  font-size: 14px;\n  font-weight: 600;\n  letter-spacing: .12em;\n  text-transform: uppercase;\n  color: var(--ouro);\n  padding: 0 8px;\n}\n\n.avancado {\n  border: 1px solid var(--feltro-borda);\n  border-radius: var(--raio);\n  padding: 14px 18px;\n  margin-bottom: 24px;\n}\n\n.avancado summary {\n  cursor: pointer;\n  font-size: 14px;\n  font-weight: 500;\n  color: var(--sereno);\n}\n\n.avancado[open] summary { margin-bottom: 18px; color: var(--carta); }\n\n.acoes-form { display: flex; align-items: center; gap: 14px; }\n\n.teste {\n  margin-top: 26px;\n  padding-top: 22px;\n  border-top: 1px solid var(--feltro-borda);\n  display: flex;\n  align-items: center;\n  gap: 14px;\n  flex-wrap: wrap;\n}\n\n.teste .ajuda { margin: 0; }\n\n.alerta-privacidade {\n  margin-top: 26px;\n  font-size: 12px;\n  color: var(--sereno);\n  line-height: 1.6;\n}\n\n.alerta-privacidade code {\n  font-family: var(--dados);\n  background: var(--feltro-claro);\n  padding: 2px 6px;\n  border-radius: 4px;\n}\n\n/* ============================================================== rodapé ==== */\n\n.rodape {\n  border-top: 1px solid var(--feltro-borda);\n  padding: 18px 24px;\n  text-align: center;\n  font-size: 12px;\n  color: var(--sereno);\n}\n\n/* ============================================================== celular === */\n\n@media (max-width: 640px) {\n  .topo-interno { padding: 14px 16px; }\n  .menu { gap: 15px; }\n  .conteudo { padding: 20px 16px 40px; }\n  .marca-nome { font-size: 24px; }\n  .marca-sub { display: none; }\n  .fichario { grid-template-columns: 1fr; }\n  .dupla { flex-direction: column; gap: 0; }\n}\n\n/* ====================================================== tela "Celular" ==== */\n\n.cartao-qr {\n  text-align: center;\n  background: var(--feltro-claro);\n  border: 1px solid var(--feltro-borda);\n  border-radius: var(--raio);\n  padding: 26px 20px;\n  margin-bottom: 28px;\n}\n\n/* QR precisa de fundo claro e margem para a câmera ler com facilidade */\n.qr {\n  display: inline-block;\n  background: #fff;\n  border-radius: var(--raio);\n  padding: 16px;\n  line-height: 0;\n}\n\n.qr svg {\n  width: min(62vw, 240px);\n  height: min(62vw, 240px);\n}\n\n.endereco-celular {\n  font-family: var(--dados);\n  font-size: 18px;\n  color: var(--ouro);\n  margin: 18px 0 4px;\n  word-break: break-all;\n}\n\n.cartao-qr .ajuda { margin-top: 2px; }\n\n.passos {\n  margin: 0 0 26px;\n  padding-left: 22px;\n  line-height: 1.7;\n  font-size: 15px;\n}\n\n.passos li { margin-bottom: 12px; }\n\n.tecla {\n  display: inline-block;\n  font-family: var(--dados);\n  font-size: 13px;\n  background: var(--feltro-claro);\n  border: 1px solid var(--feltro-borda);\n  border-radius: 5px;\n  padding: 1px 7px;\n}\n\n.avisos-celular {\n  border: 1px solid var(--feltro-borda);\n  border-radius: var(--raio);\n  padding: 16px 20px;\n  font-size: 14px;\n  color: var(--sereno);\n  line-height: 1.65;\n}\n\n.avisos-celular p { margin: 0 0 8px; color: var(--carta); }\n.avisos-celular ul { margin: 0; padding-left: 20px; }\n.avisos-celular li { margin-bottom: 8px; }\n\n/* ============================================= app instalado no iPhone ==== */\n\n/* Respeita o entalhe (notch) e a barra de baixo do iPhone em tela cheia */\n.topo { padding-top: env(safe-area-inset-top, 0); }\n.rodape { padding-bottom: calc(18px + env(safe-area-inset-bottom, 0px)); }\n\n/* ============================================================= tela de login == */\n\nbody.corpo-login { display: block; }\n\n.tela-login {\n  min-height: 100vh;\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  padding: 24px;\n}\n\n.cartao-login {\n  width: 100%;\n  max-width: 340px;\n  background: var(--carta);\n  color: var(--tinta);\n  border-radius: var(--raio);\n  border: 1px solid var(--carta-sombra);\n  box-shadow: 0 20px 44px rgba(0,0,0,.35);\n  padding: 34px 30px;\n  text-align: center;\n}\n\n.login-icone { width: 56px; height: 56px; border-radius: 8px; margin-bottom: 14px; }\n\n.login-titulo {\n  font-family: var(--display);\n  font-size: 24px;\n  font-weight: 700;\n  letter-spacing: .04em;\n  text-transform: uppercase;\n  margin: 0 0 6px;\n}\n\n.login-subtitulo { color: var(--tinta-fraca); font-size: 13px; margin: 0 0 20px; }\n\n.cartao-login label { display: block; margin-bottom: 18px; text-align: left; }\n.cartao-login .recado { text-align: left; margin-bottom: 16px; }\n.cartao-login .botao-principal { width: 100%; }\n\n.menu-sair { opacity: .75; }\n.menu-sair:hover { opacity: 1; }\n'
ICONE_PNG_BASE64 = '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'

# ============================================================================
# CONFIGURACAO E ARMAZENAMENTO
#
# Guarda tudo num unico arquivo JSON. Em hospedagem gratuita (Render/Railway)
# esse arquivo pode ser apagado quando o servico reinicia ou voce reimplanta
# o codigo — normal para uma versao de teste. Se isso incomodar mais adiante,
# o proximo passo natural e trocar por um banco de dados de verdade.
# ============================================================================

CAMINHO_DADOS = os.path.join(os.path.dirname(__file__), "dados.json")

CONFIG_PADRAO = {
    "intervalo_minutos": 20,
    "timeout_segundos": 15,
    "avisos": {
        "discord_ativo": False, "discord_webhook": "",
        "telegram_ativo": False, "telegram_token": "", "telegram_chat_id": "",
        "email_ativo": False, "email_servidor": "smtp.gmail.com", "email_porta": 587,
        "email_usuario": "", "email_senha": "", "email_destino": "",
    },
    "produtos": [],
    "estado": {},
    "historico": [],
}


def ler_dados():
    if not os.path.exists(CAMINHO_DADOS):
        return json.loads(json.dumps(CONFIG_PADRAO))
    try:
        with open(CAMINHO_DADOS, "r", encoding="utf-8") as f:
            dados = json.load(f)
    except (json.JSONDecodeError, OSError):
        return json.loads(json.dumps(CONFIG_PADRAO))
    for chave, valor in CONFIG_PADRAO.items():
        dados.setdefault(chave, valor)
    for chave, valor in CONFIG_PADRAO["avisos"].items():
        dados["avisos"].setdefault(chave, valor)
    return dados


def gravar_dados(dados):
    with open(CAMINHO_DADOS, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)


def atualizar_produto(dados, produto_id, em_estoque, preco, detalhe, erro=None):
    """Compara com a checagem anterior e devolve o que mudou."""
    anterior = dados["estado"].get(produto_id, {})
    mudancas = {
        "voltou_ao_estoque": (not anterior.get("em_estoque")) and bool(em_estoque),
        "saiu_de_estoque": bool(anterior.get("em_estoque")) and em_estoque is False,
        "preco_mudou": (
            anterior.get("preco") is not None and preco is not None
            and anterior.get("preco") != preco
        ),
    }
    dados["estado"][produto_id] = {
        "em_estoque": em_estoque, "preco": preco, "detalhe": detalhe, "erro": erro,
        "checado_em": datetime.now(timezone.utc).isoformat(),
    }
    return mudancas


def registrar_historico(dados, nome, resumo, limite=30):
    dados["historico"].insert(0, {"nome": nome, "resumo": resumo,
                                   "quando": datetime.now(timezone.utc).isoformat()})
    del dados["historico"][limite:]


# ============================================================================
# SCRAPER — vai ate a loja e descobre se o produto esta disponivel
# ============================================================================

CABECALHOS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept-Language": "pt-BR,pt;q=0.9",
}

PALAVRAS_ESGOTADO = [
    "esgotado", "indisponivel", "indisponível", "fora de estoque",
    "avise-me", "avise me quando chegar", "sold out", "sem estoque", "produto esgotado",
]

PLATAFORMAS_CONHECIDAS = {
    "b2b.copagloja.com.br": "vtex",
    "lojapokemonsuper.com": "woocommerce",
    "dalaran.com.br": "html",
    "actionnew.com.br": "html",
    "paladinsgames.com.br": "html",
    "buscaintegrada.com.br": "html",
    "epicgame.com.br": "html",
}


def identificar_loja(url):
    endereco = urlparse(url)
    dominio = endereco.netloc.lower().replace("www.", "")
    metodo = PLATAFORMAS_CONHECIDAS.get(dominio, "html")
    partes = [seg for seg in endereco.path.split("/") if seg and seg not in ("p", "produto", "produtos")]
    termo = partes[-1].replace("-", " ") if partes else ""
    return {"loja": dominio, "metodo": metodo,
            "base_url": f"{endereco.scheme}://{endereco.netloc}", "termo_busca": termo}


def converter_preco(texto):
    numeros = "".join(c for c in texto if c.isdigit() or c in ",.")
    numeros = numeros.replace(".", "").replace(",", ".")
    try:
        return float(numeros)
    except ValueError:
        return None


def checar_vtex(produto, timeout=15):
    base = produto["base_url"].rstrip("/")
    url = f"{base}/api/catalog_system/pub/products/search/{produto['termo_busca']}"
    r = requests.get(url, headers=CABECALHOS, timeout=timeout)
    r.raise_for_status()
    itens = r.json()
    if not itens:
        return {"em_estoque": None, "preco": None, "detalhe": "produto nao encontrado — revise o termo de busca"}
    oferta = itens[0]["items"][0]["sellers"][0]["commertialOffer"]
    qtd = oferta.get("AvailableQuantity", 0)
    return {"em_estoque": qtd > 0, "preco": oferta.get("Price"), "detalhe": f"{qtd} em estoque na API"}


def checar_woocommerce(produto, timeout=15):
    base = produto["base_url"].rstrip("/")
    r = requests.get(f"{base}/wp-json/wc/store/v1/products",
                      params={"search": produto["termo_busca"], "per_page": 5},
                      headers=CABECALHOS, timeout=timeout)
    if r.status_code != 200:
        return checar_html(produto, timeout=timeout)
    itens = r.json()
    if not itens:
        return {"em_estoque": None, "preco": None, "detalhe": "produto nao encontrado — revise o termo de busca"}
    item = itens[0]
    preco = None
    precos = item.get("prices") or {}
    if precos.get("price"):
        divisor = 10 ** int(precos.get("currency_minor_unit", 2))
        preco = int(precos["price"]) / divisor
    return {"em_estoque": bool(item.get("is_in_stock")), "preco": preco,
            "detalhe": f"encontrado na API: {item.get('name')}"}


def checar_html(produto, timeout=15):
    r = requests.get(produto["url"], headers=CABECALHOS, timeout=timeout)
    r.raise_for_status()
    sopa = BeautifulSoup(r.text, "html.parser")
    palavras = [pal.lower() for pal in (produto.get("palavras_esgotado") or PALAVRAS_ESGOTADO)]

    seletor = produto.get("seletor_estoque")
    if seletor:
        elemento = sopa.select_one(seletor)
        if elemento is None:
            em_estoque, detalhe = None, "nao achei esse trecho na pagina — revise o seletor"
        else:
            texto = elemento.get_text(strip=True).lower()
            achou = next((pal for pal in palavras if pal in texto), None)
            em_estoque, detalhe = achou is None, f"trecho lido: {texto[:60]}"
    else:
        texto = sopa.get_text(" ", strip=True).lower()
        achou = next((pal for pal in palavras if pal in texto), None)
        em_estoque = achou is None
        detalhe = f"achei a palavra '{achou}' na pagina" if achou else "nenhum sinal de esgotado na pagina"

    preco = None
    if produto.get("seletor_preco"):
        el = sopa.select_one(produto["seletor_preco"])
        if el:
            preco = converter_preco(el.get_text(strip=True))

    return {"em_estoque": em_estoque, "preco": preco, "detalhe": detalhe}


METODOS = {"vtex": checar_vtex, "woocommerce": checar_woocommerce, "html": checar_html}


def checar_produto(produto, timeout=15):
    return METODOS[produto.get("metodo", "html")](produto, timeout=timeout)


# ============================================================================
# AVISOS — Discord, Telegram e email
# ============================================================================

def enviar_discord(webhook, mensagem):
    r = requests.post(webhook, json={"content": mensagem}, timeout=10)
    r.raise_for_status()


def enviar_telegram(token, chat_id, mensagem):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    r = requests.post(url, json={"chat_id": chat_id, "text": mensagem}, timeout=10)
    r.raise_for_status()


def enviar_email(cfg, assunto, mensagem):
    msg = MIMEText(mensagem, "plain", "utf-8")
    msg["Subject"], msg["From"], msg["To"] = assunto, cfg["email_usuario"], cfg["email_destino"]
    with smtplib.SMTP(cfg["email_servidor"], int(cfg["email_porta"])) as s:
        s.starttls()
        s.login(cfg["email_usuario"], cfg["email_senha"])
        s.sendmail(cfg["email_usuario"], [cfg["email_destino"]], msg.as_string())


def enviar_avisos(dados, mensagem):
    avisos = dados.get("avisos", {})
    erros = []
    if avisos.get("discord_ativo") and avisos.get("discord_webhook"):
        try:
            enviar_discord(avisos["discord_webhook"], mensagem)
        except Exception as e:
            erros.append(f"Discord: {e}")
    if avisos.get("telegram_ativo") and avisos.get("telegram_token"):
        try:
            enviar_telegram(avisos["telegram_token"], avisos["telegram_chat_id"], mensagem)
        except Exception as e:
            erros.append(f"Telegram: {e}")
    if avisos.get("email_ativo") and avisos.get("email_usuario"):
        try:
            enviar_email(avisos, "Monitor TCG", mensagem)
        except Exception as e:
            erros.append(f"Email: {e}")
    return erros


def resumir_mudanca(mudancas, preco):
    partes = []
    if mudancas["voltou_ao_estoque"]:
        partes.append("voltou ao estoque")
    if mudancas["saiu_de_estoque"]:
        partes.append("saiu de estoque")
    if mudancas["preco_mudou"] and preco is not None:
        partes.append(f"preco mudou para R$ {preco:.2f}".replace(".", ","))
    return ", ".join(partes)


def montar_mensagem(produto, mudancas, preco):
    linhas = [f"**{produto['nome']}**", f"_{produto.get('loja', '')}_"]
    if mudancas["voltou_ao_estoque"]:
        linhas.append("VOLTOU AO ESTOQUE")
    if mudancas["saiu_de_estoque"]:
        linhas.append("Saiu de estoque")
    if mudancas["preco_mudou"] and preco is not None:
        linhas.append(f"Preco agora: R$ {preco:.2f}".replace(".", ","))
    linhas.append(produto["url"])
    return "\n".join(linhas)


# ============================================================================
# MONITOR — o maestro: checa tudo, compara, avisa
# ============================================================================

_trava = threading.Lock()


def checar_um(produto, dados):
    try:
        resultado = checar_produto(produto, timeout=dados["timeout_segundos"])
        erro = None
    except Exception as falha:
        resultado = {"em_estoque": None, "preco": None, "detalhe": str(falha)}
        erro = str(falha)

    mudancas = atualizar_produto(dados, produto["id"], resultado["em_estoque"],
                                  resultado["preco"], resultado["detalhe"], erro)
    if erro or not any(mudancas.values()):
        return None
    return {
        "mensagem": montar_mensagem(produto, mudancas, resultado["preco"]),
        "nome": produto["nome"],
        "resumo": resumir_mudanca(mudancas, resultado["preco"]),
    }


def checar_todos(avisar=True):
    with _trava:
        dados = ler_dados()
        avisos = []
        for produto in dados["produtos"]:
            aviso = checar_um(produto, dados)
            if aviso:
                avisos.append(aviso)
                registrar_historico(dados, aviso["nome"], aviso["resumo"])
        gravar_dados(dados)
    if avisar:
        for aviso in avisos:
            enviar_avisos(dados, aviso["mensagem"])
    return len(avisos)


_checagem_automatica_iniciada = False


def iniciar_checagem_automatica():
    global _checagem_automatica_iniciada
    if _checagem_automatica_iniciada:
        return
    _checagem_automatica_iniciada = True

    def rodar():
        while True:
            intervalo = ler_dados()["intervalo_minutos"]
            threading.Event().wait(max(1, intervalo) * 60)
            try:
                checar_todos()
            except Exception as falha:
                print(f"[checagem automatica] deu problema: {falha}")

    threading.Thread(target=rodar, daemon=True).start()


# ============================================================================
# APLICATIVO WEB
# ============================================================================

app = Flask(__name__)
app.secret_key = os.environ.get("CHAVE_SECRETA", uuid.uuid4().hex)
app.jinja_loader = DictLoader(TEMPLATES)

def carregar_acessos():
    """
    Le quem pode entrar a partir da variavel de ambiente ACESSOS, no formato
    "nome:senha,nome2:senha2" — uma senha por pessoa, pra poder tirar o
    acesso de alguem sem mexer na senha de mais ninguem.

    Se ACESSOS nao estiver definida, cai para a variavel SENHA_APP (uma
    senha so, sem nome) — mantem funcionando quem configurou antes desta
    versao com nomes.
    """
    acessos = {}
    bruto = os.environ.get("ACESSOS", "").strip()
    if bruto:
        for par in bruto.split(","):
            if ":" in par:
                nome, senha = par.split(":", 1)
                nome, senha = nome.strip(), senha.strip()
                if nome and senha:
                    acessos[nome] = senha
    if not acessos:
        senha_unica = os.environ.get("SENHA_APP", "").strip()
        if senha_unica:
            acessos["você"] = senha_unica
    return acessos


ACESSOS = carregar_acessos()
ROTAS_LIVRES = ("/icone.png", "/estilo.css", "/entrar")

PRODUTOS_INICIAIS = [
    {"nome": "Box Display ME05 Escuridao Absoluta", "loja": "b2b.copagloja.com.br",
     "metodo": "vtex", "base_url": "https://www.b2b.copagloja.com.br",
     "termo_busca": "box display escuridao absoluta",
     "url": "https://www.b2b.copagloja.com.br/box-display-pokemon-me05-escuridao-absoluta-028d199900000bx/p"},
    {"nome": "Box Display Escuridao Absoluta 5.0", "loja": "lojapokemonsuper.com",
     "metodo": "woocommerce", "base_url": "https://lojapokemonsuper.com",
     "termo_busca": "box display escuridao absoluta",
     "url": "https://lojapokemonsuper.com/produto/box-display-escuridao-absoluta-megaevolucao-5-0/"},
    {"nome": "Riftbound Unleashed — Booster avulso", "loja": "dalaran.com.br", "metodo": "html",
     "url": "https://www.dalaran.com.br/riftbound-tcg-unleashed-booster-avulso"},
    {"nome": "ETB Escuridao Absoluta (PT-BR)", "loja": "actionnew.com.br", "metodo": "html",
     "url": "https://www.actionnew.com.br/pokemon-tcg-colecao-treinador-avancado-megaevolucao-5-escuridao-absoluta-pt-br"},
]


def preparar_primeira_execucao():
    dados = ler_dados()
    if not dados["produtos"]:
        for base in PRODUTOS_INICIAIS:
            dados["produtos"].append({"id": uuid.uuid4().hex[:8], **base})
        gravar_dados(dados)


@app.before_request
def exigir_login():
    # Icone, CSS e a propria tela de login ficam abertos (sem dado sensivel);
    # todo o resto exige sessao valida, porque este app e publico na internet.
    if request.path in ROTAS_LIVRES or not ACESSOS:
        return
    if not session.get("nome"):
        return redirect(url_for("entrar", proximo=request.path))


@app.route("/entrar", methods=["GET", "POST"])
def entrar():
    proximo = request.args.get("proximo") or request.form.get("proximo") or url_for("painel")
    if request.method == "GET":
        return render_template("entrar.html", erro=None, proximo=proximo)
    encontrado = next((nome for nome, senha in ACESSOS.items()
                        if senha == request.form.get("senha", "")), None)
    if not encontrado:
        return render_template("entrar.html", erro="Senha incorreta.", proximo=proximo)
    session.clear()
    session["nome"] = encontrado
    session.permanent = True
    return redirect(proximo)


@app.route("/sair", methods=["GET", "POST"])
def sair():
    session.clear()
    return redirect(url_for("entrar"))


@app.route("/")
def painel():
    dados = ler_dados()
    produtos = [{**produto, "situacao": dados["estado"].get(produto["id"], {})} for produto in dados["produtos"]]
    resumo = {
        "disponiveis": sum(1 for produto in produtos if produto["situacao"].get("em_estoque") is True),
        "esgotados": sum(1 for produto in produtos if produto["situacao"].get("em_estoque") is False),
        "com_erro": sum(1 for produto in produtos if produto["situacao"].get("em_estoque") is None and produto["situacao"]),
        "total": len(produtos),
    }
    return render_template("painel.html", produtos=produtos, resumo=resumo,
                            historico=dados["historico"][:8], config=dados)


@app.route("/produtos/novo", methods=["GET", "POST"])
def novo_produto():
    if request.method == "GET":
        return render_template("produto.html", produto=None)
    url = request.form["url"].strip()
    detectado = identificar_loja(url)
    produto = {
        "id": uuid.uuid4().hex[:8],
        "nome": request.form["nome"].strip() or "Produto sem nome",
        "url": url,
        "loja": request.form.get("loja", "").strip() or detectado["loja"],
        "metodo": request.form.get("metodo") or detectado["metodo"],
        "base_url": detectado["base_url"],
        "termo_busca": request.form.get("termo_busca", "").strip() or detectado["termo_busca"],
        "seletor_preco": request.form.get("seletor_preco", "").strip(),
    }
    dados = ler_dados()
    dados["produtos"].append(produto)
    gravar_dados(dados)
    flash(f"{produto['nome']} entrou na lista.", "ok")
    return redirect(url_for("painel"))


@app.route("/produtos/<produto_id>/editar", methods=["GET", "POST"])
def editar_produto(produto_id):
    dados = ler_dados()
    produto = next((p for p in dados["produtos"] if p["id"] == produto_id), None)
    if produto is None:
        return redirect(url_for("painel"))
    if request.method == "GET":
        return render_template("produto.html", produto=produto)
    produto["nome"] = request.form["nome"].strip()
    produto["url"] = request.form["url"].strip()
    produto["loja"] = request.form.get("loja", "").strip()
    produto["metodo"] = request.form.get("metodo", "html")
    produto["termo_busca"] = request.form.get("termo_busca", "").strip()
    produto["seletor_preco"] = request.form.get("seletor_preco", "").strip()
    produto["base_url"] = identificar_loja(produto["url"])["base_url"]
    gravar_dados(dados)
    flash("Alteracoes salvas.", "ok")
    return redirect(url_for("painel"))


@app.route("/produtos/<produto_id>/remover", methods=["POST"])
def remover_produto(produto_id):
    dados = ler_dados()
    dados["produtos"] = [p for p in dados["produtos"] if p["id"] != produto_id]
    dados["estado"].pop(produto_id, None)
    gravar_dados(dados)
    flash("Produto retirado da lista.", "ok")
    return redirect(url_for("painel"))


@app.route("/checar", methods=["POST"])
def checar_agora():
    qtd = checar_todos()
    flash(f"{qtd} novidade(s) — os avisos foram enviados." if qtd else "Tudo checado. Nada mudou desde a ultima vez.",
          "ok" if qtd else "neutro")
    return redirect(url_for("painel"))


@app.route("/ajustes", methods=["GET", "POST"])
def ajustes():
    dados = ler_dados()
    if request.method == "POST":
        dados["intervalo_minutos"] = max(1, int(request.form.get("intervalo_minutos", 20)))
        dados["timeout_segundos"] = max(5, int(request.form.get("timeout_segundos", 15)))
        dados["avisos"] = {
            "discord_ativo": request.form.get("discord_ativo") == "sim",
            "discord_webhook": request.form.get("discord_webhook", "").strip(),
            "telegram_ativo": request.form.get("telegram_ativo") == "sim",
            "telegram_token": request.form.get("telegram_token", "").strip(),
            "telegram_chat_id": request.form.get("telegram_chat_id", "").strip(),
            "email_ativo": request.form.get("email_ativo") == "sim",
            "email_servidor": request.form.get("email_servidor", "smtp.gmail.com").strip(),
            "email_porta": request.form.get("email_porta", "587").strip(),
            "email_usuario": request.form.get("email_usuario", "").strip(),
            "email_senha": request.form.get("email_senha", "").strip(),
            "email_destino": request.form.get("email_destino", "").strip(),
        }
        gravar_dados(dados)
        flash("Ajustes salvos.", "ok")
        return redirect(url_for("ajustes"))
    return render_template("ajustes.html", config=dados)


@app.route("/ajustes/testar", methods=["POST"])
def testar_avisos():
    dados = ler_dados()
    erros = enviar_avisos(dados, "Teste do Monitor TCG — se voce esta lendo isso, funcionou.")
    flash(" . ".join(erros) if erros else "Mensagem de teste enviada. Confira seus canais.",
          "erro" if erros else "ok")
    return redirect(url_for("ajustes"))


@app.route("/icone.png")
def icone():
    return Response(base64.b64decode(ICONE_PNG_BASE64), mimetype="image/png")


@app.route("/estilo.css")
def estilo():
    return Response(CSS, mimetype="text/css")


@app.template_filter("hora")
def formatar_hora(iso):
    if not isinstance(iso, str) or not iso:
        return "—"
    try:
        momento = datetime.fromisoformat(iso).astimezone()
    except ValueError:
        return "—"
    agora = datetime.now(timezone.utc).astimezone()
    return momento.strftime("%H:%M" if momento.date() == agora.date() else "%d/%m %H:%M")


@app.template_filter("reais")
def formatar_reais(valor):
    if not isinstance(valor, (int, float)) or isinstance(valor, bool):
        return "—"
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


preparar_primeira_execucao()
iniciar_checagem_automatica()

if __name__ == "__main__":
    if not ACESSOS:
        print("  [aviso] ACESSOS (ou SENHA_APP) nao definida — o app esta SEM protecao de login.")
    if not os.environ.get("CHAVE_SECRETA"):
        print("  [aviso] CHAVE_SECRETA nao definida — todo mundo vai precisar logar de novo a cada reinício.")
    porta = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=porta, debug=False)
