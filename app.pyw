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
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kindle  # noqa: E402
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

VERSAO = "1.1.0"
URL_CODIGO = "https://github.com/mikaelvestenasantos-maker/conversor-pdf-epub"

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
        self.enviado = False


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.livros: dict[str, Livro] = {}
        self.fila: queue.Queue = queue.Queue()
        self.ocupado = False
        self.pasta_saida: Path | None = None
        self.cfg = kindle.carregar_config()

        root.title("PDF → EPUB para Kindle")
        root.configure(bg=COR_FUNDO)

        st = ttk.Style()
        st.theme_use("clam")
        st.configure(".", background=COR_FUNDO, foreground=COR_TEXTO, font=("Segoe UI", 10))
        st.configure("TLabel", background=COR_FUNDO)
        st.configure("Sub.TLabel", foreground=COR_SUAVE)
        st.configure("Titulo.TLabel", font=("Segoe UI Semibold", 16))
        st.configure("Secao.TLabel", font=("Segoe UI Semibold", 10))
        st.configure("Link.TLabel", foreground=COR_DESTAQUE, font=("Segoe UI", 9, "underline"))
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
            highlightthickness=2, highlightbackground=COR_BORDA, height=3,
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
                                   selectmode="browse", height=5)
        for col, nome, larg in (("titulo", "Título", 300), ("autor", "Autor", 180), ("status", "Status", 170)):
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

        # opções do livro
        opcoes = ttk.Frame(principal)
        opcoes.pack(fill="x", pady=(0, 4))
        self.var_capa = tk.BooleanVar(value=True)
        self.var_capitulares = tk.BooleanVar(value=True)
        ttk.Checkbutton(opcoes, text="1ª página como capa", variable=self.var_capa).pack(side="left")
        ttk.Checkbutton(opcoes, text="Letra grande no início dos capítulos",
                        variable=self.var_capitulares).pack(side="left", padx=(16, 0))
        self.lbl_saida = ttk.Label(opcoes, text="Salvar: na pasta de cada PDF", style="Sub.TLabel")
        self.lbl_saida.pack(side="right")
        ttk.Button(opcoes, text="Mudar…", command=self.escolher_saida).pack(side="right", padx=(0, 8))

        # Kindle
        linha_k = ttk.Frame(principal)
        linha_k.pack(fill="x", pady=(4, 8))
        ttk.Label(linha_k, text="Kindle:", style="Secao.TLabel").pack(side="left")
        self.lbl_kindle = ttk.Label(linha_k, text="", style="Sub.TLabel")
        self.lbl_kindle.pack(side="left", padx=(6, 0))
        ttk.Button(linha_k, text="Configurar…", command=self.configurar_kindle).pack(side="right")
        self.var_auto = tk.BooleanVar(value=self.cfg.enviar_ao_terminar)
        self.chk_auto = ttk.Checkbutton(linha_k, text="Enviar ao terminar de converter",
                                        variable=self.var_auto, command=self._mudou_auto)
        self.chk_auto.pack(side="right", padx=(0, 12))
        self._atualizar_kindle()

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
        self.btn_enviar = ttk.Button(botoes, text="Enviar pro Kindle", command=self.enviar_kindle, state="disabled")
        self.btn_enviar.pack(side="right", padx=8)
        self.btn_abrir = ttk.Button(botoes, text="Abrir pasta", command=self.abrir_pasta, state="disabled")
        self.btn_abrir.pack(side="right")
        ttk.Button(botoes, text="Remover", command=self.remover).pack(side="left")
        ttk.Button(botoes, text="Sobre", command=self.sobre).pack(side="left", padx=8)

        for arg in sys.argv[1:]:  # permite "Abrir com…"
            self.adicionar([arg])
        self.root.after(100, self._ler_fila)
        root.update_idletasks()
        root.minsize(root.winfo_reqwidth(), root.winfo_reqheight())

    # ---- arquivos -------------------------------------------------------
    def escolher(self):
        if self.ocupado:
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
        if self.ocupado:
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
        if self.ocupado:
            return
        for iid in self.tabela.selection():
            self.tabela.delete(iid)
            self.livros.pop(iid, None)
        self._habilitar_edicao(False)
        self._atualizar_botoes()

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
        self._atualizar_botoes()
        if not sel:
            self._habilitar_edicao(False)
            return
        livro = self.livros[sel[0]]
        self._habilitar_edicao(not self.ocupado)
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

    def _atualizar_botoes(self):
        convertidos = [l for l in self.livros.values() if l.saida]
        self.btn_abrir.config(state="normal" if convertidos else "disabled")
        self.btn_enviar.config(state="normal" if convertidos and not self.ocupado else "disabled")

    def _set_status(self, chave, texto):
        livro = self.livros.get(chave)
        if livro:
            livro.status = texto
            self.tabela.item(chave, values=(livro.titulo, livro.autor, livro.status))

    def _ocupar(self, sim):
        self.ocupado = sim
        self.btn_converter.config(state="disabled" if sim else "normal")
        self._atualizar_botoes()
        if sim:
            self._habilitar_edicao(False)
        else:
            self._ao_selecionar()

    # ---- conversão ------------------------------------------------------
    def converter(self):
        pendentes = [(k, l) for k, l in self.livros.items() if not l.saida]
        if not pendentes:
            if not self.livros:
                self.escolher()
            else:
                messagebox.showinfo("Tudo pronto", "Todos os livros da lista já foram convertidos.")
            return
        enviar = self.var_auto.get()
        if enviar and not self.cfg.completa:
            if not self.configurar_kindle():
                enviar = False
        self._ocupar(True)
        args = (pendentes, self.var_capa.get(), self.var_capitulares.get(), enviar)
        threading.Thread(target=self._trabalhar, args=args, daemon=True).start()

    def _trabalhar(self, pendentes, capa, capitulares, enviar):
        total = len(pendentes)
        for i, (chave, livro) in enumerate(pendentes):
            self.fila.put(("status", chave, "Convertendo…"))
            pasta = self.pasta_saida or livro.caminho.parent
            destino = pasta / (livro.caminho.stem + ".epub")

            def prog(f, msg, i=i):
                self.fila.put(("progresso", (i + f) / total, f"[{i + 1}/{total}] {livro.titulo}: {msg}"))

            try:
                r = converter(livro.caminho, destino, livro.titulo, livro.autor, capa, prog, capitulares)
                livro.saida = r.destino
                self.fila.put(("status", chave, "Pronto ✓" if not r.avisos else "Pronto (ver aviso)"))
                if r.avisos:
                    self.fila.put(("aviso", livro.titulo, "\n".join(r.avisos)))
            except ErroConversao as e:
                self.fila.put(("status", chave, "Erro"))
                self.fila.put(("erro", "Falha na conversão", livro.titulo, str(e)))
                continue
            except Exception as e:  # erro inesperado: mostra sem derrubar o app
                self.fila.put(("status", chave, "Erro"))
                self.fila.put(("erro", "Falha na conversão", livro.titulo,
                               f"Erro inesperado: {type(e).__name__}: {e}"))
                continue
            if enviar and not self._enviar_um(chave, livro):
                enviar = False  # se falhou (ex.: senha errada), não insiste nos próximos
        self.fila.put(("fim",))

    # ---- envio pro Kindle -----------------------------------------------
    def _enviar_um(self, chave, livro) -> bool:
        self.fila.put(("status", chave, "Enviando pro Kindle…"))
        self.fila.put(("progresso", None, f"Enviando “{livro.titulo}” para {self.cfg.email_kindle}…"))
        try:
            kindle.enviar(livro.saida, livro.titulo, self.cfg)
        except kindle.ErroEnvio as e:
            self.fila.put(("status", chave, "Pronto (não enviado)"))
            self.fila.put(("erro", "Falha no envio pro Kindle", livro.titulo, str(e)))
            return False
        except Exception as e:
            self.fila.put(("status", chave, "Pronto (não enviado)"))
            self.fila.put(("erro", "Falha no envio pro Kindle", livro.titulo,
                           f"Erro inesperado: {type(e).__name__}: {e}"))
            return False
        livro.enviado = True
        self.fila.put(("status", chave, "Enviado pro Kindle ✓"))
        return True

    def enviar_kindle(self):
        if self.ocupado:
            return
        sel = [k for k in self.tabela.selection() if self.livros[k].saida]
        if sel:
            alvos = sel
        else:
            alvos = [k for k, l in self.livros.items() if l.saida and not l.enviado]
        if not alvos:
            messagebox.showinfo("Nada para enviar", "Converta um livro primeiro (ou selecione um já convertido).")
            return
        livro = self.livros[alvos[0]]
        if livro.enviado and not messagebox.askyesno(
                "Enviar de novo?", f"“{livro.titulo}” já foi enviado. Enviar de novo?"):
            return
        if not self.cfg.completa and not self.configurar_kindle():
            return
        self._ocupar(True)

        def trabalho():
            for k in alvos:
                if not self._enviar_um(k, self.livros[k]):
                    break
            self.fila.put(("fim",))

        threading.Thread(target=trabalho, daemon=True).start()

    def _atualizar_kindle(self):
        if self.cfg.completa:
            self.lbl_kindle.config(text=f"envia para {self.cfg.email_kindle}")
        else:
            self.lbl_kindle.config(text="não configurado")

    def _mudou_auto(self):
        self.cfg.enviar_ao_terminar = self.var_auto.get()
        kindle.salvar_config(self.cfg)
        if self.var_auto.get() and not self.cfg.completa:
            if not self.configurar_kindle():
                self.var_auto.set(False)
                self.cfg.enviar_ao_terminar = False
                kindle.salvar_config(self.cfg)

    def configurar_kindle(self) -> bool:
        dlg = DialogoKindle(self.root, self.cfg)
        self.root.wait_window(dlg.janela)
        if dlg.salvo:
            self.cfg = dlg.cfg
            self.cfg.enviar_ao_terminar = self.var_auto.get()
            kindle.salvar_config(self.cfg)
        self._atualizar_kindle()
        return self.cfg.completa

    # ---- fila de eventos da thread --------------------------------------
    def _ler_fila(self):
        try:
            while True:
                msg = self.fila.get_nowait()
                tipo = msg[0]
                if tipo == "progresso":
                    if msg[1] is not None:
                        self.progresso["value"] = msg[1]
                    self.lbl_status.config(text=msg[2])
                elif tipo == "status":
                    self._set_status(msg[1], msg[2])
                elif tipo == "erro":
                    messagebox.showerror(msg[1], f"{msg[2]}\n\n{msg[3]}")
                elif tipo == "aviso":
                    messagebox.showwarning("Convertido com aviso", f"{msg[1]}\n\n{msg[2]}")
                elif tipo == "fim":
                    self._ocupar(False)
                    feitos = [l for l in self.livros.values() if l.saida]
                    enviados = [l for l in feitos if l.enviado]
                    if feitos:
                        texto = f"Pronto! {len(feitos)} EPUB(s) criado(s)"
                        if enviados:
                            texto += f", {len(enviados)} enviado(s) pro Kindle. Eles aparecem na biblioteca em alguns minutos"
                        self.lbl_status.config(text=texto + ".")
        except queue.Empty:
            pass
        self.root.after(100, self._ler_fila)

    def abrir_pasta(self):
        feitos = [l for l in self.livros.values() if l.saida]
        if feitos:
            os.startfile(feitos[-1].saida.parent)

    def sobre(self):
        messagebox.showinfo(
            "Sobre",
            f"PDF → EPUB para Kindle  •  versão {VERSAO}\n"
            "Copyright © 2026 mikaelvestenasantos-maker\n\n"
            "Software livre e gratuito, licenciado sob a GNU Affero General "
            "Public License v3 (AGPL-3.0). Você pode usar, estudar, modificar e "
            "redistribuir, desde que mantenha a mesma licença. Não há nenhuma garantia.\n\n"
            f"Código-fonte: {URL_CODIGO}\n\n"
            "Usa PyMuPDF (AGPL-3.0, Artifex), windnd (MIT) e keyring (MIT).\n"
            "Kindle é marca registrada da Amazon. Este programa não tem "
            "vínculo com a Amazon.\n\n"
            "Converta apenas arquivos que você tem direito de usar.",
        )


class DialogoKindle:
    """Janela para configurar o envio por e-mail (Gmail → Kindle)."""

    def __init__(self, pai: tk.Tk, cfg: kindle.ConfigKindle):
        self.salvo = False
        self.cfg = kindle.ConfigKindle(cfg.email_kindle, cfg.email_gmail, cfg.enviar_ao_terminar)
        j = self.janela = tk.Toplevel(pai)
        j.title("Enviar pro Kindle")
        j.configure(bg=COR_FUNDO)
        j.transient(pai)
        j.resizable(False, False)

        f = ttk.Frame(j, padding=20)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Enviar pro Kindle pelo Gmail", style="Titulo.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(f, text="Configure uma vez. Depois é só clicar em “Enviar pro Kindle”.",
                  style="Sub.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 14))

        self.var_kindle = tk.StringVar(value=self.cfg.email_kindle)
        self.var_gmail = tk.StringVar(value=self.cfg.email_gmail)
        self.var_senha = tk.StringVar(value=kindle.ler_senha(self.cfg.email_gmail))

        def campo(linha, rotulo, var, ajuda, link_txt, link_url, oculto=False):
            ttk.Label(f, text=rotulo, style="Secao.TLabel").grid(row=linha, column=0, sticky="w", pady=(6, 0))
            e = ttk.Entry(f, textvariable=var, width=42, show="•" if oculto else "")
            e.grid(row=linha, column=1, sticky="ew", pady=(6, 0), padx=(12, 0))
            ttk.Label(f, text=ajuda, style="Sub.TLabel", wraplength=430, justify="left").grid(
                row=linha + 1, column=1, sticky="w", padx=(12, 0))
            if link_txt:
                l = ttk.Label(f, text=link_txt, style="Link.TLabel", cursor="hand2")
                l.grid(row=linha + 2, column=1, sticky="w", padx=(12, 0), pady=(0, 4))
                l.bind("<Button-1>", lambda ev: webbrowser.open(link_url))
            return e

        primeiro = campo(2, "E-mail do Kindle", self.var_kindle,
                         "Termina em @kindle.com. Fica na Amazon em Conteúdo e dispositivos → "
                         "Preferências → Configurações de documentos pessoais.",
                         "Abrir essa página na Amazon", kindle.URL_REMETENTES)
        campo(5, "Seu Gmail", self.var_gmail,
              "Na mesma página da Amazon, adicione este Gmail em “Lista de e-mails aprovados”, "
              "senão a Amazon descarta o livro.", "", "")
        campo(8, "Senha de app", self.var_senha,
              "Não é a senha normal do Gmail: é uma senha de 16 letras criada só para este app. "
              "Exige a verificação em duas etapas ativada. Fica guardada no Gerenciador de "
              "Credenciais do Windows.",
              "Criar senha de app no Google", kindle.URL_SENHA_APP, oculto=True)
        f.columnconfigure(1, weight=1)

        self.lbl_teste = ttk.Label(f, text="", style="Sub.TLabel", wraplength=520, justify="left")
        self.lbl_teste.grid(row=11, column=0, columnspan=2, sticky="w", pady=(12, 0))

        b = ttk.Frame(f)
        b.grid(row=12, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        ttk.Button(b, text="Salvar", style="Primario.TButton", command=self.salvar).pack(side="right")
        ttk.Button(b, text="Cancelar", command=j.destroy).pack(side="right", padx=8)
        ttk.Button(b, text="Testar conexão", command=self.testar).pack(side="left")

        j.bind("<Escape>", lambda e: j.destroy())
        j.update_idletasks()
        x = pai.winfo_rootx() + (pai.winfo_width() - j.winfo_width()) // 2
        y = pai.winfo_rooty() + (pai.winfo_height() - j.winfo_height()) // 3
        j.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        j.grab_set()
        primeiro.focus_set()

    def _validar(self) -> tuple[str, str, str] | None:
        k = self.var_kindle.get().strip()
        g = self.var_gmail.get().strip()
        s = kindle.normalizar_senha(self.var_senha.get())
        if not kindle.email_valido(k):
            messagebox.showwarning("E-mail do Kindle", "Confira o e-mail do Kindle (algo como nome_123@kindle.com).", parent=self.janela)
            return None
        if not k.lower().endswith(("@kindle.com", "@free.kindle.com", "@kindle.cn")):
            if not messagebox.askyesno("E-mail do Kindle", f"“{k}” não termina em @kindle.com. Usar mesmo assim?", parent=self.janela):
                return None
        if not kindle.email_valido(g):
            messagebox.showwarning("Gmail", "Confira o seu endereço do Gmail.", parent=self.janela)
            return None
        if len(s) < 8:
            messagebox.showwarning("Senha de app", "Cole a senha de app de 16 letras que o Google gerou.", parent=self.janela)
            return None
        return k, g, s

    def testar(self):
        dados = self._validar()
        if not dados:
            return
        _, g, s = dados
        self.lbl_teste.config(text="Testando o login no Gmail…")
        self.janela.config(cursor="watch")
        self.janela.update()
        try:
            kindle.testar(g, s)
            self.lbl_teste.config(text="✓ Login no Gmail funcionou. Lembre de aprovar este Gmail na Amazon.")
        except kindle.ErroEnvio as e:
            self.lbl_teste.config(text="")
            messagebox.showerror("Teste falhou", str(e), parent=self.janela)
        finally:
            self.janela.config(cursor="")

    def salvar(self):
        dados = self._validar()
        if not dados:
            return
        k, g, s = dados
        try:
            if self.cfg.email_gmail and self.cfg.email_gmail != g:
                kindle.apagar_senha(self.cfg.email_gmail)
            kindle.salvar_senha(g, s)
        except Exception as e:
            messagebox.showerror("Não consegui salvar a senha", str(e), parent=self.janela)
            return
        self.cfg.email_kindle, self.cfg.email_gmail = k, g
        self.salvo = True
        self.janela.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
