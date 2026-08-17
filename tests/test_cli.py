import json
from watermark_generator.cli import main


def test_cli_hides_secrets_and_export_excludes_roots(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WATERMARK_GENERATOR_PASSPHRASE", "correct horse battery staple")
    assert main(["--root", str(tmp_path), "init"]) == 0
    capsys.readouterr()
    assert main(["--root", str(tmp_path), "--models", "openai", "--watermark", "formula"]) == 0
    output = capsys.readouterr().out
    vault = json.loads((tmp_path / "private" / "vault.json").read_text())
    assert vault["ciphertext"] not in output and "KEY=" not in output
    assert main(["--root", str(tmp_path), "export", "--model", "openai"]) == 0
    exported = capsys.readouterr().out
    assert "KEY=" in exported
    assert "identity_private_key" not in exported and "master_secret" not in exported


def test_show_secrets_is_explicit(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WATERMARK_GENERATOR_PASSPHRASE", "correct horse battery staple")
    main(["--root", str(tmp_path), "init"]); capsys.readouterr()
    main(["--root", str(tmp_path), "--models", "openai", "--watermark", "one", "--show-secrets"])
    assert "WARNING: WATERMARK SECRET EXPOSED" in capsys.readouterr().out


def test_verify_returns_nonzero_after_tamper(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WATERMARK_GENERATOR_PASSPHRASE", "correct horse battery staple")
    main(["--root", str(tmp_path), "init"]); capsys.readouterr()
    main(["--root", str(tmp_path), "--models", "openai", "--watermark", "one"]); capsys.readouterr()
    path = next(p for p in (tmp_path / "public" / "manifests").glob("*create*"))
    data = json.loads(path.read_text()); data["status"] = "REVOKED"; path.write_text(json.dumps(data))
    assert main(["--root", str(tmp_path), "verify", str(path)]) == 2


def test_older_signed_manifest_is_rejected_as_rollback(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WATERMARK_GENERATOR_PASSPHRASE", "correct horse battery staple")
    main(["--root", str(tmp_path), "init"]); capsys.readouterr()
    main(["--root", str(tmp_path), "--models", "openai", "--watermark", "one"]); capsys.readouterr()
    old = next(p for p in (tmp_path / "public" / "manifests").glob("*create*"))
    main(["--root", str(tmp_path), "rotate", "--model", "openai", "--reason", "manual"]); capsys.readouterr()
    assert main(["--root", str(tmp_path), "verify", str(old)]) == 3
    assert "ROLLBACK REJECTED" in capsys.readouterr().out
