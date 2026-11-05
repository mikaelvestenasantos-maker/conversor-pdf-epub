# PDF → EPUB para Kindle

Converte livros em PDF para EPUB com **texto fluido**. No Kindle, você ajusta fonte, tamanho e margem como em qualquer livro comprado, sem ficar dando zoom em página de PDF.

Gratuito e de código aberto (AGPL-3.0).

## Baixar

Vá em **Releases** e baixe:

- `ConversorEPUB-x64.exe` para a maioria dos computadores com Windows;
- `ConversorEPUB-arm64.exe` para Windows em processador ARM (Snapdragon, Surface Pro X).

Não precisa instalar nada, é só abrir. Como o programa não tem assinatura digital paga, o Windows pode mostrar "O Windows protegeu o computador". Clique em **Mais informações → Executar assim mesmo**.

## Como usar

1. Arraste os PDFs para a janela (ou clique na área para escolher).
2. Confira título e autor.
3. Clique em **Converter**. O EPUB é salvo ao lado do PDF.
4. Mande para o Kindle pelo [Send to Kindle](https://www.amazon.com/sendtokindle): pelo site, pelo e-mail do seu Kindle ou pelo app.

## O que ele faz

- Junta as linhas em parágrafos, inclusive quando um parágrafo continua na página seguinte.
- Desfaz a hifenização de fim de linha.
- Remove cabeçalhos, rodapés e números de página repetidos.
- Detecta os capítulos pelo sumário do PDF (bookmarks) ou, na falta dele, pelo tamanho da fonte, e gera o sumário navegável do Kindle.
- Mantém itálico, negrito, imagens e separadores de cena (`* * *`).
- Usa a 1ª página como capa.

## Limitações

- PDFs escaneados (páginas como imagem) precisam de OCR antes.
- Layouts em duas colunas, tabelas e fórmulas podem sair desorganizados.
- PDFs com DRM ou senha não são suportados. Converta apenas arquivos que você tem direito de usar.

## Rodar pelo código-fonte

Requer Python 3.10 ou superior.

```bash
pip install -r requirements.txt
python app.pyw
```

Pela linha de comando:

```bash
python conversor.py livro.pdf --titulo "Título" --autor "Autor"
```

Em Windows ARM, se o `pip` tentar compilar o PyMuPDF, use `pip install --only-binary=:all: pymupdf`.

## Gerar o executável

```bash
pip install pyinstaller
python build.py
```

Ao publicar uma tag `v*` no GitHub (ex.: `v1.0.0`), o workflow em `.github/workflows/release.yml` gera as versões x64 e ARM64 e cria o Release automaticamente.

## Licença

Copyright © 2026 mikaelvestenasantos-maker.

Distribuído sob a [GNU Affero General Public License v3](LICENSE). Você pode usar, estudar, modificar e redistribuir, desde que versões modificadas também sejam distribuídas com o código-fonte sob a mesma licença.

Usa [PyMuPDF](https://github.com/pymupdf/PyMuPDF) (AGPL-3.0) e [windnd](https://github.com/cilame/windnd) (MIT).

Kindle é marca registrada da Amazon.com, Inc. Este projeto não tem vínculo com a Amazon.
