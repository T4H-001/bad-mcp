# Synal-Core

## Qwen Code Integration

Synal-Core includes the Qwen Code GitHub Action integration for governed
AI-assisted repository operations.

### Supported interactions

- `@qwencoder` — invoke Qwen Code from supported GitHub conversations
- `@qwencoder /review` — request a pull-request review
- `@qwencoder /triage` — request issue triage
- Pull-request review workflow
- Issue triage workflow

### Configuration

- `QWEN.md` — Synal-Core agent operating contract
- `.qwen/settings.json` — Qwen Code settings
- `.github/workflows/qwen-dispatch.yml` — command dispatcher
- `.github/workflows/qwen-invoke.yml` — Qwen invocation
- `.github/workflows/qwen-review.yml` — pull-request review
- `.github/workflows/qwen-triage.yml` — issue triage

Authentication is supplied through GitHub encrypted secrets. Credentials,
API keys and tokens must never be committed to the repository.

### Governance

Qwen changes are subject to repository governance and evidence requirements.
Agent output is not treated as verified merely because a workflow completed;
material changes require validation and observable evidence.
