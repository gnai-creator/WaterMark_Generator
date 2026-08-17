import json
import getpass
import pytest
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


def test_cli_loads_passphrase_from_root_dotenv(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("WATERMARK_GENERATOR_PASSPHRASE", raising=False)
    (tmp_path / ".env").write_text(
        "# local secret\nWATERMARK_GENERATOR_PASSPHRASE='dotenv passphrase 123'\n"
    )
    monkeypatch.setattr(getpass, "getpass", lambda _prompt: pytest.fail("must not prompt"))
    assert main(["--root", str(tmp_path), "init"]) == 0
    capsys.readouterr()
    assert main(["--root", str(tmp_path), "--models", "openai", "--watermark", "one"]) == 0


def test_session_prompt_is_visible_only_and_contains_no_key(tmp_path, capsys):
    private = tmp_path / "private"; private.mkdir()
    (private / "watermark.txt").write_text("AUTHORIZED_VISIBLE_MARK")
    assert main(["--root", str(tmp_path), "session-prompt"]) == 0
    output = capsys.readouterr().out
    assert "AUTHORIZED_VISIBLE_MARK" in output
    assert "FMM: VISIBLE MARK ONLY" in output
    assert "KEY=" not in output


def test_session_local_is_one_command_after_env_configuration(tmp_path, monkeypatch, capsys):
    from watermark_generator import local_runtime

    class FakeRuntime:
        vocabulary_size = 16
        eos_token_id = None
        def __init__(self, *_args, **_kwargs): pass
        def encode(self, text): return [ord(char) % 16 for char in text] or [1]
        def decode(self, tokens): return "generated"
        def next_token_logits(self, tokens): return [0.0] * 16

    monkeypatch.setattr(local_runtime, "TransformersRuntime", FakeRuntime)
    monkeypatch.setenv("WATERMARK_GENERATOR_PASSPHRASE", "correct horse battery staple")
    assert main(["--root", str(tmp_path), "init"]) == 0
    assert main(["--root", str(tmp_path), "--models", "openai", "--watermark", "one"]) == 0
    monkeypatch.delenv("WATERMARK_GENERATOR_PASSPHRASE")
    private = tmp_path / "private"
    (private / "intensity.json").write_text("[1,1,1,1]")
    (tmp_path / ".env").write_text(
        "WATERMARK_GENERATOR_PASSPHRASE='correct horse battery staple'\n"
        "WATERMARK_LOCAL_MODEL='/local/model'\n"
        "WATERMARK_INTENSITY_TABLE='private/intensity.json'\n"
        "WATERMARK_SESSION_PROVIDER='openai'\n"
        "WATERMARK_PERIOD=4\nWATERMARK_MINIMUM_TOKENS=2\n"
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: "/exit")
    capsys.readouterr()
    assert main(["--root", str(tmp_path), "session-local", "--prompt", "hello",
                 "--max-new-tokens", "3", "--temperature", "0"]) == 0
    captured = capsys.readouterr()
    assert "generated" in captured.out and "FMM: APPLIED" in captured.err
    assert "KEY=" not in captured.out + captured.err
    assert "KEY" not in __import__("os").environ and "KEY_ID" not in __import__("os").environ
