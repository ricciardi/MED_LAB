"""Isolated integration fixtures; never load a development/production database."""

from copy import deepcopy

import pytest

from medlab import create_app
from medlab.case_schema import SECTION_TITLES, validate_case
from medlab.extensions import db
from medlab.models import ClinicalCase, User


TEST_PASSWORD = "Teste-local-2026!"


@pytest.fixture
def case_data():
    """Synthetic schema fixture; not a clinical teaching case."""
    return validate_case({
        "caseId": "cenario-teste",
        "caseTitle": "Cenário de teste automatizado",
        "specialty": "Clínica médica",
        "difficulty": "Iniciante",
        "summary": "Pessoa fictícia apresenta um sintoma para testar o sistema.",
        "patientProfile": {
            "name": "Paciente Fictício",
            "age_value": 32,
            "age_unit": "anos",
            "gender": "Feminino",
            "occupation": "Profissão fictícia",
            "chief_complaint": "Estou com um desconforto desde ontem.",
            "present_illness_history": "Desconforto fictício iniciado ontem.",
            "past_medical_history": "Sem antecedentes no cenário.",
            "personal_history": "Informação fictícia para teste.",
            "family_history": "Sem antecedentes no cenário.",
            "other_medical_history": "Sem outros antecedentes no cenário.",
            "allergy_history": "Nega alergias no cenário fictício.",
            "idea": "Não sabe a origem do desconforto.",
            "concern": "Gostaria de compreender o sintoma.",
            "expectation": "Espera uma explicação.",
        },
        "mustConsultionItems": ["Queixa principal", "Início dos sintomas"],
        "optionalConsultionItems": ["Alergias"],
        "mustPhysicalExams": ["Sinais vitais"],
        "optionalPhysicalExams": [],
        "mustAuxiliaryLabs": ["Exame laboratorial fictício"],
        "optionalAuxiliaryLabs": [],
        "physicalExams": {"Sinais vitais": "Resultado físico de teste."},
        "auxiliaryLabs": {"Exame laboratorial fictício": "Resultado laboratorial de teste."},
        "diagnoses": {
            "mainDiagnosis": {"disease": "Diagnóstico reservado do cenário"},
            "differentialDiagnoses": [{"disease": "Diferencial fictício"}],
        },
        "treatments": "Plano reservado do cenário de teste.",
        "learningObjectives": ["Verificar a aplicação, sem significado clínico."],
        "references": [{"title": "Referência sintética", "url": "https://example.invalid/referencia"}],
        "rubric": {
            key: {"criteria": ["Critério reservado de teste."], "criticalActions": []}
            for key in SECTION_TITLES
        },
        "simulationOnly": True,
        "reviewStatus": "reviewed",
    })


@pytest.fixture
def app(case_data, monkeypatch):
    # An explicit config wins over every environment variable, including .env.
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    application = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-only-secret-key-which-is-never-a-production-secret",
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "WTF_CSRF_ENABLED": False,
        "SESSION_COOKIE_SECURE": False,
        "DEMO_MODE": True,
        "DEEPSEEK_API_KEY": "",
        "ADMIN_PATH": "/gestao-teste",
        "MAX_DAILY_AI_REQUESTS": 120,
    })
    with application.app_context():
        db.create_all()
        for email, name, admin in (
            ("aluna@example.invalid", "Aluna Teste", False),
            ("colega@example.invalid", "Colega Teste", False),
            ("admin@example.invalid", "Gestão Teste", True),
        ):
            user = User(email=email, name=name, is_admin=admin)
            user.set_password(TEST_PASSWORD)
            db.session.add(user)
        for index, published in enumerate((True, True, False), start=1):
            payload = deepcopy(case_data)
            payload["caseId"] = f"cenario-teste-{index}"
            payload["caseTitle"] = f"Cenário de teste {index}"
            db.session.add(ClinicalCase(
                slug=payload["caseId"], title=payload["caseTitle"],
                specialty=payload["specialty"], difficulty=payload["difficulty"],
                summary=payload["summary"], data=payload,
                is_published=published, is_generated=not published,
            ))
        db.session.commit()
    yield application
    with application.app_context():
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def login(client):
    def authenticate(email="aluna@example.invalid", *, admin=False):
        response = client.post(
            "/gestao-teste/login" if admin else "/entrar",
            data={"email": email, "password": TEST_PASSWORD},
        )
        assert response.status_code in (302, 303)
        return client
    return authenticate


@pytest.fixture
def start_session(app, login):
    def start(case_index=1):
        authenticated = login()
        with app.app_context():
            case = db.session.scalar(db.select(ClinicalCase).where(
                ClinicalCase.slug == f"cenario-teste-{case_index}"
            ))
            case_id = case.id
        response = authenticated.post("/casos/iniciar", data={"case_id": case_id})
        assert response.status_code in (302, 303)
        return response.headers["Location"].rstrip("/").split("/")[-1]
    return start
