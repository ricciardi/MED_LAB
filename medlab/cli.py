import json
import re
from pathlib import Path
import click
from sqlalchemy import delete, select
from .extensions import db
from .models import ClinicalCase, RateLimit, User

def register_commands(app):
    @app.cli.command("init-db")
    def init_db():
        """Cria tabelas e importa casos iniciais, sem apagar dados existentes."""
        from .case_schema import validate_case
        db.create_all()
        cases = json.loads((Path(__file__).parent / "data" / "cases_pt.json").read_text(encoding="utf-8"))
        added = 0
        for item in cases:
            data = validate_case(item)
            if db.session.execute(select(ClinicalCase.id).where(ClinicalCase.slug == data["caseId"])).scalar_one_or_none():
                continue
            db.session.add(ClinicalCase(slug=data["caseId"], title=data["caseTitle"], specialty=data["specialty"], difficulty=data["difficulty"], summary=data["summary"], data=data, is_published=True, is_generated=False))
            added += 1
        db.session.commit()
        click.echo(f"Banco pronto. {added} novos casos incluídos. Dados existentes preservados.")

    @app.cli.command("create-admin")
    @click.option("--email", prompt="E-mail do administrador")
    @click.option("--name", prompt="Nome do administrador")
    @click.password_option(prompt="Senha (mínimo 10 caracteres)", confirmation_prompt="Confirme a senha")
    def create_admin(email, name, password):
        """Cria administrador com senha em prompt oculto."""
        email = email.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(email) > 254:
            raise click.ClickException("E-mail inválido.")
        if not 10 <= len(password) <= 128 or not 2 <= len(name.strip()) <= 100:
            raise click.ClickException("Informe nome válido e senha de 10 a 128 caracteres.")
        if db.session.execute(select(User).where(User.email == email)).scalar_one_or_none():
            raise click.ClickException("Já existe uma conta com este e-mail. Use outro e-mail para administração.")
        user = User(name=name.strip(), email=email, is_admin=True)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Administrador criado. Acesse {app.config['ADMIN_PATH']}/login.")

    @app.cli.command("reset-password")
    @click.argument("email")
    @click.password_option(prompt="Nova senha (mínimo 10 caracteres)", confirmation_prompt="Confirme a senha")
    def reset_password(email, password):
        """Redefine senha pelo terminal do servidor."""
        user = db.session.execute(select(User).where(User.email == email.strip().lower())).scalar_one_or_none()
        if not user:
            raise click.ClickException("Conta não encontrada.")
        if not 10 <= len(password) <= 128:
            raise click.ClickException("A senha deve ter de 10 a 128 caracteres.")
        user.set_password(password)
        db.session.commit()
        click.echo("Senha atualizada.")

    @app.cli.command("clear-rate-limits")
    def clear_rate_limits():
        """Limpa contadores de proteção após manutenção controlada."""
        db.session.execute(delete(RateLimit))
        db.session.commit()
        click.echo("Contadores de tentativas limpos.")
