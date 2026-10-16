"""License notices use each source's terms and appear only after the encounter."""
from copy import deepcopy

import pytest

from medlab.extensions import db
from medlab.models import ClinicalCase, Consultation


@pytest.mark.parametrize("scope,expected,absent", [
    ("noncommercial", "Política de uso exclusivamente não comercial.", "Política de reutilização com possibilidade de uso comercial."),
    ("commercial", "Política de reutilização com possibilidade de uso comercial.", "Política de uso exclusivamente não comercial."),
])
def test_private_registry_policy_text_follows_configured_scope(app, client, login, scope, expected, absent):
    app.config["CASE_REUSE_SCOPE"] = scope
    login("admin@example.invalid", admin=True)
    response = client.get("/gestao-teste/acervo")
    assert response.status_code == 200
    assert expected in response.text and absent not in response.text
    assert f'data-reuse-scope="{scope}"' in response.text
    if scope == "noncommercial":
        assert "CC BY, CC0, CC BY-NC, CC BY-SA e CC BY-NC-SA" in response.text
        assert "A versão e as condições de cada licença são conferidas na fonte." in response.text
    else:
        assert "Esta instalação admite fontes elegíveis com CC BY ou CC0." in response.text
        assert "CC BY, CC0, CC BY-NC, CC BY-SA e CC BY-NC-SA" not in response.text


@pytest.mark.parametrize("code,url,nc,sharealike", [
    ("CC BY", "https://creativecommons.org/licenses/by/4.0/", False, False),
    ("CC0", "https://creativecommons.org/publicdomain/zero/1.0/", False, False),
    ("CC BY-NC", "https://creativecommons.org/licenses/by-nc/3.0/", True, False),
    ("CC BY-SA", "https://creativecommons.org/licenses/by-sa/4.0/", False, True),
    ("CC BY-NC-SA", "https://creativecommons.org/licenses/by-nc-sa/4.0/", True, True),
    ("CC BY-ND", "https://creativecommons.org/licenses/by-nd/4.0/", False, False),
    ("CC BY-NC-ND", "https://creativecommons.org/licenses/by-nc-nd/4.0/", True, False),
])
def test_completed_result_shows_source_specific_nc_sa_conditions_without_early_spoilers(app, client, login, code, url, nc, sharealike):
    app.config["CASE_REUSE_SCOPE"] = "noncommercial"
    version = url.rstrip("/").rsplit("/",1)[-1]
    factual = code.endswith("-ND")
    article_title = "Private source title only after completion"
    with app.app_context():
        case = db.session.scalar(db.select(ClinicalCase).where(ClinicalCase.slug == "cenario-teste-1"))
        snapshot = deepcopy(case.data)
        # Rendering fixtures deliberately exercise historical licenses without
        # optional flags, since old stored consultations retain their snapshots.
        snapshot["provenance"] = {"articleTitle": article_title,
            "articleUrl": "https://example.invalid/original-source", "citation": "Synthetic source citation",
            "authors": ["Author from source"], "pmcid": "PMC123", "doi": None,
            "license": {"code": code, "url": url, "version": version, "text": "Source license text"},
            "clinicalMoment": "Primeiro encontro, antes do diagnóstico.",
            "changes": ["Texto traduzido e identidade substituída para simulação."], "missingFacts": []}
        if factual:
            snapshot["provenance"]["reuseBasis"] = "clinical_facts"
            snapshot["provenance"]["changes"] = ["Roteiro original com identidade simulada e fatos conferidos."]
        case.data = snapshot
        case_id = case.id
        db.session.commit()
    login()
    started = client.post("/casos/iniciar", data={"case_id": case_id})
    assert started.status_code == 302
    consultation_url = started.location
    result_url = consultation_url + "/resultado"
    for page in ("/casos", "/painel", consultation_url):
        response = client.get(page)
        assert response.status_code == 200
        assert article_title not in response.text
        assert "data-license-noncommercial" not in response.text
        assert "data-license-sharealike" not in response.text
        assert url not in response.text
        assert "Source license text" not in response.text
    early_result = client.get(result_url)
    assert early_result.status_code == 302
    assert early_result.location == consultation_url
    with app.app_context():
        training = db.session.get(Consultation, consultation_url.rsplit("/",1)[-1])
        training.status = "completed"
        training.evaluation = {"score": None, "summary": "Feedback de teste.", "sections": [],
            "strengths": [], "improvements": [], "next_steps": []}
        training.diagnosis = "Hipótese de teste"
        training.treatment = "Conduta de teste"
        db.session.commit()
    result = client.get(result_url)
    assert result.status_code == 200
    assert article_title in result.text and "Author from source" in result.text
    assert f'href="{url}"' in result.text
    assert "Texto de licença informado pela fonte" in result.text
    assert "Source license text" in result.text
    assert ("data-license-noncommercial" in result.text) is nc
    assert ("data-license-sharealike" in result.text) is sharealike
    if nc:
        assert "Uso não comercial" in result.text
    if sharealike:
        assert "Esta adaptação é disponibilizada sob a mesma licença" in result.text
        assert f"{code} {version}</a> do relato original" in result.text
    if factual:
        assert 'data-source-reuse="clinical_facts"' in result.text
        assert "Fonte factual, com redação original." in result.text
        assert "Essa licença não é atribuída à ficha" in result.text
        assert "não autoriza sua adaptação textual" in result.text
        assert "Licença do artigo" in result.text
        assert "Adaptação educacional em português." not in result.text
        assert "O uso deste material e de sua adaptação" not in result.text
        assert "Texto traduzido" not in result.text
    else:
        assert "data-source-factual-notice" not in result.text
    assert "/gestao-teste" not in result.text


@pytest.mark.parametrize("enabled", [False, True])
def test_private_policy_explains_factual_option_only_when_enabled(app, client, login, enabled):
    app.config["CASE_ALLOW_CLINICAL_FACTS"] = enabled
    login("admin@example.invalid", admin=True)
    response = client.get("/gestao-teste/acervo")
    assert ("data-source-factual-policy" in response.text) is enabled
    if enabled:
        assert "Esse fluxo não autoriza tradução ou adaptação textual do artigo" in response.text


@pytest.mark.parametrize("basis,scope,enabled,expected", [
    ("factual", "noncommercial", False, False),
    ("factual", "noncommercial", True, True),
    ("licensed_nc", "commercial", False, False),
    ("licensed_nc", "noncommercial", False, True),
])
def test_publication_rechecks_current_policy_without_deleting_draft(app, client, login, basis, scope, enabled, expected):
    from medlab.case_schema import validate_case
    from test_clinical_facts import factual_inputs
    from test_importer import ARTICLE, SPEC, real_case

    if basis == "factual":
        service, provenance, *_ = factual_inputs()
        payload = service.adapt_case({"articleText": ARTICLE}, provenance, SPEC)
    else:
        payload = real_case()
        payload["provenance"]["license"].update(code="CC BY-NC", url="https://creativecommons.org/licenses/by-nc/4.0/")
        payload = validate_case(payload)
    app.config.update(CASE_REUSE_SCOPE=scope, CASE_ALLOW_CLINICAL_FACTS=enabled)
    with app.app_context():
        draft = db.session.scalar(db.select(ClinicalCase).where(ClinicalCase.is_published.is_(False)))
        draft.data = payload
        draft_id = draft.id
        db.session.commit()
    login("admin@example.invalid", admin=True)
    response = client.post(f"/gestao-teste/casos/{draft_id}/publicar", data={"review_confirmed": "yes"}, follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        draft = db.session.get(ClinicalCase, draft_id)
        assert draft is not None and draft.is_published is expected
        assert draft.data["reviewStatus"] == ("reviewed" if expected else "review_required")
    if expected:
        assert "Caso publicado e disponível para os alunos." in response.text
    else:
        assert "não atende à política de reutilização atual" in response.text
