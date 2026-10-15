"""Private registry UI: real counts, publication state, filtering and isolation."""
import html
import re
from urllib.parse import parse_qs, urlsplit

from medlab.extensions import db
from medlab.models import ClinicalCase, SourceRecord


def add_source(index, **overrides):
    data = dict(dataset="MedCaseReasoning", pmcid=f"PMC990{index:04}",
        dataset_revision="a" * 40, dataset_url="https://example.invalid/dataset",
        source_file_sha256="b" * 64, record_sha256=f"{index:064x}",
        source_row_number=index, original_title=f"Original clinical report {index}",
        original_diagnosis=f"Original diagnosis {index}", original_metadata={},
        import_status="registered", verified=False, translated=False)
    data.update(overrides)
    if data["verified"]:
        data.setdefault("license_code", "CC BY")
        data.setdefault("provenance", {"license": {"code": "CC BY", "url": "https://creativecommons.org/licenses/by/4.0/"}})
    record = SourceRecord(**data)
    db.session.add(record)
    return record


def count(body, name):
    return int(re.search(rf'data-source-count="{name}".*?<strong>([\d.]+)</strong>', body, re.S).group(1).replace(".", ""))


def source_ids(body):
    return re.findall(r'data-source-id="(\d+)"', body)


def test_verified_filter_and_totals_follow_factual_opt_in_and_scope(app, client, login):
    with app.app_context():
        add_source(1, verified=True)
        add_source(2, verified=True, license_code="CC BY-NC-ND", provenance={
            "reuseBasis": "clinical_facts", "license": {"code": "CC BY-NC-ND", "url": "https://creativecommons.org/licenses/by-nc-nd/4.0/"}})
        db.session.commit()
    login("admin@example.invalid", admin=True)
    for scope, enabled, expected in (("noncommercial", False, 1), ("noncommercial", True, 2), ("commercial", True, 1)):
        app.config.update(CASE_REUSE_SCOPE=scope, CASE_ALLOW_CLINICAL_FACTS=enabled)
        response = client.get("/gestao-teste/acervo?stage=verified")
        assert response.status_code == 200
        assert len(source_ids(response.text)) == count(response.text, "verified") == expected


def test_registry_requires_private_admin_login_and_is_not_linked_publicly(client, login):
    assert client.get("/gestao-teste/acervo").status_code == 404
    assert "/acervo" not in client.get("/").text
    login()
    assert client.get("/gestao-teste/acervo").status_code == 404
    assert "/acervo" not in client.get("/painel").text
    login("admin@example.invalid")
    assert client.get("/gestao-teste/acervo").status_code == 404
    login("admin@example.invalid", admin=True)
    response = client.get("/gestao-teste/acervo")
    assert response.status_code == 200
    assert 'name="robots" content="noindex,nofollow"' in response.text
    assert '/gestao-teste/acervo' in client.get("/gestao-teste").text


def test_registry_handles_no_records_without_invented_totals(client, login):
    login("admin@example.invalid", admin=True)
    response = client.get("/gestao-teste/acervo")
    assert response.status_code == 200
    assert "O acervo está pronto para receber fontes." in response.text
    for key in ("registered", "verified", "translated", "drafts", "published"):
        assert count(response.text, key) == 0
    assert "14.489" not in response.text
    assert "A presença no acervo não significa que a tradução já foi feita." in response.text


def test_progress_counts_are_cumulative_and_publication_uses_live_case_state(app, client, login):
    with app.app_context():
        published = db.session.scalar(db.select(ClinicalCase).where(ClinicalCase.is_published.is_(True)))
        draft = db.session.scalar(db.select(ClinicalCase).where(ClinicalCase.is_published.is_(False)))
        add_source(1)
        add_source(2, verified=True, import_status="verified", license_code="CC BY")
        add_source(3, verified=True, translated=True, import_status="draft", clinical_case_id=draft.id)
        add_source(4, verified=True, translated=True, import_status="draft", clinical_case_id=published.id)
        add_source(5, import_status="license_rejected", last_error="Licença fora da política atual.")
        draft_id = draft.id
        db.session.commit()
    login("admin@example.invalid", admin=True)
    response = client.get("/gestao-teste/acervo")
    assert [count(response.text, key) for key in ("registered", "verified", "translated", "drafts", "published")] == [5, 3, 2, 1, 1]
    assert "Não elegível na política atual" in response.text
    assert "Outras licenças podem permitir uso e tradução sob condições próprias" in response.text
    for stage, expected in (("verified",3),("translated",2),("untranslated",3),("draft",1),("published",1)):
        body = client.get(f"/gestao-teste/acervo?stage={stage}").text
        assert len(source_ids(body)) == expected
        assert count(body,"registered") == 5
    with app.app_context():
        db.session.get(ClinicalCase,draft_id).is_published = True
        db.session.commit()
    body = client.get("/gestao-teste/acervo?stage=published").text
    assert len(source_ids(body)) == 2
    assert count(body,"published") == 2
    assert count(body,"drafts") == 0


def test_search_filters_and_pagination_preserve_selection(app, client, login):
    with app.app_context():
        for index in range(1,28):
            add_source(index, original_title=f"Chest pain report {index}", specialty="Cardiologia", import_status="verified", verified=True)
        add_source(28, dataset="OtherSource", specialty="Pediatria", original_title="Other story")
        db.session.commit()
    login("admin@example.invalid", admin=True)
    url = "/gestao-teste/acervo?q=Chest&dataset=MedCaseReasoning&specialty=Cardiologia&stage=verified&status=verified"
    first = client.get(url)
    assert first.status_code == 200
    assert len(source_ids(first.text)) == 25
    links = [html.unescape(value) for value in re.findall(r'href="([^"]+)"',first.text) if "page=2" in value]
    assert links
    parameters = parse_qs(urlsplit(links[0]).query)
    assert parameters == {"page":["2"],"q":["Chest"],"dataset":["MedCaseReasoning"],"specialty":["Cardiologia"],"stage":["verified"],"status":["verified"]}
    second = client.get(links[0])
    assert len(source_ids(second.text)) == 2
    assert set(source_ids(first.text)).isdisjoint(source_ids(second.text))
    assert count(second.text,"registered") == 28
    assert len(source_ids(client.get("/gestao-teste/acervo?dataset=OtherSource").text)) == 1
    assert len(source_ids(client.get("/gestao-teste/acervo?specialty=Pediatria").text)) == 1
    assert len(source_ids(client.get("/gestao-teste/acervo?q=PMC9900028").text)) == 1


def test_original_fields_are_escaped_and_search_wildcards_are_literal(app, client, login):
    with app.app_context():
        add_source(1, original_title='<script>alert("original")</script> 100% finding',
            original_diagnosis='<img src=x onerror=alert("diagnosis")>',
            original_metadata={"article_link":"javascript:alert('link')"})
        add_source(2, original_title="Other report")
        db.session.commit()
    login("admin@example.invalid", admin=True)
    body = client.get("/gestao-teste/acervo?q=%25").text
    assert len(source_ids(body)) == 1
    assert '<script>alert("original")</script>' not in body
    assert '&lt;script&gt;' in body
    assert '<img src=x' not in body
    assert 'href="javascript:' not in body
    assert 'href="https://pmc.ncbi.nlm.nih.gov/articles/PMC9900001/"' in body
    assert "Português ainda não preparado" in body


def test_invalid_and_out_of_range_pages_recover_without_large_offset(app, client, login):
    with app.app_context():
        add_source(1)
        db.session.commit()
    login("admin@example.invalid", admin=True)
    for page in ("abc","-5"):
        assert client.get(f"/gestao-teste/acervo?page={page}").status_code == 200
    response = client.get("/gestao-teste/acervo?page=999999999999999999999999999999&q=Original")
    assert response.status_code == 302
    assert "page=1" in response.location and "q=Original" in response.location
    assert client.get(response.location).status_code == 200
    empty = client.get("/gestao-teste/acervo?q=not-found")
    assert empty.status_code == 200
    assert "Nenhuma fonte nesta seleção." in empty.text
    assert count(empty.text,"registered") == 1


def test_checked_restrictions_and_failed_verification_are_not_presented_as_pending(app, client, login):
    with app.app_context():
        add_source(1, import_status="license_rejected", last_error="CC-BY-SA fora da política atual.")
        add_source(2, import_status="source_error", last_error="Fonte indisponível.")
        db.session.commit()
    login("admin@example.invalid", admin=True)
    body = client.get("/gestao-teste/acervo").text
    assert "Verificação concluída com restrição" in body
    assert "Verificação com falha" in body
    assert "Verificação pendente" not in body
    assert "tradução proibida" not in body
