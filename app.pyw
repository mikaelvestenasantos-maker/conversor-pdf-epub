"""Interface gráfica do conversor PDF → EPUB para Kindle.

Copyright (C) 2026 mikaelvestenasantos-maker
Licenciado sob a GNU Affero General Public License v3. Veja o arquivo LICENSE.
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conversor import ErroConversao, converter, ler_metadados  # noqa: E402

try:
    import windnd
except ImportError:  # arrastar e soltar é opcional
    windnd = None

try:  # texto nítido em telas com escala
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

VERSAO = "1.0.0"
URL_CODIGO = ""  # preencher com o endereço do repositório quando for publicado

COR_FUNDO = "#f5f3ef"
COR_ZONA = "#ffffff"
COR_BORDA = "#c9c2b6"
COR_DESTAQUE = "#2f5d8a"
COR_TEXTO = "#2b2b2b"
COR_SUAVE = "#6b665e"


class Livro:
    def __init__(self, caminho: Path):
        self.caminho = caminho
        self.titulo, self.autor = ler_metadados(caminho)
        self.status = "Na fila"
        self.saida: Path | None = None


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.livros: dict[str, Livro] = {}
        self.fila: queue.Queue = queue.Queue()
        self.convertendo = False
        self.pasta_saida: Path | None = None

        root.title("PDF → EPUB para Kindle")
        root.configure(bg=COR_FUNDO)

        st = ttk.Style()
        st.theme_use("clam")
        st.configure(".", background=COR_FUNDO, foreground=COR_TEXTO, font=("Segoe UI", 10))
        st.configure("TLabel", background=COR_FUNDO)
        st.configure("Sub.TLabel", foreground=COR_SUAVE)
        st.configure("Titulo.TLabel", font=("Segoe UI Semibold", 16))
        st.configure("TCheckbutton", background=COR_FUNDO)
        st.configure("Treeview", rowheight=28, background="white", fieldbackground="white")
        st.configure("Treeview.Heading", font=("Segoe UI Semibold", 9))
        st.configure("Primario.TButton", font=("Segoe UI Semibold", 10), foreground="white",
                     background=COR_DESTAQUE, padding=(16, 8))
        st.map("Primario.TButton", background=[("disabled", "#9fb3c8"), ("active", "#244a6f")])
        st.configure("TButton", padding=(12, 6))
        st.configure("Horizontal.TProgressbar", background=COR_DESTAQUE, troughcolor="#e4dfd6")

        principal = ttk.Frame(root, padding=20)
        principal.pack(fill="both", expand=True)

        ttk.Label(principal, text="PDF → EPUB para Kindle", style="Titulo.TLabel").pack(anchor="w")
        ttk.Label(principal, text="O texto é refeito em parágrafos fluidos para você ajustar fonte e tamanho no Kindle.",
                  style="Sub.TLabel").pack(anchor="w", pady=(2, 14))

        # zona de soltar
        self.zona = tk.Label(
            principal, text="Arraste seus PDFs para cá\nou clique para escolher",
            bg=COR_ZONA, fg=COR_SUAVE, font=("Segoe UI", 12), cursor="hand2",
            highlightthickness=2, highlightbackground=COR_BORDA, height=4,
        )
        self.zona.pack(fill="x")
        self.zona.bind("<Button-1>", lambda e: self.escolher())
        self.zona.bind("<Enter>", lambda e: self.zona.config(highlightbackground=COR_DESTAQUE))
        self.zona.bind("<Leave>", lambda e: self.zona.config(highlightbackground=COR_BORDA))
        if windnd:
            windnd.hook_dropfiles(root, func=self._ao_soltar)

        # lista
        quadro = ttk.Frame(principal)
        quadro.pack(fill="both", expand=True, pady=(14, 8))
        self.tabela = ttk.Treeview(quadro, columns=("titulo", "autor", "status"), show="headings",
                                   selectmode="browse", height=6)
        for col, nome, larg in (("titulo", "Título", 300), ("autor", "Autor", 180), ("status", "Status", 150)):
            self.tabela.heading(col, text=nome, anchor="w")
            self.tabela.column(col, width=larg, anchor="w")
        rolagem = ttk.Scrollbar(quadro, orient="vertical", command=self.tabela.yview)
        self.tabela.configure(yscrollcommand=rolagem.set)
        self.tabela.pack(side="left", fill="both", expand=True)
        rolagem.pack(side="right", fill="y")
        self.tabela.bind("<<TreeviewSelect>>", self._ao_selecionar)
        self.tabela.bind("<Delete>", lambda e: self.remover())

        # edição de título/autor
        edicao = ttk.Frame(principal)
        edicao.pack(fill="x", pady=(4, 8))
        self.var_titulo = tk.StringVar()
        self.var_autor = tk.StringVar()
        ttk.Label(edicao, text="Título").grid(row=0, column=0, sticky="w")
        self.ent_titulo = ttk.Entry(edicao, textvariable=self.var_titulo)
        self.ent_titulo.grid(row=0, column=1, sticky="ew", padx=(8, 16))
        ttk.Label(edicao, text="Autor").grid(row=0, column=2, sticky="w")
        self.ent_autor = ttk.Entry(edicao, textvariable=self.var_autor, width=24)
        self.ent_autor.grid(row=0, column=3, sticky="ew", padx=(8, 0))
        edicao.columnconfigure(1, weight=3)
        edicao.columnconfigure(3, weight=2)
        self.var_titulo.trace_add("write", lambda *a: self._editar("titulo"))
        self.var_autor.trace_add("write", lambda *a: self._editar("autor"))
        self._habilitar_edicao(False)

        # opções
        opcoes = ttk.Frame(principal)
        opcoes.pack(fill="x", pady=(0, 8))
        self.var_capa = tk.BooleanVar(value=True)
        ttk.Checkbutton(opcoes, text="Usar a 1ª página como capa", variable=self.var_capa).pack(side="left")
        self.lbl_saida = ttk.Label(opcoes, text="Salvar: na pasta de cada PDF", style="Sub.TLabel")
        self.lbl_saida.pack(side="right")
        ttk.Button(opcoes, text="Mudar…", command=self.escolher_saida).pack(side="right", padx=(0, 8))

        # progresso
        self.progresso = ttk.Progressbar(principal, mode="determinate", maximum=1.0)
        self.progresso.pack(fill="x", pady=(4, 4))
        self.lbl_status = ttk.Label(principal, text="Adicione um PDF para começar.", style="Sub.TLabel")
        self.lbl_status.pack(anchor="w")

        # botões
        botoes = ttk.Frame(principal)
        botoes.pack(fill="x", pady=(12, 0))
        self.btn_converter = ttk.Button(botoes, text="Converter", style="Primario.TButton", command=self.converter)
        self.btn_converter.pack(side="right")
        self.btn_abrir = ttk.Button(botoes, text="Abrir pasta do EPUB", command=self.abrir_pasta, state="disabled")
        self.btn_abrir.pack(side="right", padx=8)
        ttk.Button(botoes, text="Remover", command=self.remover).pack(side="left")
        ttk.Button(botoes, text="Como mandar pro Kindle?", command=self.ajuda_kindle).pack(side="left", padx=8)
        ttk.Button(botoes, text="Sobre", command=self.sobre).pack(side="left")

        for arg in sys.argv[1:]:  # permite "Abrir com…"
            self.adicionar([arg])
        self.root.after(100, self._ler_fila)
        root.update_idletasks()
        root.minsize(root.winfo_reqwidth(), root.winfo_reqheight())

    # ---- arquivos -------------------------------------------------------
    def escolher(self):
        if self.convertendo:
            return
        arquivos = filedialog.askopenfilenames(title="Escolha os PDFs", filetypes=[("PDF", "*.pdf")])
        self.adicionar(arquivos)

    def _ao_soltar(self, arquivos):
        caminhos = []
        for f in arquivos:
            if isinstance(f, bytes):
                try:
                    f = f.decode("utf-8")
                except UnicodeDecodeError:
                    f = f.decode("mbcs", errors="replace")
            caminhos.append(f)
        self.root.after(0, lambda: self.adicionar(caminhos))

    def adicionar(self, caminhos):
        if self.convertendo:
            return
        novos = 0
        for c in caminhos:
            p = Path(c)
            if p.is_dir():
                self.adicionar(sorted(str(x) for x in p.glob("*.pdf")))
                continue
            if p.suffix.lower() != ".pdf" or not p.exists():
                continue
            chave = str(p.resolve())
            if chave in self.livros:
                continue
            try:
                livro = Livro(p)
            except Exception as e:
                messagebox.showerror("Não consegui abrir", f"{p.name}\n\n{e}")
                continue
            self.livros[chave] = livro
            self.tabela.insert("", "end", iid=chave, values=(livro.titulo, livro.autor, livro.status))
            novos += 1
        if novos:
            ultimo = list(self.livros)[-1]
            self.tabela.selection_set(ultimo)
            self.lbl_status.config(text=f"{len(self.livros)} livro(s) na lista. Confira título e autor e clique em Converter.")

    def remover(self):
        if self.convertendo:
            return
        for iid in self.tabela.selection():
            self.tabela.delete(iid)
            self.livros.pop(iid, None)
        self._habilitar_edicao(False)

    def escolher_saida(self):
        pasta = filedialog.askdirectory(title="Onde salvar os EPUBs?")
        if pasta:
            self.pasta_saida = Path(pasta)
            self.lbl_saida.config(text=f"Salvar: {self._encurtar(pasta)}")

    @staticmethod
    def _encurtar(t, n=40):
        return t if len(t) <= n else "…" + t[-(n - 1):]

    # ---- edição ---------------------------------------------------------
    def _habilitar_edicao(self, sim):
        estado = "normal" if sim else "disabled"
        self.ent_titulo.config(state=estado)
        self.ent_autor.config(state=estado)
        if not sim:
            self._carregando = True
            self.var_titulo.set("")
            self.var_autor.set("")
            self._carregando = False

    def _ao_selecionar(self, _=None):
        sel = self.tabela.selection()
        if not sel:
            self._habilitar_edicao(False)
            return
        livro = self.livros[sel[0]]
        self._habilitar_edicao(not self.convertendo)
        self._carregando = True
        self.var_titulo.set(livro.titulo)
        self.var_autor.set(livro.autor)
        self._carregando = False

    def _editar(self, campo):
        if getattr(self, "_carregando", False):
            return
        sel = self.tabela.selection()
        if not sel:
            return
        livro = self.livros[sel[0]]
        setattr(livro, campo, (self.var_titulo if campo == "titulo" else self.var_autor).get())
        self.tabela.item(sel[0], values=(livro.titulo, livro.autor, livro.status))

    # ---- conversão ------------------------------------------------------
    def converter(self):
        pendentes = [(k, l) for k, l in self.livros.items() if l.status != "Pronto ✓"]
        if not pendentes:
            if not self.livros:
                self.escolher()
            else:
                messagebox.showinfo("Tudo pronto", "Todos os livros da lista já foram convertidos.")
            return
        self.convertendo = True
        self.btn_converter.config(state="disabled")
        self._habilitar_edicao(False)
        capa = self.var_capa.get()
        threading.Thread(target=self._trabalhar, args=(pendentes, capa), daemon=True).start()

    def _trabalhar(self, pendentes, capa):
        total = len(pendentes)
        for i, (chave, livro) in enumerate(pendentes):
            self.fila.put(("status", chave, "Convertendo…"))
            pasta = self.pasta_saida or livro.caminho.parent
            destino = pasta / (livro.caminho.stem + ".epub")

            def prog(f, msg, i=i):
                self.fila.put(("progresso", (i + f) / total, f"[{i + 1}/{total}] {livro.titulo}: {msg}"))

            try:
                r = converter(livro.caminho, destino, livro.titulo, livro.autor, capa, prog)
                livro.saida = r.destino
                texto = "Pronto ✓" if not r.avisos else "Pronto (ver aviso)"
                self.fila.put(("status", chave, texto))
                if r.avisos:
                    self.fila.put(("aviso", livro.titulo, "\n".join(r.avisos)))
            except ErroConversao as e:
                self.fila.put(("status", chave, "Erro"))
                self.fila.put(("erro", livro.titulo, str(e)))
            except Exception as e:  # erro inesperado: mostra sem derrubar o app
                self.fila.put(("status", chave, "Erro"))
                self.fila.put(("erro", livro.titulo, f"Erro inesperado: {type(e).__name__}: {e}"))
        self.fila.put(("fim",))

    def _ler_fila(self):
        try:
            while True:
                msg = self.fila.get_nowait()
                tipo = msg[0]
                if tipo == "progresso":
                    self.progresso["value"] = msg[1]
                    self.lbl_status.config(text=msg[2])
                elif tipo == "status":
                    livro = self.livros.get(msg[1])
                    if livro:
                        livro.status = msg[2]
                        self.tabela.item(msg[1], values=(livro.titulo, livro.autor, livro.status))
                elif tipo == "erro":
                    messagebox.showerror("Falha na conversão", f"{msg[1]}\n\n{msg[2]}")
                elif tipo == "aviso":
                    messagebox.showwarning("Convertido com aviso", f"{msg[1]}\n\n{msg[2]}")
                elif tipo == "fim":
                    self.convertendo = False
                    self.btn_converter.config(state="normal")
                    feitos = [l for l in self.livros.values() if l.saida]
                    if feitos:
                        self.btn_abrir.config(state="normal")
                        self.lbl_status.config(text=f"Pronto! {len(feitos)} EPUB(s) criado(s).")
                    self._ao_selecionar()
        except queue.Empty:
            pass
        self.root.after(100, self._ler_fila)

    def abrir_pasta(self):
        feitos = [l for l in self.livros.values() if l.saida]
        if feitos:
            os.startfile(feitos[-1].saida.parent)

    def sobre(self):
        codigo = URL_CODIGO or "o código-fonte acompanha este programa"
        messagebox.showinfo(
            "Sobre",
            f"PDF → EPUB para Kindle  •  versão {VERSAO}\n"
            "Copyright © 2026 mikaelvestenasantos-maker\n\n"
            "Software livre e gratuito, licenciado sob a GNU Affero General "
            "Public License v3 (AGPL-3.0). Você pode usar, estudar, modificar e "
            "redistribuir, desde que mantenha a mesma licença. Não há nenhuma garantia.\n\n"
            f"Código-fonte: {codigo}\n\n"
            "Usa PyMuPDF (AGPL-3.0, Artifex) e windnd (MIT).\n"
            "Kindle é marca registrada da Amazon. Este programa não tem "
            "vínculo com a Amazon.\n\n"
            "Converta apenas arquivos que você tem direito de usar.",
        )

    def ajuda_kindle(self):
        messagebox.showinfo(
            "Como mandar pro Kindle",
            "O Kindle aceita EPUB direto pelo Send to Kindle:\n\n"
            "• Site: amazon.com/sendtokindle (arraste o .epub)\n"
            "• E-mail: anexe o .epub e envie para o endereço do seu Kindle "
            "(aparece em Configurações → Sua conta → E-mail do Send-to-Kindle). "
            "O seu e-mail precisa estar na lista de remetentes aprovados na Amazon.\n"
            "• App Send to Kindle para Windows.\n\n"
            "Em poucos minutos o livro aparece na biblioteca do Kindle.",
        )


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
