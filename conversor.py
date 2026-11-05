"""
Conversor de PDF para EPUB fluido, pensado para leitura no Kindle.

Uso pela linha de comando:
    python conversor.py livro.pdf [saida.epub] [--titulo "..."] [--autor "..."]

O trabalho pesado é reconstruir a estrutura que o PDF não guarda:
parágrafos, títulos, capítulos, hifenização, cabeçalhos/rodapés repetidos.

Copyright (C) 2026 mikaelvestenasantos-maker

Este programa é software livre: você pode redistribuí-lo e/ou modificá-lo
sob os termos da GNU Affero General Public License, versão 3, publicada pela
Free Software Foundation. Ele é distribuído SEM NENHUMA GARANTIA. Veja o
arquivo LICENSE para mais detalhes.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import re
import statistics
import uuid
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pymupdf

# ---------------------------------------------------------------------------
# Modelo de dados
# ---------------------------------------------------------------------------

TERMINAIS = tuple('.!?…:;"”»\')]')


@dataclass
class Span:
    text: str
    bold: bool = False
    italic: bool = False
    sup: bool = False
    page: int = -1
    nota: int = 0          # >0: chamada da nota de rodapé de número interno `nota`
    capitular: bool = False

    def copia(self, text: str | None = None) -> "Span":
        return Span(self.text if text is None else text, self.bold, self.italic, self.sup,
                    self.page, self.nota, self.capitular)


@dataclass
class Linha:
    spans: list[Span]
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    page: int
    papel: str = "texto"  # texto | titulo | lixo
    nivel: int = 0

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)


@dataclass
class Imagem:
    data: bytes
    ext: str
    page: int
    area: float = 0.0


@dataclass
class Pagina:
    numero: int
    largura: float
    altura: float
    itens: list = field(default_factory=list)  # Linha | Imagem


@dataclass
class Elemento:
    tipo: str  # p | h | img | nota
    spans: list[Span] = field(default_factory=list)
    nivel: int = 0
    classe: str = ""
    imagem: Imagem | None = None
    pagina: int = -1
    marcador: str = ""   # para notas: "1", "*", ...
    nota_id: int = 0


class ErroConversao(Exception):
    pass


# ---------------------------------------------------------------------------
# 1. Extração
# ---------------------------------------------------------------------------

def _normalizar_espacos(t: str) -> str:
    return t.replace(" ", " ").replace("\t", " ")


def extrair_paginas(doc: pymupdf.Document, progresso) -> list[Pagina]:
    paginas = []
    flags = pymupdf.TEXT_PRESERVE_IMAGES | pymupdf.TEXT_PRESERVE_LIGATURES
    total = len(doc)
    for n, page in enumerate(doc):
        progresso(0.05 + 0.45 * n / max(total, 1), f"Lendo página {n + 1} de {total}")
        rect = page.rect
        pg = Pagina(n, rect.width, rect.height)
        d = page.get_text("dict", flags=flags)
        for bloco in d["blocks"]:
            if bloco["type"] == 1:
                img = _extrair_imagem(bloco, n, rect)
                if img:
                    pg.itens.append(img)
                continue
            linhas_bloco: list[Linha] = []
            for ln in bloco.get("lines", []):
                spans = []
                brutos = []  # (tamanho, y da linha de base) de cada trecho
                tamanhos = Counter()
                for s in ln["spans"]:
                    t = _normalizar_espacos(s["text"])
                    if not t:
                        continue
                    fonte = s.get("font", "").lower()
                    bold = bool(s["flags"] & 16) or "bold" in fonte or "black" in fonte or "heavy" in fonte
                    italic = bool(s["flags"] & 2) or "italic" in fonte or "oblique" in fonte
                    sup = bool(s["flags"] & 1)
                    spans.append(Span(t, bold, italic, sup, n))
                    brutos.append((s["size"], s.get("origin", (0, s["bbox"][3]))[1]))
                    if t.strip():
                        tamanhos[round(s["size"], 1)] += len(t.strip())
                if not spans or not tamanhos:
                    continue
                # sobrescrito "desenhado": número pequeno acima da linha de base
                dominante = tamanhos.most_common(1)[0][0]
                bases = [b for (tam, b), sp in zip(brutos, spans) if abs(tam - dominante) < 0.3]
                base = statistics.median(bases) if bases else None
                for sp, (tam, b) in zip(spans, brutos):
                    if (not sp.sup and base is not None and tam <= dominante * 0.8
                            and b < base - dominante * 0.15
                            and re.fullmatch(r"\d{1,3}|[*†‡§]{1,3}", sp.text.strip())):
                        sp.sup = True
                dx, dy = ln.get("dir", (1, 0))
                if abs(dy) > 0.2:  # texto vertical/rotacionado (marcas laterais)
                    continue
                x0, y0, x1, y1 = ln["bbox"]
                linha = Linha(spans, x0, y0, x1, y1, tamanhos.most_common(1)[0][0], n)
                # junta fragmentos na mesma linha de base
                if linhas_bloco:
                    ant = linhas_bloco[-1]
                    meio_ant = (ant.y0 + ant.y1) / 2
                    meio = (y0 + y1) / 2
                    if abs(meio - meio_ant) < min(ant.size, linha.size) * 0.35 and x0 >= ant.x1 - 2:
                        if x0 - ant.x1 > ant.size * 0.15 and not ant.text.endswith(" "):
                            ant.spans.append(Span(" ", ant.spans[-1].bold, ant.spans[-1].italic))
                        ant.spans.extend(spans)
                        ant.x1 = max(ant.x1, x1)
                        ant.y0, ant.y1 = min(ant.y0, y0), max(ant.y1, y1)
                        ant.size = max(ant.size, linha.size) if linha.size > ant.size * 1.3 else ant.size
                        continue
                linhas_bloco.append(linha)
            pg.itens.extend(linhas_bloco)
        paginas.append(pg)
    return paginas


def _extrair_imagem(bloco, n, rect) -> Imagem | None:
    data = bloco.get("image")
    if not data or bloco.get("width", 0) < 40 or bloco.get("height", 0) < 40:
        return None
    x0, y0, x1, y1 = bloco["bbox"]
    area = (x1 - x0) * (y1 - y0) / (rect.width * rect.height)
    if area < 0.015:
        return None
    ext = (bloco.get("ext") or "png").lower()
    if ext not in ("jpeg", "jpg", "png", "gif"):
        try:
            pix = pymupdf.Pixmap(data)
            if pix.n - pix.alpha > 3:
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            data, ext = pix.tobytes("png"), "png"
        except Exception:
            return None
    if ext == "jpg":
        ext = "jpeg"
    return Imagem(data, ext, n, area)


# ---------------------------------------------------------------------------
# 2. Análise: fonte do corpo, cabeçalhos/rodapés, títulos
# ---------------------------------------------------------------------------

def tamanho_corpo(paginas: list[Pagina]) -> float:
    c = Counter()
    for pg in paginas:
        for it in pg.itens:
            if isinstance(it, Linha):
                c[it.size] += len(it.text.strip())
    if not c:
        raise ErroConversao(
            "Não encontrei texto neste PDF. Ele provavelmente é escaneado "
            "(páginas como imagem) e precisaria de OCR antes da conversão."
        )
    return c.most_common(1)[0][0]


_ROMANO = re.compile(r"^[ivxlcdm]+$", re.I)


def _chave_repeticao(t: str) -> str:
    t = re.sub(r"\d+", "#", t.strip().lower())
    return re.sub(r"\s+", " ", t)


def marcar_cabecalhos_rodapes(paginas: list[Pagina]) -> None:
    contagem = defaultdict(set)
    candidatos = []
    for pg in paginas:
        linhas = [it for it in pg.itens if isinstance(it, Linha)]
        if not linhas:
            continue
        topo, base = pg.altura * 0.10, pg.altura * 0.90
        # as primeiras e últimas linhas da página que estão nas margens
        for ln in linhas[:3] + linhas[-3:]:
            if ln.y1 <= topo or ln.y0 >= base:
                candidatos.append(ln)
                contagem[_chave_repeticao(ln.text)].add(pg.numero)
    n = len(paginas)
    limite = max(3, int(n * 0.15))
    for ln in candidatos:
        t = ln.text.strip()
        so_numero = bool(re.fullmatch(r"[\-–—\s]*(\d+|[ivxlcdm]+)[\-–—\s]*", t, re.I))
        repetido = len(contagem[_chave_repeticao(t)]) >= limite
        if so_numero or (repetido and len(t) < 120):
            ln.papel = "lixo"


def _norm(t: str) -> str:
    return re.sub(r"[^\w]", "", t.lower())


def marcar_titulos_por_sumario(paginas, toc, corpo) -> bool:
    """Usa o sumário (bookmarks) do PDF, quando existe. Retorna True se usou."""
    entradas = [(lvl, titulo, pag - 1) for lvl, titulo, pag, *_ in toc if pag >= 1 and titulo.strip()]
    if len(entradas) < 2:
        return False
    max_nivel = 3
    por_pagina = defaultdict(list)
    for lvl, titulo, pag in entradas:
        if lvl <= max_nivel and 0 <= pag < len(paginas):
            por_pagina[pag].append((lvl, titulo))
    achados = 0
    for pag, lista in por_pagina.items():
        linhas = [it for it in paginas[pag].itens if isinstance(it, Linha) and it.papel == "texto"]
        for lvl, titulo in lista:
            alvo = _norm(titulo)
            if not alvo:
                continue
            for i, ln in enumerate(linhas):
                t = _norm(ln.text)
                if not t or ln.papel != "texto":
                    continue
                if alvo.startswith(t) or (t.startswith(alvo) and len(t) < len(alvo) * 1.5):
                    usadas = [ln]
                    acumulado = t
                    j = i + 1
                    while len(acumulado) < len(alvo) and j < len(linhas):
                        prox = acumulado + _norm(linhas[j].text)
                        if not alvo.startswith(prox):
                            break
                        acumulado = prox
                        usadas.append(linhas[j])
                        j += 1
                    for k, u in enumerate(usadas):
                        u.papel = "titulo"
                        u.nivel = lvl
                        u.spans = [Span(titulo.strip())] if k == 0 else []
                    achados += 1
                    break
            else:
                # título no sumário mas não achado no texto: insere no início da página
                falsa = Linha([Span(titulo.strip())], 0, -1, 0, -1, corpo, pag, "titulo", lvl)
                paginas[pag].itens.insert(0, falsa)
    return achados > 0 or bool(por_pagina)


def marcar_titulos_por_fonte(paginas, corpo) -> None:
    tamanhos = Counter()
    for pg in paginas:
        for ln in pg.itens:
            if isinstance(ln, Linha) and ln.papel == "texto":
                t = ln.text.strip()
                if ln.size >= corpo * 1.2 and 2 < len(t) < 120:
                    tamanhos[ln.size] += 1
    if not tamanhos:
        return
    # tamanhos usados demais provavelmente não são títulos (ex.: livro inteiro em fonte maior)
    distintos = sorted(tamanhos, reverse=True)
    nivel_por_tamanho = {}
    nivel = 1
    for tam in distintos:
        nivel_por_tamanho[tam] = min(nivel, 3)
        nivel += 1
    for pg in paginas:
        for ln in pg.itens:
            if isinstance(ln, Linha) and ln.papel == "texto" and ln.size in nivel_por_tamanho:
                if 2 < len(ln.text.strip()) < 120:
                    ln.papel = "titulo"
                    ln.nivel = nivel_por_tamanho[ln.size]


# ---------------------------------------------------------------------------
# 3. Montagem de parágrafos
# ---------------------------------------------------------------------------

def _anexar(spans: list[Span], novos: list[Span]) -> None:
    """Anexa uma linha ao parágrafo, desfazendo hifenização de fim de linha."""
    novos = [s.copia() for s in novos]
    if not novos:
        return
    novos[0].text = novos[0].text.lstrip()
    if not spans:
        spans.extend(novos)
        return
    ult = spans[-1]
    ult.text = ult.text.rstrip()
    prox = novos[0].text
    if ult.text.endswith("­"):
        ult.text = ult.text[:-1]
    elif re.search(r"\w-$", ult.text) and prox[:1].islower():
        ult.text = ult.text[:-1]
    elif ult.text.endswith(("-", "–", "—")) and not prox[:1].isalpha():
        pass
    else:
        ult.text += " "
    spans.extend(novos)


def _texto(spans) -> str:
    return "".join(s.text for s in spans)


def _medidas_pagina(linhas: list[Linha], corpo: float):
    corpo_ln = [l for l in linhas if abs(l.size - corpo) <= corpo * 0.15 and len(l.text.strip()) > 20]
    if len(corpo_ln) < 3:
        corpo_ln = [l for l in linhas if l.text.strip()] or linhas
    # margem esquerda: a menor posição que se repete (em páginas cheias de
    # diálogo, as linhas recuadas podem ser maioria)
    contagem = Counter(round(l.x0) for l in corpo_ln)
    minimo = max(2, len(corpo_ln) * 0.15) if len(corpo_ln) >= 4 else 1
    frequentes = sorted(x for x, n in contagem.items() if n >= minimo)
    esquerda = frequentes[0] if frequentes else (contagem.most_common(1)[0][0] if contagem else 0)
    direita = sorted(l.x1 for l in corpo_ln)[int(len(corpo_ln) * 0.9) - 1] if corpo_ln else 0
    passos = []
    for a, b in zip(corpo_ln, corpo_ln[1:]):
        d = b.y0 - a.y0
        if 0 < d < corpo * 3:
            passos.append(d)
    passo = statistics.median(passos) if passos else corpo * 1.3
    return esquerda, direita, passo


def _livro_usa_recuo(paginas: list[Pagina], corpo: float) -> bool:
    """Verdadeiro se uma fração típica das linhas do corpo começa recuada (1ª linha de parágrafo)."""
    recuadas = total = 0
    for pg in paginas:
        linhas = [it for it in pg.itens if isinstance(it, Linha) and it.papel == "texto"
                  and abs(it.size - corpo) <= corpo * 0.15]
        if len(linhas) < 5:
            continue
        esquerda, _, _ = _medidas_pagina(linhas, corpo)
        for l in linhas:
            total += 1
            if corpo * 0.8 < l.x0 - esquerda < corpo * 4:
                recuadas += 1
    return total > 0 and 0.03 < recuadas / total < 0.5


def montar_elementos(paginas: list[Pagina], corpo: float, pular_paginas: set[int]) -> list[Elemento]:
    elementos: list[Elemento] = []
    atual: list[Span] | None = None
    classe_atual = ""
    capitular = None  # letra capitular esperando o próximo parágrafo
    hashes = Counter()
    for pg in paginas:
        for it in pg.itens:
            if isinstance(it, Imagem):
                hashes[hashlib.md5(it.data).hexdigest()] += 1

    usa_recuo = _livro_usa_recuo(paginas, corpo)
    notas_pendentes: list[Elemento] = []  # notas de rodapé esperando o parágrafo atual fechar

    def fechar():
        nonlocal atual, classe_atual
        if atual and _texto(atual).strip():
            elementos.append(Elemento("p", atual, classe=classe_atual))
        atual, classe_atual = None, ""
        elementos.extend(notas_pendentes)
        notas_pendentes.clear()

    def linha_de_nota(ln: Linha):
        marcador, resto = _separar_marcador(ln.spans)
        if marcador:
            nota = Elemento("nota", [], pagina=ln.page, marcador=marcador)
            _anexar(nota.spans, resto)
            notas_pendentes.append(nota)
        elif notas_pendentes and notas_pendentes[-1].pagina == ln.page:
            _anexar(notas_pendentes[-1].spans, ln.spans)  # continuação da nota
        else:
            nota = Elemento("nota", [], pagina=ln.page)
            _anexar(nota.spans, ln.spans)
            notas_pendentes.append(nota)

    for pg in paginas:
        if pg.numero in pular_paginas:
            continue
        linhas = [it for it in pg.itens if isinstance(it, Linha) and it.papel == "texto"]
        # rodapé de notas: linhas menores que o corpo, abaixo da última linha do corpo
        corpo_pg = [l.y1 for l in linhas if abs(l.size - corpo) <= corpo * 0.12 and len(l.text.strip()) > 3]
        limite_rodape = max(corpo_pg) if corpo_pg else None
        esquerda, direita, passo = _medidas_pagina(linhas, corpo) if linhas else (0, pg.largura, corpo)
        centro = (esquerda + direita) / 2 if linhas else pg.largura / 2
        largura_texto = max(direita - esquerda, 1)
        anterior: Linha | None = None
        primeira_da_pagina = True

        for it in pg.itens:
            if isinstance(it, Imagem):
                if hashes[hashlib.md5(it.data).hexdigest()] > 3:
                    continue  # logotipo/ornamento repetido
                fechar()
                elementos.append(Elemento("img", imagem=it))
                anterior = None
                continue
            ln: Linha = it
            if ln.papel == "lixo":
                continue
            if ln.papel == "titulo":
                fechar()
                if not ln.spans:
                    continue  # continuação de título já incorporado
                txt = ln.text.strip()
                if (elementos and elementos[-1].tipo == "h" and elementos[-1].nivel == ln.nivel
                        and anterior is not None and anterior.papel == "titulo"
                        and ln.y0 - anterior.y1 < ln.size * 1.2):
                    _anexar(elementos[-1].spans, [Span(txt)])  # título em várias linhas
                else:
                    elementos.append(Elemento("h", [Span(txt)], nivel=ln.nivel))
                anterior = ln
                primeira_da_pagina = False
                continue

            txt = ln.text.strip()
            if not txt:
                continue

            if (limite_rodape is not None and ln.y0 >= limite_rodape - 1
                    and ln.size < corpo * 0.9 and ln.y0 > pg.altura * 0.5):
                linha_de_nota(ln)
                continue

            # capitular: letra grande isolada no início do parágrafo
            if len(txt) <= 2 and ln.size >= corpo * 1.6 and txt[0].isalpha():
                fechar()
                capitular = ln.spans
                anterior = None
                primeira_da_pagina = False
                continue

            centralizada = (abs((ln.x0 + ln.x1) / 2 - centro) < corpo * 1.5
                            and (ln.x1 - ln.x0) < largura_texto * 0.7
                            and ln.x0 - esquerda > corpo * 2)
            separador = bool(re.fullmatch(r"[\*\s#~•·◆◇❖✦—–\-_=]{1,15}", txt)) and not txt.isalnum()

            if separador:
                fechar()
                elementos.append(Elemento("p", [Span("* * *")], classe="cena"))
                anterior = None
                primeira_da_pagina = False
                continue

            novo = False
            if atual is None:
                novo = True
            elif primeira_da_pagina:
                # continuação entre páginas
                fim = _texto(atual).rstrip()
                recuado = ln.x0 - esquerda > corpo * 0.8
                if usa_recuo:
                    # livro com recuo: sem recuo na 1ª linha = mesmo parágrafo
                    continua = not recuado or txt[:1].islower()
                else:
                    continua = not fim.endswith(TERMINAIS) or txt[:1].islower()
                novo = not continua
            elif anterior is None:
                novo = True
            else:
                fim = _texto(atual).rstrip()
                salto = ln.y0 - anterior.y0
                if salto > passo * 1.45 or salto < 0:
                    novo = True
                elif ln.x0 - esquerda > corpo * 0.8 and not centralizada:
                    novo = True  # recuo de primeira linha
                elif not usa_recuo and anterior.x1 < direita - corpo * 2.5 and fim.endswith(TERMINAIS):
                    novo = True  # linha anterior curta terminando frase
                elif abs(ln.size - anterior.size) > corpo * 0.2:
                    novo = True
                elif centralizada != (classe_atual == "centro"):
                    novo = True

            if novo:
                fechar()
                atual = []
                if ln.size < corpo * 0.85:
                    classe_atual = "nota"
                elif centralizada:
                    classe_atual = "centro"
                if capitular:
                    atual.extend(Span(s.text.strip(), s.bold, s.italic) for s in capitular)
                    capitular = None
                    atual.extend(s.copia() for s in ln.spans)
                    atual[len(atual) - len(ln.spans)].text = atual[len(atual) - len(ln.spans)].text.lstrip()
                else:
                    _anexar(atual, ln.spans)
            else:
                _anexar(atual, ln.spans)
            anterior = ln
            primeira_da_pagina = False
    fechar()
    return elementos


_MARCADOR = re.compile(r"^\s*(\d{1,3}|[*†‡§]{1,3})[.)]?(?=\s|[^\w\s]|[A-ZÀ-Ú])\s*")


def _separar_marcador(spans: list[Span]) -> tuple[str, list[Span]]:
    """Separa o número/símbolo do início de uma linha de nota de rodapé."""
    spans = [s.copia() for s in spans]
    while spans and not spans[0].text.strip():
        spans.pop(0)
    if not spans:
        return "", spans
    primeiro = spans[0]
    if primeiro.sup:
        m = primeiro.text.strip().rstrip(".)")
        if re.fullmatch(r"\d{1,3}|[*†‡§]{1,3}", m):
            return m, spans[1:]
    # o número pode estar num trecho separado do texto: olha a linha inteira
    m = _MARCADOR.match(_texto(spans))
    if m:
        cortar = m.end()
        while spans and cortar > 0:
            if len(spans[0].text) <= cortar:
                cortar -= len(spans[0].text)
                spans.pop(0)
            else:
                spans[0].text = spans[0].text[cortar:]
                cortar = 0
        return m.group(1), spans
    return "", spans


def ligar_notas(elementos: list[Elemento]) -> int:
    """Liga as chamadas sobrescritas do texto às notas do rodapé da mesma página.

    Notas sem chamada correspondente voltam a ser parágrafos comuns (pequenos).
    Retorna quantas notas foram ligadas.
    """
    notas = {}
    for e in elementos:
        if e.tipo == "nota" and e.marcador:
            notas.setdefault((e.pagina, e.marcador), e)
    proximo = 1
    if notas:
        for e in elementos:
            if e.tipo not in ("p", "h"):
                continue
            for s in e.spans:
                if not s.sup:
                    continue
                m = s.text.strip().rstrip(".,;)")
                alvo = notas.get((s.page, m))
                if alvo and not alvo.nota_id:
                    alvo.nota_id = proximo
                    s.nota = proximo
                    s.text = m
                    proximo += 1
    for e in elementos:
        if e.tipo == "nota" and not e.nota_id:
            e.tipo, e.classe = "p", "nota"
            if e.marcador:
                e.spans.insert(0, Span(e.marcador + " "))
    return proximo - 1


# ---------------------------------------------------------------------------
# 4. Capítulos e EPUB
# ---------------------------------------------------------------------------

def tipografia(spans: list[Span], lang: str) -> list[Span]:
    """Aspas curvas, travessões e reticências, sem mexer nas palavras."""
    travessao = "—" if not lang.startswith("en") else "–"
    saida = []
    ant = " "  # caractere anterior, atravessando os trechos
    for i, s in enumerate(spans):
        t = s.text
        if s.sup or s.nota:
            saida.append(s.copia())
            ant = t[-1:] or ant
            continue
        t = t.replace("...", "…").replace("---", "—").replace("--", "—")
        t = re.sub(r"(?<=\S) - (?=\S)", f" {travessao} ", t)
        if i == 0 or not "".join(x.text for x in spans[:i]).strip():
            # travessão de diálogo no início do parágrafo
            t = re.sub(r"^(\s*)[-–](\s?)(?=\S)", lambda m: m.group(1) + "— ", t, count=1)
        chars = []
        for ch in t:
            if ch == '"':
                ch = "“" if (ant.isspace() or ant in "([{—–-/“‘") else "”"
            elif ch == "'":
                ch = "‘" if (ant.isspace() or ant in "([{—–-/“") else "’"
            chars.append(ch)
            ant = ch
        saida.append(s.copia("".join(chars)))
    return saida


def marcar_capitular(spans: list[Span]) -> list[Span]:
    """Destaca a primeira letra do parágrafo (só se começar com letra)."""
    spans = [s.copia() for s in spans]
    for i, s in enumerate(spans):
        t = s.text.lstrip()
        if not t:
            continue
        if not t[0].isalpha() or s.sup:
            return spans
        resto = t[1:]
        spans[i:i + 1] = [s.copia(t[0]), s.copia(resto)]
        spans[i].capitular = True
        return spans
    return spans


def _spans_html(spans: list[Span]) -> str:
    # junta trechos vizinhos com o mesmo estilo
    def chave(s):
        return (s.bold, s.italic, s.sup, s.nota, s.capitular)

    grupos: list[Span] = []
    for s in spans:
        if grupos and chave(grupos[-1]) == chave(s) and not s.nota and not s.capitular:
            grupos[-1] = grupos[-1].copia(grupos[-1].text + s.text)
        else:
            grupos.append(s.copia())
    partes = []
    for g in grupos:
        t = html.escape(re.sub(r"\s+", " ", g.text), quote=False)
        if not t.strip():
            partes.append(t)
            continue
        esp_ini = " " if t[0] == " " else ""
        esp_fim = " " if t[-1] == " " else ""
        t = t.strip()
        if g.nota:
            t = (f'<sup><a epub:type="noteref" id="r{g.nota}" href="@@NOTA{g.nota}@@" '
                 f'class="chamada">{t}</a></sup>')
        elif g.sup:
            t = f"<sup>{t}</sup>"
        if g.capitular:
            t = f'<span class="capitular">{t}</span>'
        if g.italic:
            t = f"<i>{t}</i>"
        if g.bold:
            t = f"<b>{t}</b>"
        partes.append(esp_ini + t + esp_fim)
    return "".join(partes).strip()


@dataclass
class Capitulo:
    titulo: str
    corpo: list[str] = field(default_factory=list)
    toc: list[tuple[int, str, str]] = field(default_factory=list)  # (nivel, titulo, ancora)
    notas: list[str] = field(default_factory=list)
    tamanho: int = 0


def dividir_capitulos(elementos: list[Elemento], imagens_out: dict, lang: str = "pt-BR",
                      capitulares: bool = True) -> list[Capitulo]:
    niveis = [e.nivel for e in elementos if e.tipo == "h"]
    nivel_corte = min(niveis) if niveis else None
    # se só há um título do nível mais alto, divide pelo próximo nível
    if niveis and niveis.count(nivel_corte) <= 1 and len(set(niveis)) > 1:
        nivel_corte = sorted(set(niveis))[1]
    caps: list[Capitulo] = []
    cap = Capitulo("Início")
    ancora_n = 0
    LIMITE = 200_000
    espera_capitular = False
    onde_ref: dict[int, int] = {}   # nota -> índice do capítulo da chamada
    onde_nota: dict[int, int] = {}  # nota -> índice do capítulo onde a nota fica

    for i, e in enumerate(elementos):
        if e.tipo == "h":
            titulo = re.sub(r"\s+", " ", _texto(e.spans)).strip()
            anterior_e_titulo = i > 0 and elementos[i - 1].tipo == "h"
            if e.nivel <= (nivel_corte or 0) and not anterior_e_titulo and cap.corpo:
                caps.append(cap)
                cap = Capitulo(titulo)
            elif not cap.corpo:
                cap.titulo = titulo if cap.titulo == "Início" else cap.titulo
            ancora_n += 1
            ancora = f"t{ancora_n}"
            nivel_html = min(max(e.nivel - (nivel_corte or 1) + 1, 1), 3) if nivel_corte else 2
            cap.corpo.append(f'<h{nivel_html} id="{ancora}">{_spans_html(tipografia(e.spans, lang))}</h{nivel_html}>')
            cap.toc.append((e.nivel, titulo, ancora))
            if nivel_html == 1:
                espera_capitular = capitulares
        elif e.tipo == "img":
            nome = f"img{len(imagens_out) + 1:04d}.{'jpg' if e.imagem.ext == 'jpeg' else e.imagem.ext}"
            imagens_out[nome] = e.imagem
            cap.corpo.append(f'<div class="imagem"><img src="../images/{nome}" alt=""/></div>')
        elif e.tipo == "nota":
            conteudo = _spans_html(tipografia(e.spans, lang))
            onde_nota[e.nota_id] = len(caps)
            cap.notas.append(
                f'<aside epub:type="footnote" id="n{e.nota_id}"><p class="nota">'
                f'<a href="@@REF{e.nota_id}@@">{html.escape(e.marcador)}</a> {conteudo}</p></aside>'
            )
        else:
            spans = tipografia(e.spans, lang)
            if espera_capitular and e.classe == "":
                spans = marcar_capitular(spans)
            if e.classe not in ("cena",):
                espera_capitular = False
            for s in spans:
                if s.nota:
                    onde_ref[s.nota] = len(caps)
            conteudo = _spans_html(spans)
            if not conteudo:
                continue
            cls = f' class="{e.classe}"' if e.classe else ""
            linha = f"<p{cls}>{conteudo}</p>"
            cap.corpo.append(linha)
            cap.tamanho += len(linha)
            if cap.tamanho > LIMITE:
                caps.append(cap)
                cap = Capitulo(cap.titulo)
    if cap.corpo or cap.notas:
        caps.append(cap)

    # notas no fim de cada capítulo + links entre arquivos quando necessário
    for idx, c in enumerate(caps):
        if c.notas:
            c.corpo.append('<section class="notas" epub:type="footnotes">')
            c.corpo.extend(c.notas)
            c.corpo.append("</section>")

    def destino(idx_alvo, idx_atual, ancora):
        return f"#{ancora}" if idx_alvo == idx_atual else f"cap{idx_alvo + 1:03d}.xhtml#{ancora}"

    for idx, c in enumerate(caps):
        texto = "\n".join(c.corpo)
        texto = re.sub(r"@@NOTA(\d+)@@",
                       lambda m: destino(onde_nota.get(int(m.group(1)), idx), idx, f"n{m.group(1)}"), texto)
        texto = re.sub(r"@@REF(\d+)@@",
                       lambda m: destino(onde_ref.get(int(m.group(1)), idx), idx, f"r{m.group(1)}"), texto)
        c.corpo = [texto]
    return caps


CSS = """\
@charset "UTF-8";
html, body { margin: 0; padding: 0; }
body { line-height: 1.4; orphans: 2; widows: 2; }
p { margin: 0; text-indent: 1.5em; text-align: justify; }
h1, h2, h3 { text-align: center; line-height: 1.25; font-weight: bold;
  page-break-after: avoid; text-indent: 0; hyphens: none; -webkit-hyphens: none; }
h1 { font-size: 1.6em; margin: 2em 0 1.2em 0; page-break-before: always; }
h2 { font-size: 1.3em; margin: 1.6em 0 1em 0; }
h3 { font-size: 1.1em; margin: 1.2em 0 0.8em 0; }
h1 + p, h2 + p, h3 + p, p.cena + p, div.imagem + p { text-indent: 0; }
p.cena { text-align: center; text-indent: 0; margin: 1em 0; }
p.centro { text-align: center; text-indent: 0; margin: 0.6em 0; }
p.nota { font-size: 0.85em; text-indent: 0; margin: 0.4em 0; }
div.imagem { text-align: center; margin: 1em 0; page-break-inside: avoid; }
div.imagem img { max-width: 100%; height: auto; }
sup { font-size: 0.7em; line-height: 0; }
a.chamada { text-decoration: none; }
span.capitular { float: left; font-size: 3.2em; line-height: 0.85; margin: 0.06em 0.08em 0 0; }
section.notas { margin-top: 2em; padding-top: 0.5em; border-top: 1px solid #999; }
aside { margin: 0.4em 0; }
aside p.nota { margin: 0; }
aside p.nota a { text-decoration: none; font-weight: bold; }
body.capa { text-align: center; }
body.capa img { max-width: 100%; max-height: 100%; }
"""


def _xhtml(titulo: str, corpo: str, lang: str, classe_body: str = "") -> str:
    cb = f' class="{classe_body}"' if classe_body else ""
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
        f'lang="{lang}" xml:lang="{lang}">\n<head>\n<meta charset="utf-8"/>\n'
        f"<title>{html.escape(titulo)}</title>\n"
        '<link rel="stylesheet" type="text/css" href="../style.css"/>\n</head>\n'
        f"<body{cb}>\n{corpo}\n</body>\n</html>\n"
    )


def _arvore_nav(entradas):
    """entradas: lista de (nivel, titulo, href). Gera <ol> aninhado."""
    if not entradas:
        return '<ol><li><a href="text/cap001.xhtml">Início</a></li></ol>'
    base = min(n for n, _, _ in entradas)
    saida, prof = [], 0
    for nivel, titulo, href in entradas:
        n = max(1, min(nivel - base + 1, prof + 1))  # nunca pula mais de um nível
        if n > prof:
            saida.append("<ol>")
        else:
            saida.append("</li>")
            while prof > n:
                saida.append("</ol></li>")
                prof -= 1
        prof = n
        saida.append(f'<li><a href="{href}">{html.escape(titulo)}</a>')
    saida.append("</li>")
    while prof > 1:
        saida.append("</ol></li>")
        prof -= 1
    saida.append("</ol>")
    return "\n".join(saida)


def _ncx_navpoints(entradas):
    pontos = []
    for i, (_, titulo, href) in enumerate(entradas, 1):
        pontos.append(
            f'<navPoint id="np{i}" playOrder="{i}"><navLabel><text>{html.escape(titulo)}</text>'
            f'</navLabel><content src="{href}"/></navPoint>'
        )
    return "\n".join(pontos)


def detectar_idioma(texto: str) -> str:
    palavras = re.findall(r"[a-zà-ú]+", texto.lower()[:200_000])
    c = Counter(palavras)
    pontos = {
        "pt-BR": sum(c[w] for w in ("não", "que", "uma", "com", "para", "você", "são", "também", "ele", "ela")),
        "en": sum(c[w] for w in ("the", "and", "of", "to", "is", "that", "with", "was", "for", "it")),
        "es": sum(c[w] for w in ("el", "los", "las", "y", "una", "con", "para", "pero", "está", "muy")),
    }
    return max(pontos, key=pontos.get) if any(pontos.values()) else "pt-BR"


def escrever_epub(destino: Path, titulo: str, autor: str, lang: str, capitulos: list[Capitulo],
                  imagens: dict, capa: bytes | None) -> None:
    uid = f"urn:uuid:{uuid.uuid4()}"
    modificado = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest, spine, nav_entradas = [], [], []

    with zipfile.ZipFile(destino, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)

        def add(nome, conteudo):
            z.writestr(nome, conteudo, compress_type=zipfile.ZIP_DEFLATED)

        add("META-INF/container.xml",
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles>\n</container>\n")
        add("OEBPS/style.css", CSS)
        manifest.append('<item id="css" href="style.css" media-type="text/css"/>')

        if capa:
            add("OEBPS/images/capa.jpg", capa)
            manifest.append('<item id="cover-image" href="images/capa.jpg" media-type="image/jpeg" properties="cover-image"/>')
            add("OEBPS/text/capa.xhtml", _xhtml(titulo, '<div><img src="../images/capa.jpg" alt="Capa"/></div>', lang, "capa"))
            manifest.append('<item id="capa" href="text/capa.xhtml" media-type="application/xhtml+xml"/>')
            spine.append('<itemref idref="capa" linear="yes"/>')

        for nome, img in imagens.items():
            mt = {"jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif"}[img.ext]
            iid = "im_" + nome.split(".")[0]
            add(f"OEBPS/images/{nome}", img.data)
            manifest.append(f'<item id="{iid}" href="images/{nome}" media-type="{mt}"/>')

        for i, cap in enumerate(capitulos, 1):
            arq = f"cap{i:03d}.xhtml"
            add(f"OEBPS/text/{arq}", _xhtml(cap.titulo, "\n".join(cap.corpo), lang))
            manifest.append(f'<item id="c{i}" href="text/{arq}" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="c{i}"/>')
            if cap.toc:
                for nivel, t, ancora in cap.toc:
                    nav_entradas.append((nivel, t, f"text/{arq}#{ancora}"))
            elif i == 1 or not capitulos[i - 2].titulo == cap.titulo:
                nav_entradas.append((1, cap.titulo, f"text/{arq}"))

        if not nav_entradas and capitulos:
            nav_entradas = [(1, c.titulo, f"text/cap{i:03d}.xhtml") for i, c in enumerate(capitulos, 1)]

        nav = (
            '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{lang}" xml:lang="{lang}">\n'
            '<head><meta charset="utf-8"/><title>Sumário</title></head>\n<body>\n'
            f'<nav epub:type="toc" id="toc"><h1>Sumário</h1>\n{_arvore_nav(nav_entradas)}\n</nav>\n'
            '<nav epub:type="landmarks" hidden=""><ol>'
            + (f'<li><a epub:type="cover" href="text/capa.xhtml">Capa</a></li>' if capa else "")
            + (f'<li><a epub:type="bodymatter" href="text/cap001.xhtml">Início</a></li>' if capitulos else "")
            + "</ol></nav>\n</body>\n</html>\n"
        )
        add("OEBPS/nav.xhtml", nav)
        manifest.append('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')

        ncx = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
            f'<head><meta name="dtb:uid" content="{uid}"/><meta name="dtb:depth" content="1"/>'
            '<meta name="dtb:totalPageCount" content="0"/><meta name="dtb:maxPageNumber" content="0"/></head>\n'
            f"<docTitle><text>{html.escape(titulo)}</text></docTitle>\n"
            f"<navMap>\n{_ncx_navpoints(nav_entradas)}\n</navMap>\n</ncx>\n"
        )
        add("OEBPS/toc.ncx", ncx)
        manifest.append('<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')

        opf = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">\n'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
            f'<dc:identifier id="bookid">{uid}</dc:identifier>\n'
            f"<dc:title>{html.escape(titulo)}</dc:title>\n"
            f"<dc:creator>{html.escape(autor or 'Desconhecido')}</dc:creator>\n"
            f"<dc:language>{lang}</dc:language>\n"
            f'<meta property="dcterms:modified">{modificado}</meta>\n'
            + ('<meta name="cover" content="cover-image"/>\n' if capa else "")
            + "</metadata>\n<manifest>\n" + "\n".join(manifest) + "\n</manifest>\n"
            '<spine toc="ncx">\n' + "\n".join(spine) + "\n</spine>\n</package>\n"
        )
        add("OEBPS/content.opf", opf)


# ---------------------------------------------------------------------------
# Ponto de entrada
# ---------------------------------------------------------------------------

@dataclass
class Resultado:
    destino: Path
    titulo: str
    autor: str
    paginas: int
    capitulos: int
    imagens: int
    avisos: list[str]
    notas: int = 0


def ler_metadados(pdf: Path) -> tuple[str, str]:
    with pymupdf.open(pdf) as doc:
        meta = doc.metadata or {}
    titulo = (meta.get("title") or "").strip()
    autor = (meta.get("author") or "").strip()
    if not titulo or titulo.lower() in ("untitled", "sem título") or titulo.lower().endswith((".doc", ".docx", ".pdf")):
        titulo = pdf.stem.replace("_", " ").strip()
    return titulo, autor


def converter(pdf: Path | str, destino: Path | str | None = None, titulo: str | None = None,
              autor: str | None = None, usar_capa: bool = True,
              progresso: Callable[[float, str], None] | None = None,
              capitulares: bool = True) -> Resultado:
    pdf = Path(pdf)
    progresso = progresso or (lambda f, m: None)
    destino = Path(destino) if destino else pdf.with_suffix(".epub")
    t_meta, a_meta = ler_metadados(pdf)
    titulo = (titulo or "").strip() or t_meta
    autor = (autor or "").strip() or a_meta
    avisos = []

    progresso(0.02, "Abrindo o PDF")
    try:
        doc = pymupdf.open(pdf)
    except Exception as e:
        raise ErroConversao(f"Não consegui abrir o PDF: {e}")
    if doc.needs_pass:
        raise ErroConversao("Este PDF é protegido por senha.")
    with doc:
        paginas = extrair_paginas(doc, progresso)
        corpo = tamanho_corpo(paginas)
        total_chars = sum(len(l.text) for p in paginas for l in p.itens if isinstance(l, Linha))
        if total_chars < 40 * len(paginas):
            avisos.append("Pouco texto encontrado: partes do PDF podem ser escaneadas (sem OCR).")

        progresso(0.55, "Removendo cabeçalhos e números de página")
        marcar_cabecalhos_rodapes(paginas)

        progresso(0.6, "Identificando capítulos")
        if not marcar_titulos_por_sumario(paginas, doc.get_toc(simple=True), corpo):
            marcar_titulos_por_fonte(paginas, corpo)

        capa = None
        pular = set()
        if usar_capa and len(doc) > 0:
            progresso(0.65, "Gerando capa")
            p0 = doc[0]
            zoom = 1400 / max(p0.rect.height, 1)
            pix = p0.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            capa = pix.tobytes("jpeg", jpg_quality=85)
            texto_p0 = sum(len(l.text) for l in paginas[0].itens if isinstance(l, Linha))
            if texto_p0 < 400:
                pular.add(0)  # a 1ª página é a própria capa

        progresso(0.75, "Reconstruindo parágrafos")
        elementos = montar_elementos(paginas, corpo, pular)
        n_notas = ligar_notas(elementos)

    lang = detectar_idioma(" ".join(_texto(e.spans) for e in elementos if e.tipo == "p"))
    progresso(0.85, "Montando capítulos")
    imagens: dict[str, Imagem] = {}
    capitulos = dividir_capitulos(elementos, imagens, lang, capitulares)
    if not capitulos:
        raise ErroConversao("Não sobrou conteúdo para o EPUB depois da análise.")
    if not any(c.toc for c in capitulos):
        avisos.append("Não identifiquei capítulos; o sumário terá só divisões automáticas.")

    progresso(0.93, "Gravando EPUB")
    escrever_epub(destino, titulo, autor, lang, capitulos, imagens, capa)
    progresso(1.0, "Pronto!")
    return Resultado(destino, titulo, autor, len(paginas), len(capitulos), len(imagens), avisos, n_notas)


def main():
    ap = argparse.ArgumentParser(description="Converte PDF em EPUB fluido para Kindle.")
    ap.add_argument("pdf")
    ap.add_argument("saida", nargs="?")
    ap.add_argument("--titulo")
    ap.add_argument("--autor")
    ap.add_argument("--sem-capa", action="store_true")
    ap.add_argument("--sem-capitulares", action="store_true")
    a = ap.parse_args()
    r = converter(a.pdf, a.saida, a.titulo, a.autor, not a.sem_capa,
                  lambda f, m: print(f"[{f * 100:5.1f}%] {m}"), not a.sem_capitulares)
    print(f"\nEPUB criado: {r.destino}\n{r.capitulos} capítulo(s), {r.imagens} imagem(ns), "
          f"{r.notas} nota(s) de rodapé, {r.paginas} páginas.")
    for av in r.avisos:
        print("Aviso:", av)


if __name__ == "__main__":
    main()
