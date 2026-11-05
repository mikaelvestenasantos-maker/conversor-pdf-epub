"""Envio de EPUB para o Kindle por e-mail (Send to Kindle), usando Gmail.

A senha de app do Gmail fica no Gerenciador de Credenciais do Windows
(via `keyring`); o resto da configuração fica em %APPDATA%/ConversorEPUB.

Copyright (C) 2026 mikaelvestenasantos-maker
Licenciado sob a GNU Affero General Public License v3. Veja o arquivo LICENSE.
"""
from __future__ import annotations

import json
import os
import re
import smtplib
import ssl
import unicodedata
from dataclasses import asdict, dataclass
from email.message import EmailMessage
from pathlib import Path

try:
    import keyring
except ImportError:  # sem keyring, a senha não é guardada
    keyring = None

SERVICO = "ConversorEPUB-gmail"
SMTP_HOST, SMTP_PORTA = "smtp.gmail.com", 465
LIMITE_MB = 25  # limite de anexo do Gmail

URL_SENHA_APP = "https://myaccount.google.com/apppasswords"
URL_REMETENTES = "https://www.amazon.com.br/hz/mycd/myx#/home/settings/pdoc"
URL_SEND_TO_KINDLE = "https://www.amazon.com/sendtokindle"


class ErroEnvio(Exception):
    pass


@dataclass
class ConfigKindle:
    email_kindle: str = ""
    email_gmail: str = ""
    enviar_ao_terminar: bool = False

    @property
    def completa(self) -> bool:
        return bool(self.email_kindle and self.email_gmail and ler_senha(self.email_gmail))


def _arquivo_config() -> Path:
    base = Path(os.environ.get("APPDATA") or Path.home())
    return base / "ConversorEPUB" / "config.json"


def carregar_config() -> ConfigKindle:
    try:
        dados = json.loads(_arquivo_config().read_text(encoding="utf-8"))
        return ConfigKindle(**{k: v for k, v in dados.items() if k in ConfigKindle.__dataclass_fields__})
    except (OSError, ValueError, TypeError):
        return ConfigKindle()


def salvar_config(cfg: ConfigKindle) -> None:
    arq = _arquivo_config()
    arq.parent.mkdir(parents=True, exist_ok=True)
    arq.write_text(json.dumps(asdict(cfg), ensure_ascii=False, indent=2), encoding="utf-8")


def ler_senha(email_gmail: str) -> str:
    if not keyring or not email_gmail:
        return ""
    try:
        return keyring.get_password(SERVICO, email_gmail) or ""
    except Exception:
        return ""


def salvar_senha(email_gmail: str, senha: str) -> None:
    if not keyring:
        raise ErroEnvio("Não foi possível guardar a senha com segurança neste computador.")
    keyring.set_password(SERVICO, email_gmail, senha)


def apagar_senha(email_gmail: str) -> None:
    if keyring and email_gmail:
        try:
            keyring.delete_password(SERVICO, email_gmail)
        except Exception:
            pass


def normalizar_senha(senha: str) -> str:
    # o Google mostra a senha de app em 4 blocos com espaços
    return re.sub(r"\s+", "", senha)


def email_valido(e: str) -> bool:
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e.strip()))


def _conectar(email_gmail: str, senha: str) -> smtplib.SMTP_SSL:
    try:
        smtp = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORTA, context=ssl.create_default_context(), timeout=60)
    except OSError as e:
        raise ErroEnvio(f"Não consegui conectar ao Gmail. Verifique a internet.\n\n({e})")
    try:
        smtp.login(email_gmail, senha)
    except smtplib.SMTPAuthenticationError:
        smtp.close()
        raise ErroEnvio(
            "O Gmail recusou o login.\n\n"
            "Use uma SENHA DE APP (16 letras), não a senha normal da sua conta. "
            "Ela é criada em Conta Google → Segurança → Senhas de app, e exige a "
            "verificação em duas etapas ativada."
        )
    return smtp


def testar(email_gmail: str, senha: str) -> None:
    smtp = _conectar(email_gmail, senha)
    smtp.quit()


def _nome_seguro(nome: str) -> str:
    # a Amazon lida melhor com nomes de arquivo sem acentos
    s = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    s = re.sub(r"[^\w\-. ]+", "", s).strip() or "livro"
    return s[:80]


def enviar(epub: Path, titulo: str, cfg: ConfigKindle) -> None:
    epub = Path(epub)
    senha = ler_senha(cfg.email_gmail)
    if not (cfg.email_kindle and cfg.email_gmail and senha):
        raise ErroEnvio("Configure o envio pro Kindle primeiro (botão \"Configurar Kindle\").")
    tamanho_mb = epub.stat().st_size / 1_048_576
    if tamanho_mb > LIMITE_MB:
        raise ErroEnvio(
            f"O EPUB tem {tamanho_mb:.0f} MB, acima do limite de {LIMITE_MB} MB do Gmail.\n\n"
            f"Envie pelo site {URL_SEND_TO_KINDLE}, que aceita arquivos maiores."
        )
    msg = EmailMessage()
    msg["From"] = cfg.email_gmail
    msg["To"] = cfg.email_kindle
    msg["Subject"] = titulo or epub.stem
    msg.set_content("Enviado pelo Conversor PDF → EPUB.")
    msg.add_attachment(epub.read_bytes(), maintype="application", subtype="epub+zip",
                       filename=_nome_seguro(titulo or epub.stem) + ".epub")
    smtp = _conectar(cfg.email_gmail, senha)
    try:
        smtp.send_message(msg)
    except smtplib.SMTPException as e:
        raise ErroEnvio(f"O Gmail não aceitou o envio: {e}")
    finally:
        try:
            smtp.quit()
        except Exception:
            pass
