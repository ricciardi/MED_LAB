"""Secret-entry behavior using synthetic tokens and temporary files only."""
import os
import stat
import warnings

from dotenv import dotenv_values
import pytest

from scripts import configure_deepseek as setup


TOKEN = "sk-test-only-not-a-real-credential-12345"


def test_preserves_other_variables_comments_and_multiline_values(tmp_path):
    path = tmp_path / ".env"
    preserved = "# configuração existente\r\nSECRET_KEY='keep-this-value'\r\nOTHER='first\nDEMO_MODE=inside-a-value\nlast'\n"
    path.write_bytes((preserved + "export DEEPSEEK_API_KEY=old-value\nDEMO_MODE=true\nDEEPSEEK_API_KEY=duplicate\n").encode())
    path.chmod(0o644)
    setup.configure(path, TOKEN)
    content = path.read_bytes().decode()
    values = dotenv_values(path, interpolate=False)
    assert content.startswith(preserved)
    assert values["DEEPSEEK_API_KEY"] == TOKEN
    assert values["DEMO_MODE"] == "false"
    assert values["OTHER"] == "first\nDEMO_MODE=inside-a-value\nlast"
    assert content.count("DEEPSEEK_API_KEY=") == 1
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert list(tmp_path.iterdir()) == [path]


def test_creates_private_env_and_status_does_not_reveal_token(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(setup, "ROOT", tmp_path)
    assert setup.main(["--check"]) == 1
    monkeypatch.setattr(setup.getpass, "getpass", lambda prompt: TOKEN)
    assert setup.main([]) == 0
    assert setup.main(["--check"]) == 0
    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err
    assert "não validada na API" in captured.out
    assert stat.S_IMODE((tmp_path / ".env").stat().st_mode) == 0o600


def test_unknown_arguments_are_not_echoed(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(setup, "ROOT", tmp_path)
    assert setup.main(["--key", TOKEN]) == 2
    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err
    assert not (tmp_path / ".env").exists()


def test_no_terminal_never_falls_back_to_visible_input(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(setup, "ROOT", tmp_path)
    def visible_fallback(prompt):
        warnings.warn("Cannot control echo", setup.getpass.GetPassWarning)
        pytest.fail("Visible fallback must never proceed")
    monkeypatch.setattr(setup.getpass, "getpass", visible_fallback)
    assert setup.main([]) == 1
    assert "Terminal interativo" in capsys.readouterr().err
    assert not (tmp_path / ".env").exists()


def test_symlink_refused_for_read_and_write(tmp_path):
    target = tmp_path / "outside-dummy.txt"
    target.write_text("SAFE_DUMMY=unchanged\n")
    path = tmp_path / ".env"
    path.symlink_to(target)
    with pytest.raises(setup.ConfigurationError):
        setup.configured(path)
    with pytest.raises(setup.ConfigurationError):
        setup.configure(path, TOKEN)
    assert target.read_text() == "SAFE_DUMMY=unchanged\n"
    assert path.is_symlink()


@pytest.mark.parametrize("key", ["", "too-short", TOKEN + "\nDEMO_MODE=true", TOKEN + "${ENV}"])
def test_invalid_token_does_not_modify_env(tmp_path, key):
    path = tmp_path / ".env"
    path.write_text("SECRET_KEY=preserved\n")
    with pytest.raises(setup.ConfigurationError):
        setup.configure(path, key)
    assert path.read_text() == "SECRET_KEY=preserved\n"


def test_failed_write_preserves_original_and_removes_private_temporary(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("SECRET_KEY=preserved\n")
    def fail_replace(*args):
        raise OSError("simulated write failure")
    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError):
        setup.configure(path, TOKEN)
    assert path.read_text() == "SECRET_KEY=preserved\n"
    assert list(tmp_path.iterdir()) == [path]


def test_concurrent_file_edit_is_not_overwritten(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("SECRET_KEY=before\n")
    original_fsync = os.fsync
    def external_change(fd):
        original_fsync(fd)
        path.write_text("SECRET_KEY=external-change\n")
    monkeypatch.setattr(os, "fsync", external_change)
    with pytest.raises(setup.ConfigurationError):
        setup.configure(path, TOKEN)
    assert path.read_text() == "SECRET_KEY=external-change\n"
    assert list(tmp_path.iterdir()) == [path]


def test_malformed_env_is_not_rewritten(tmp_path):
    path = tmp_path / ".env"
    path.write_text("OTHER='unterminated\n")
    with pytest.raises(setup.ConfigurationError):
        setup.configure(path, TOKEN)
    assert path.read_text() == "OTHER='unterminated\n"
