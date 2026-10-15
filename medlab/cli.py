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
        from .migrations import upgrade_database
        from .catalog import SEED_CASE_TYPES, SPECIALTY_ALIASES
        from .catalogue import case_type_for
        upgrade_database()
        cases = json.loads((Path(__file__).parent / "data" / "cases_pt.json").read_text(encoding="utf-8"))
        added = 0
        for item in cases:
            item["caseType"] = SEED_CASE_TYPES.get(item["caseId"], "Primeira consulta")
            item["specialty"] = SPECIALTY_ALIASES.get(item["specialty"], item["specialty"])
            data = validate_case(item)
            if db.session.execute(select(ClinicalCase.id).where(ClinicalCase.slug == data["caseId"])).scalar_one_or_none():
                continue
            db.session.add(ClinicalCase(slug=data["caseId"], title=data["caseTitle"], specialty=data["specialty"], case_type=case_type_for(item), difficulty=data["difficulty"], summary=data["summary"], data=data, is_published=True, is_generated=False))
            added += 1
        db.session.commit()
        click.echo(f"Banco pronto. {added} novos casos incluídos. Dados existentes preservados.")

    @app.cli.command("upgrade-db")
    def upgrade_db():
        """Atualiza tabelas e metadados de catálogo sem alterar históricos."""
        from .migrations import upgrade_database
        from .catalog import SEED_CASE_TYPES, SPECIALTY_ALIASES
        from .catalogue import case_type_for
        upgrade_database()
        for case in db.session.execute(select(ClinicalCase)).scalars().yield_per(100):
            data = dict(case.data)
            data.setdefault("caseType", SEED_CASE_TYPES.get(case.slug, "Primeira consulta"))
            data["specialty"] = SPECIALTY_ALIASES.get(case.specialty, case.specialty)
            case.data, case.specialty, case.case_type = data, data["specialty"], case_type_for(data)
        db.session.commit()
        click.echo("Banco atualizado. Consultas e notas anteriores preservadas.")

    @app.cli.command("catalog-report")
    def catalog_report():
        """Informa metas e casos reais publicados, em revisão e fictícios."""
        from .catalogue import catalogue_counts
        report = catalogue_counts()
        click.echo(json.dumps({"target_per_specialty": 300, "specialties": report,
            "published_real": sum(r["published_real"] for r in report),
            "draft_real": sum(r["draft_real"] for r in report),
            "published_fictional": sum(r["published_fictional"] for r in report)}, ensure_ascii=False, indent=2))

    @app.cli.command("import-real-cases")
    @click.option("--source", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
    @click.option("--work-dir", type=click.Path(file_okay=False, path_type=Path), default=Path("instance/imports/MedCaseReasoning/processed"), show_default=True)
    @click.option("--specialty", "specialties", multiple=True, help="Especialidade oficial; repita para restringir. Padrão: todas as 55.")
    @click.option("--target-per-specialty", type=click.IntRange(1, 10000), default=300, show_default=True)
    @click.option("--limit", type=click.IntRange(1, 100000), default=50, show_default=True, help="Máximo de registros que exigem trabalho neste lote.")
    @click.option("--adapt", is_flag=True, help="Classifica, traduz e audita com DeepSeek. Consome créditos do provedor.")
    @click.option("--retry-errors", is_flag=True, help="Tenta novamente registros que falharam anteriormente.")
    @click.option("--refresh-sources", is_flag=True, help="Reconsulta fontes ainda não adaptadas, incluindo rejeitadas.")
    def import_real_cases(source, work_dir, specialties, target_per_specialty, limit, adapt, retry_errors, refresh_sources):
        """Importa relatos reais em lotes retomáveis; todos entram como rascunhos privados."""
        from .importer import CaseImporter, ImportError as CaseImportError
        from .catalogue import case_type_for, catalogue_counts
        from .migrations import upgrade_database
        upgrade_database()
        try:
            importer = CaseImporter(app.config, work_dir)
            def progress(item):
                click.echo(f"{item['pmcid']}: {item['status']}" + (f" · {item['specialty']}" if item.get("specialty") else ""))
            report = importer.run(source, specialties=specialties or None, target_per_specialty=target_per_specialty,
                limit=limit, adapt=adapt, retry_errors=retry_errors, refresh_sources=refresh_sources, on_progress=progress)
            added = 0
            for data in importer.iter_drafts():
                # A completed/reviewed case must never be overwritten by a resumed import.
                if db.session.scalar(select(ClinicalCase.id).where(ClinicalCase.slug == data["caseId"])):
                    continue
                db.session.add(ClinicalCase(slug=data["caseId"], title=data["caseTitle"], specialty=data["specialty"],
                    case_type=case_type_for(data), difficulty=data["difficulty"], summary=data["summary"],
                    data=data, is_generated=False, is_published=False))
                db.session.commit()
                added += 1
            report["newDatabaseDrafts"] = added
            report["database"] = catalogue_counts(target_per_specialty)
            click.echo(json.dumps(report, ensure_ascii=False, indent=2))
            if report.get("adaptationBlocked"):
                click.echo("Adaptação pendente: configure DEEPSEEK_API_KEY e DEMO_MODE=false; a verificação de fontes foi preservada.", err=True)
        except CaseImportError as exc:
            db.session.rollback()
            raise click.ClickException(str(exc)) from exc

    @app.cli.command("import-report")
    @click.option("--work-dir", type=click.Path(file_okay=False, path_type=Path), default=Path("instance/imports/MedCaseReasoning/processed"), show_default=True)
    def import_report(work_dir):
        """Exibe progresso de fontes/tradução e contagem publicada separadamente."""
        from .importer import CaseImporter, ImportError as CaseImportError
        from .catalogue import catalogue_counts
        try:
            report = CaseImporter(app.config, work_dir).report()
            report["database"] = catalogue_counts()
            click.echo(json.dumps(report, ensure_ascii=False, indent=2))
        except CaseImportError as exc:
            raise click.ClickException(str(exc)) from exc

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
