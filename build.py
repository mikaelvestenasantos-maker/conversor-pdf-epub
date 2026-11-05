"""Gera o executável do Windows em dist/.

    pip install -r requirements.txt pyinstaller
    python build.py

O nome do arquivo inclui a arquitetura (x64 ou arm64) da instalação do Python usada.
"""
import platform
import shutil
from pathlib import Path

import PyInstaller.__main__

RAIZ = Path(__file__).resolve().parent
arq = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(platform.machine().lower(), platform.machine().lower())
nome = f"ConversorEPUB-{arq}"

PyInstaller.__main__.run([
    str(RAIZ / "app.pyw"),
    "--name", nome,
    "--onefile",
    "--windowed",
    "--noconfirm",
    "--clean",
    "--add-data", f"{RAIZ / 'LICENSE'};.",
    "--hidden-import", "keyring.backends.Windows",
    "--distpath", str(RAIZ / "dist"),
    "--workpath", str(RAIZ / "build"),
    "--specpath", str(RAIZ / "build"),
])
shutil.copy(RAIZ / "LICENSE", RAIZ / "dist" / "LICENSE.txt")
print(f"\nPronto: {RAIZ / 'dist' / (nome + '.exe')}")
