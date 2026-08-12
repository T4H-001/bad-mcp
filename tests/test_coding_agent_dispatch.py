from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from synal import coding_agent_dispatch as cad


def test_only_canonical_kimi_work_key_matches():
    assert cad.is_kimi_work(cad.KIMI_WORK_KEY)
    assert not cad.is_kimi_work("github:other:work")


def test_explicit_checkout_requires_governed_verifier(tmp_path, monkeypatch):
    monkeypatch.setenv("T4H_KIMI_REPO_ROOT", str(tmp_path))
    runtime = SimpleNamespace(STATE_DB=tmp_path / "state" / "work.db")
    with pytest.raises(RuntimeError, match="KIMI_CHECKOUT_INVALID"):
        cad.resolve_checkout(runtime)


def test_explicit_checkout_is_provider_neutral(tmp_path, monkeypatch):
    verifier = tmp_path / "scripts" / "tools" / "verify-kimi-code.sh"
    verifier.parent.mkdir(parents=True)
    verifier.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    monkeypatch.setenv("T4H_TARGET_REPO_ROOT", str(tmp_path))
    monkeypatch.delenv("T4H_KIMI_REPO_ROOT", raising=False)
    runtime = SimpleNamespace(STATE_DB=tmp_path / "state" / "work.db")
    assert cad.resolve_checkout(runtime) == tmp_path.resolve()


def test_verifier_success_returns_receipt(tmp_path, monkeypatch):
    verifier = tmp_path / "scripts" / "tools" / "verify-kimi-code.sh"
    verifier.parent.mkdir(parents=True)
    verifier.write_text("#!/usr/bin/env bash\necho RECEIPT=/tmp/kimi.json\nexit 0\n", encoding="utf-8")
    monkeypatch.setenv("T4H_KIMI_REPO_ROOT", str(tmp_path))
    runtime = SimpleNamespace(STATE_DB=tmp_path / "state" / "work.db")
    result = cad.run_kimi_verifier(runtime)
    assert result["exit_code"] == 0
    assert result["receipt_path"] == "/tmp/kimi.json"


def test_missing_kimi_is_dependency_block_not_generic_model_fallback(tmp_path, monkeypatch):
    verifier = tmp_path / "scripts" / "tools" / "verify-kimi-code.sh"
    verifier.parent.mkdir(parents=True)
    verifier.write_text("#!/usr/bin/env bash\necho 'kimi executable not found'\nexit 2\n", encoding="utf-8")
    monkeypatch.setenv("T4H_KIMI_REPO_ROOT", str(tmp_path))
    runtime = SimpleNamespace(STATE_DB=tmp_path / "state" / "work.db")
    with pytest.raises(RuntimeError, match="KIMI_EXECUTABLE_UNAVAILABLE_ON_RUNTIME_HOST"):
        cad.run_kimi_verifier(runtime)
