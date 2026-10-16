import json
import re
from pathlib import Path
import click
from flask import current_app
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from .extensions import db
from .models import ClinicalCase, RateLimit, User


def basic_case_bundles():
    """Only authored basic-case bundles, not source exports or coverage reports."""
    from .catalog import BODY_SYSTEMS
    folder = Path(__file__).parent / "data" / "basic_cases"
    return [folder / f"{system}.json" for system in BODY_SYSTEMS if (folder / f"{system}.json").is_file()]


def import_basic_cases(paths=None):
    """Validate the whole authored batch, then add missing identities atomically.

    Existing content, review state and deliberate suspension are preserved.
    Publishing an explicitly imported fictional exercise does not mark it as
    clinically reviewed: its original reviewStatus stays unchanged.
    """
    from .case_schema import validate_case
    from .catalogue import case_type_for
    report = {"new": 0, "unchanged": 0}
    payloads, identities = [], set()
    try:
        for path in basic_case_bundles() if paths is None else paths:
            path = Path(path)
            if path.stat().st_size > 20 * 1024 * 1024:
                raise ValueError("oversized basic bundle")
            rows = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(rows, list):
                raise ValueError("basic bundle must be a list")
            for row in rows:
                data = validate_case(row)
                if data.get("learningTrack") != "basic" or data["catalogType"] != "fictional":
                    raise ValueError("basic bundle requires fictional basic cases")
                if data["caseId"] in identities:
                    raise ValueError("duplicate basic identity")
                identities.add(data["caseId"])
                payloads.append(data)
        for data in payloads:
            if db.session.scalar(select(ClinicalCase.id).where(ClinicalCase.slug == data["caseId"])) is not None:
                report["unchanged"] += 1
                continue
            db.session.add(ClinicalCase(slug=data["caseId"], title=data["caseTitle"],
                specialty=data["specialty"], case_type=case_type_for(data), difficulty=data["difficulty"],
                summary=data["summary"], data=data, is_published=True, is_generated=False))
            report["new"] += 1
        db.session.commit()
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, SQLAlchemyError):
        db.session.rollback()
        raise click.ClickException("O pacote de Casos Básicos é inválido. Nenhum caso deste lote foi incluído ou alterado.") from None
    return report


def store_import_drafts(importer, pmcids=None):
    """Persist each completed Portuguese draft; never replace a reviewed case."""
    from .catalogue import case_type_for
    from .source_registry import excluded_pmcids
    excluded = excluded_pmcids()
    added = 0
    for data in importer.iter_drafts(pmcids=pmcids, excluded_pmcids=excluded):
        if data.get("provenance", {}).get("pmcid") in excluded:
            continue
        if db.session.scalar(select(ClinicalCase.id).where(ClinicalCase.slug == data["caseId"])):
            continue
        db.session.add(ClinicalCase(slug=data["caseId"], title=data["caseTitle"], specialty=data["specialty"],
            case_type=case_type_for(data), difficulty=data["difficulty"], summary=data["summary"],
            data=data, is_generated=False, is_published=False))
        db.session.commit()
        added += 1
    return added


def restore_bundled_inventory():
    """The release may include private source metadata, never a user database."""
    from .source_registry import import_source_registry, SourceRegistryError
    bundle = Path(__file__).parent / "data" / "source_registry.jsonl"
    if not bundle.is_file():
        return
    try:
        report = import_source_registry(bundle)
        if report["invalid"] or report["conflicts"]:
            raise click.ClickException("O inventário incluído contém registros inválidos ou conflitantes. Confira o acervo antes de continuar.")
        click.echo(f"Acervo: {report['database']['registered']} fontes registradas; {report['new']} novas. Traduções são contadas separadamente.")
    except SourceRegistryError as exc:
        raise click.ClickException(str(exc)) from exc


def restore_bundled_drafts(bundle_path=None):
    """Restore drafts and explicitly authorized provisional cases, without XML claims.

    The complete batch is validated against the local inventory before any
    draft or source is changed. Existing cases retain their data and review;
    exact untouched private drafts may receive a new provisional authorization.
    """
    from .case_schema import CaseValidationError, is_provisionally_accepted, source_license_allowed, validate_case
    from .catalogue import case_type_for
    from .models import SourceExclusion, SourceRecord
    from .source_registry import _BASE_PROVENANCE

    bundle = Path(bundle_path) if bundle_path is not None else Path(__file__).parent / "data" / "real_case_drafts.json"
    sharded = Path(__file__).parent / "data" / "real_case_drafts"
    if bundle_path is None and sharded.is_dir():
        bundle = sharded
    report = {"new": 0, "unchanged": 0, "skipped_policy": 0, "skipped_excluded": 0, "provisional": 0, "promoted": 0}
    if bundle.is_dir():
        from .draft_bundle import bundle_shards
        try:
            shards = bundle_shards(bundle)
        except (OSError, ValueError, TypeError, KeyError):
            raise click.ClickException("O manifesto dos rascunhos é inválido. Nenhum lote foi iniciado.") from None
        for shard in shards:
            current = restore_bundled_drafts(shard)
            for key in report:
                report[key] += current[key]
            # Each bounded shard is atomic; interruption resumes idempotently.
            db.session.remove()
        return report
    if not bundle.is_file():
        return report
    try:
        if bundle.stat().st_size > 20 * 1024 * 1024:
            raise ValueError("oversized draft bundle")
        payloads = json.loads(bundle.read_text(encoding="utf-8"))
        if not isinstance(payloads, list):
            raise ValueError("draft bundle must be a list")
        planned, promotions, identities = [], [], set()
        with db.session.no_autoflush:
            for payload in payloads:
                data = validate_case(payload)
                if data["catalogType"] != "published_case":
                    raise ValueError("bundle requires sourced cases")
                origin = data["provenance"]
                identity = (origin["dataset"], origin["pmcid"])
                if identity in identities or origin["sourceRecordId"] != origin["pmcid"]:
                    raise ValueError("duplicate or inconsistent source identity")
                identities.add(identity)
                if db.session.get(SourceExclusion, identity) is not None:
                    report["skipped_excluded"] += 1
                    continue
                source = db.session.scalar(select(SourceRecord).where(
                    SourceRecord.dataset == identity[0], SourceRecord.pmcid == identity[1]))
                if source is None or source.dataset_revision != origin["datasetRevision"]:
                    raise ValueError("draft source is absent or mismatched")
                stored_origin = source.provenance
                if stored_origin is not None and not isinstance(stored_origin, dict):
                    raise ValueError("invalid registered provenance")
                if isinstance(stored_origin, dict):
                    for key in ("dataset", "datasetRevision", "sourceRecordId", "pmcid", "pmcVersion", "xmlSha256", "xmlMd5", "metadataSha256"):
                        if stored_origin.get(key) != origin[key]:
                            raise ValueError("draft conflicts with registered provenance")
                    stored_license = stored_origin.get("license")
                    if not isinstance(stored_license, dict) or stored_license.get("code") != origin["license"]["code"] or stored_license.get("url") != origin["license"]["url"]:
                        raise ValueError("draft conflicts with registered license")
                if not source_license_allowed(origin, current_app.config.get("CASE_REUSE_SCOPE", "commercial"),
                        current_app.config.get("CASE_ALLOW_CLINICAL_FACTS", False)):
                    report["skipped_policy"] += 1
                    continue
                existing = db.session.scalar(select(ClinicalCase).where(ClinicalCase.slug == data["caseId"]))
                if existing is not None:
                    # A package can newly authorize an untouched old draft.
                    # Historical authorization on a private case means deliberate
                    # suspension and must never be used to republish it here.
                    previous = existing.data
                    if (is_provisionally_accepted(data) and not existing.is_published
                            and isinstance(previous, dict) and previous.get("reviewStatus") == "review_required"
                            and "useAuthorization" not in previous
                            and previous == {key: value for key, value in data.items() if key != "useAuthorization"}):
                        if source.clinical_case_id not in {None, existing.id}:
                            raise ValueError("source already linked to a different case")
                        promotions.append((existing, data["useAuthorization"], source))
                    else:
                        report["unchanged"] += 1
                    continue
                if source.clinical_case_id is not None:
                    raise ValueError("source already linked to a different case")
                data["reviewStatus"] = "review_required"
                planned.append((data, source))
        for data, source in planned:
            provisional = is_provisionally_accepted(data)
            case = ClinicalCase(slug=data["caseId"], title=data["caseTitle"], specialty=data["specialty"],
                case_type=case_type_for(data), difficulty=data["difficulty"], summary=data["summary"],
                data=data, is_generated=False, is_published=provisional)
            db.session.add(case)
            db.session.flush()
            source.clinical_case_id = case.id
            source.translated = True
            source.import_status = "draft"
            source.specialty = data["specialty"]
            source.license_code = data["provenance"]["license"]["code"]
            if source.provenance is None:
                source.provenance = {key: value for key, value in data["provenance"].items()
                    if key in _BASE_PROVENANCE or key == "reuseBasis"}
            # verified and checkpoint hashes describe local source verification;
            # a portable draft alone must never establish or overwrite them.
            report["new"] += 1
            report["provisional"] += int(provisional)
        for existing, authorization, source in promotions:
            existing.data = {**existing.data, "useAuthorization": authorization}
            existing.is_published = True
            source.clinical_case_id = existing.id
            source.translated = True
            source.import_status = "draft"
            report["promoted"] += 1
            report["provisional"] += 1
        db.session.commit()
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, CaseValidationError, SQLAlchemyError):
        db.session.rollback()
        raise click.ClickException("O pacote de rascunhos reais é inválido ou não corresponde ao acervo. Nenhum rascunho deste lote foi alterado.") from None
    click.echo(f"Rascunhos reais: {report['new']} novos; {report['unchanged']} já existentes; "
        f"{report['promoted']} rascunhos anteriores disponibilizados por nova autorização; "
        f"{report['skipped_policy']} ignorados pela política atual; {report['skipped_excluded']} fontes excluídas. "
        f"{report['provisional']} disponibilizados por autorização provisória, com revisão clínica pendente. "
        "Os demais rascunhos permanecem privados; a verificação local das fontes é contada separadamente.")
    return report


def register_commands(app):
    @app.cli.command("import-cases")
    @click.option("--source", "sources", multiple=True, type=click.Path(exists=True, dir_okay=False, path_type=Path),
        help="Pacote JSON de Casos Básicos; repita para vários. Sem opção, usa os pacotes incluídos na aplicação.")
    def import_cases(sources):
        """Inclui exercícios fictícios básicos sem substituir fichas existentes."""
        from .migrations import upgrade_database
        upgrade_database()
        report = import_basic_cases(sources or None)
        click.echo(f"Casos Básicos: {report['new']} novos; {report['unchanged']} já existentes. Dados e revisões anteriores preservados.")

    @app.cli.command("export-real-drafts")
    @click.option("--output", type=click.Path(file_okay=False, path_type=Path), required=True)
    @click.option("--shard-size", type=click.IntRange(1, 500), default=100, show_default=True)
    def export_real_drafts(output, shard_size):
        """Exporta fichas reais pendentes, incluindo autorização provisória explícita."""
        from .draft_bundle import write_bundle
        from .case_schema import is_provisionally_accepted
        from .source_registry import excluded_pmcids
        excluded = excluded_pmcids()
        def cases():
            query = select(ClinicalCase).order_by(ClinicalCase.slug)
            for case in db.session.scalars(query).yield_per(50):
                data = case.data
                if (data.get("catalogType") == "published_case" and data.get("reviewStatus") == "review_required"
                        and data.get("provenance", {}).get("pmcid") not in excluded
                        and (not case.is_published or is_provisionally_accepted(data))):
                    # A deliberately suspended case must remain private when
                    # reinstalled, even if its historical JSON kept authorization.
                    if not case.is_published and "useAuthorization" in data:
                        import copy
                        data = copy.deepcopy(data)
                        data.pop("useAuthorization")
                    yield data
        try:
            manifest = write_bundle(cases(), output, shard_size=shard_size)
        except (OSError, ValueError, TypeError, KeyError):
            raise click.ClickException("Não foi possível exportar os rascunhos. Use um diretório vazio e confira as fichas; um manifesto só é criado após concluir todos os lotes.") from None
        click.echo(f"Exportados {manifest['cases']} rascunhos em {len(manifest['shards'])} lotes privados; autorizações provisórias ativas foram preservadas. Nenhum estado local foi alterado.")

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
        basic = import_basic_cases()
        restore_bundled_inventory()
        restore_bundled_drafts()
        click.echo(f"Banco pronto. {added} casos iniciais e {basic['new']} Casos Básicos incluídos. Dados existentes preservados.")

    @app.cli.command("upgrade-db")
    def upgrade_db():
        """Atualiza tabelas e metadados de catálogo sem alterar históricos."""
        from .migrations import upgrade_database
        from .catalog import SEED_CASE_TYPES, SPECIALTY_ALIASES
        from .catalogue import case_type_for
        from .discovery import refresh_discovery
        upgrade_database()
        last_id = 0
        final_id = db.session.scalar(select(ClinicalCase.id).order_by(ClinicalCase.id.desc()).limit(1)) or 0
        while last_id < final_id:
            # Bound the SQL result itself: MySQL's default buffered cursor would
            # otherwise hold every JSON document before ORM yield_per applies.
            batch = db.session.execute(select(ClinicalCase).where(
                ClinicalCase.id > last_id, ClinicalCase.id <= final_id
            ).order_by(ClinicalCase.id).limit(100)).scalars().all()
            if not batch:
                break
            for case in batch:
                data = dict(case.data)
                data.setdefault("caseType", SEED_CASE_TYPES.get(case.slug, "Primeira consulta"))
                data["specialty"] = SPECIALTY_ALIASES.get(case.specialty, case.specialty)
                if data != case.data:
                    case.data = data
                case.specialty, case.case_type = data["specialty"], case_type_for(data)
                refresh_discovery(case)
            last_id = batch[-1].id
            db.session.flush()
        db.session.commit()
        import_basic_cases()
        restore_bundled_inventory()
        restore_bundled_drafts()
        click.echo("Banco atualizado. Consultas e notas anteriores preservadas.")

    @app.cli.command("reindex-catalogue")
    def reindex_catalogue():
        """Reconstrói classificação e disponibilidade sem alterar fichas ou consultas."""
        from .migrations import upgrade_database
        from .discovery import rebuild_discovery
        upgrade_database()
        count = rebuild_discovery()
        click.echo(f"Catálogo atualizado: {count} casos. Fichas e históricos preservados.")

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
    @click.option("--all-records", is_flag=True, help="Processa todo o acervo elegível, inclusive além de 300 casos por especialidade.")
    @click.option("--source-workers", type=click.IntRange(1, 8), default=1, show_default=True, help="Conexões paralelas para verificar fontes.")
    @click.option("--adapt-workers", type=click.IntRange(1, 256), default=1, show_default=True, help="Casos preparados em paralelo por IA, até AI_IMPORT_WORKER_LIMIT (padrão 16). Acima de 1 exige --adapt e --all-records. Gravações no banco permanecem sequenciais.")
    @click.option("--adapt", is_flag=True, help="Classifica, traduz e audita com DeepSeek. Consome créditos do provedor.")
    @click.option("--retry-errors", is_flag=True, help="Tenta novamente registros que falharam anteriormente.")
    @click.option("--refresh-sources", is_flag=True, help="Reconsulta fontes ainda não adaptadas, incluindo rejeitadas.")
    def import_real_cases(source, work_dir, specialties, target_per_specialty, limit, adapt, retry_errors, refresh_sources, all_records, source_workers, adapt_workers):
        """Importa relatos reais em lotes retomáveis; todos entram como rascunhos privados."""
        from .importer import CaseImporter, ImportError as CaseImportError
        from .catalogue import catalogue_counts
        from .source_registry import register_sources, sync_import_records, source_counts, excluded_pmcids, SourceRegistryError
        from .migrations import upgrade_database
        upgrade_database()
        try:
            registration = register_sources(source)
            click.echo(f"Acervo registrado: {registration['database']['registered']} fontes no banco.")
            importer = CaseImporter(app.config, work_dir)
            added = 0
            def progress(item):
                nonlocal added
                if item['status'] == 'draft':
                    added += store_import_drafts(importer, pmcids=[item['pmcid']])
                sync_import_records(work_dir, pmcids=[item['pmcid']], include_counts=False)
                click.echo(f"{item['pmcid']}: {item['status']}" + (f" · {item['specialty']}" if item.get("specialty") else ""))
            report = importer.run(source, specialties=specialties or None, target_per_specialty=target_per_specialty,
                limit=limit, adapt=adapt, retry_errors=retry_errors, refresh_sources=refresh_sources,
                all_records=all_records, source_workers=source_workers, adapt_workers=adapt_workers,
                excluded_pmcids=excluded_pmcids(), on_progress=progress)
            # Recover previously checkpointed drafts only after the importer has
            # validated the CSV identity and completed its exclusive run.
            added += store_import_drafts(importer)
            sync_import_records(work_dir)
            report["newDatabaseDrafts"] = added
            report["sourceRegistry"] = source_counts()
            report["database"] = catalogue_counts(target_per_specialty)
            click.echo(json.dumps(report, ensure_ascii=False, indent=2))
            if report.get("stoppedEarly"):
                raise click.ClickException(report.get("stopMessage") or "O lote foi interrompido; os casos pendentes foram preservados.")
            if report.get("adaptationBlocked"):
                click.echo("Adaptação pendente: configure DEEPSEEK_API_KEY e DEMO_MODE=false; a verificação de fontes foi preservada.", err=True)
        except (CaseImportError, SourceRegistryError) as exc:
            db.session.rollback()
            raise click.ClickException(str(exc)) from exc

    @app.cli.command("register-sources")
    @click.option("--source", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
    @click.option("--work-dir", type=click.Path(file_okay=False, path_type=Path), default=Path("instance/imports/MedCaseReasoning/processed"))
    def register_sources_command(source, work_dir):
        """Registra o acervo completo no banco sem rede, tradução ou publicação."""
        from .migrations import upgrade_database
        from .source_registry import register_sources, sync_import_records, SourceRegistryError
        from .importer import ImportError as CaseImportError
        upgrade_database()
        try:
            report = register_sources(source)
            if (work_dir / "manifest.json").is_file():
                report["processing"] = sync_import_records(work_dir)
            click.echo(json.dumps(report, ensure_ascii=False, indent=2))
        except (CaseImportError, SourceRegistryError) as exc:
            db.session.rollback()
            raise click.ClickException(str(exc)) from exc

    @app.cli.command("exclude-restricted-sources")
    @click.option("--dataset", default="MedCaseReasoning", show_default=True)
    @click.option("--pmcid", "pmcids", multiple=True, required=True, help="Fonte rejeitada a excluir; repita para selecionar outras.")
    def exclude_restricted_sources(dataset, pmcids):
        """Exclui apenas fontes rejeitadas selecionadas; preserva casos vinculados."""
        from .migrations import upgrade_database
        from .source_registry import prune_restricted_sources, SourceRegistryError
        upgrade_database()
        try:
            click.echo(json.dumps(prune_restricted_sources(dataset=dataset, pmcids=pmcids), ensure_ascii=False, indent=2))
        except SourceRegistryError as exc:
            db.session.rollback()
            raise click.ClickException(str(exc)) from exc

    @app.cli.command("source-report")
    def source_report():
        """Conta fontes, traduções e casos reais diretamente no banco."""
        from .source_registry import source_counts
        click.echo(json.dumps(source_counts(), ensure_ascii=False, indent=2))

    @app.cli.command("export-sources")
    @click.option("--output", type=click.Path(dir_okay=False, path_type=Path), required=True)
    def export_sources_command(output):
        """Exporta inventário privado portátil, sem artigos integrais ou casos."""
        from .source_registry import export_source_registry, SourceRegistryError
        try:
            click.echo(json.dumps(export_source_registry(output), ensure_ascii=False, indent=2))
        except SourceRegistryError as exc:
            raise click.ClickException(str(exc)) from exc

    @app.cli.command("restore-sources")
    @click.option("--source", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
    def restore_sources_command(source):
        """Restaura o inventário no cPanel; não cria casos nem alega traduções."""
        from .migrations import upgrade_database
        from .source_registry import import_source_registry, SourceRegistryError
        upgrade_database()
        try:
            click.echo(json.dumps(import_source_registry(source), ensure_ascii=False, indent=2))
        except SourceRegistryError as exc:
            raise click.ClickException(str(exc)) from exc

    @app.cli.command("import-report")
    @click.option("--work-dir", type=click.Path(file_okay=False, path_type=Path), default=Path("instance/imports/MedCaseReasoning/processed"), show_default=True)
    def import_report(work_dir):
        """Exibe progresso de fontes/tradução e contagem publicada separadamente."""
        from .importer import CaseImporter, ImportError as CaseImportError
        from .catalogue import catalogue_counts
        try:
            from .source_registry import excluded_pmcids
            report = CaseImporter(app.config, work_dir).report(excluded_pmcids=excluded_pmcids())
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
