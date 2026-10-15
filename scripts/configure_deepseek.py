#!/usr/bin/env python3
"""Configure DeepSeek privately in this installation's .env, without API calls."""
from __future__ import annotations

import getpass
import io
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import warnings

from dotenv import dotenv_values
from dotenv.parser import parse_stream


ROOT = Path(__file__).resolve().parents[1]


class ConfigurationError(ValueError):
    """Messages are fixed text; never include a credential or file contents."""


def read_private_env(path: Path) -> tuple[str, tuple | None]:
    """Read only the selected .env, refusing symbolic links and special files."""
    try:
        before = path.lstat()
    except FileNotFoundError:
        return "", None
    if not stat.S_ISREG(before.st_mode):
        raise ConfigurationError("O .env precisa ser um arquivo regular, sem link simbólico.")
    with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "r", encoding="utf-8", newline="") as source:
        opened = os.fstat(source.fileno())
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ConfigurationError("O .env mudou durante a leitura. Execute o comando novamente.")
        if opened.st_size > 1024 * 1024:
            raise ConfigurationError("O .env excede o tamanho esperado. Confira a instalação.")
        text = source.read()
    return text, (opened.st_dev, opened.st_ino, opened.st_mtime_ns, opened.st_size)


def configured(path: Path) -> bool:
    text, _ = read_private_env(path)
    return bool(dotenv_values(stream=io.StringIO(text), interpolate=False).get("DEEPSEEK_API_KEY", ""))


def configure(path: Path, key: str) -> None:
    # Accept token characters, not dotenv interpolation, quotes or new lines.
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_.~+/=-]{16,512}", key):
        raise ConfigurationError("A chave deve conter de 16 a 512 caracteres, sem espaços ou quebras de linha.")
    original, fingerprint = read_private_env(path)
    replacements = {"DEEPSEEK_API_KEY": "DEEPSEEK_API_KEY='" + key + "'\n", "DEMO_MODE": "DEMO_MODE=false\n"}
    seen = set()
    parts = []
    for binding in parse_stream(io.StringIO(original)):
        if binding.error:
            raise ConfigurationError("O .env contém uma linha inválida. Corrija o formato antes de configurar a chave.")
        if binding.key in replacements:
            if binding.key not in seen:
                parts.append(replacements[binding.key])
                seen.add(binding.key)
        else:
            parts.append(binding.original.string)
    for name, value in replacements.items():
        if name not in seen:
            if parts and not parts[-1].endswith(("\n", "\r")):
                parts.append("\n")
            parts.append(value)
    temporary = None
    try:
        # The .env.* name is excluded by this repository and release packager.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", prefix=".env.", dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            os.fchmod(output.fileno(), 0o600)
            output.write("".join(parts))
            output.flush()
            os.fsync(output.fileno())
        try:
            current = path.lstat()
            current_fingerprint = (current.st_dev, current.st_ino, current.st_mtime_ns, current.st_size)
        except FileNotFoundError:
            current_fingerprint = None
        if current_fingerprint != fingerprint:
            raise ConfigurationError("O .env mudou durante a configuração. Execute o comando novamente.")
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    # Do not echo unknown arguments: a mistakenly pasted token must stay private.
    if arguments in (["--help"], ["-h"]):
        print("Uso: python scripts/configure_deepseek.py [--check]\nSem opção: entrada oculta da chave. --check: verifica somente o .env, sem chamar a API.")
        return 0
    if arguments not in ([], ["--check"]):
        print("Opção inválida. Use o comando sem argumentos ou com --check. Não passe a chave na linha de comando.", file=sys.stderr)
        return 2
    try:
        path = ROOT / ".env"
        if arguments == ["--check"]:
            present = configured(path)
            print("DeepSeek: chave configurada no .env (não validada na API)." if present else "DeepSeek: chave não configurada no .env.")
            return 0 if present else 1
        # getpass normally falls back to visible input without a real terminal.
        # Turning its warning into an error prevents that unsafe fallback.
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            key = getpass.getpass("Chave DeepSeek (entrada oculta): ").strip()
        configure(path, key)
        print("Chave salva no .env privado (0600); DEMO_MODE=false. Reinicie a aplicação para aplicar.")
        return 0
    except (KeyboardInterrupt, EOFError):
        print("Configuração cancelada; nenhuma chave foi exibida.", file=sys.stderr)
        return 130
    except getpass.GetPassWarning:
        print("Use um Terminal interativo com entrada oculta. Não envie a chave por pipe ou argumento.", file=sys.stderr)
        return 1
    except ConfigurationError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception:
        # No traceback, provider call, credential text or environment dump.
        print("Não foi possível configurar/verificar o .env. Confira as permissões e o formato do arquivo.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
