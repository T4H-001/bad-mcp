# bad-mcp — T4H legacy integration surface

**Governance status: migration / compatibility surface.**

`bad-mcp` is not the canonical T4H control plane and must not become a second
public MCP gateway. New clients and capabilities route through the T4H Bridge
and the canonical remote MCP service.

## Canonical routing

```text
Client / Agent -> T4H Bridge -> canonical /mcp -> capability
```

Runner, LLM, low-risk, mid-risk, and specialist services remain behind the
Bridge. They must not receive independent public endpoints from this repository.

## Compatibility rules

- Preserve existing integrations only while they are being migrated.
- Do not add another `/mcp`, worker, runner, or LLM public ingress here.
- Treat `api/mcp_v2.py` as a legacy compatibility endpoint; new consumers must
  not be pointed at it.
- Remove legacy endpoint implementations only after dependency inventory and
  live-client migration are verified.
- No runtime logs, credentials, tokens, or transient deployment artifacts are
  committed.

## Qwen integration

This repository retains the existing Qwen Code workflow integration. Agent
output is not proof of successful execution; material changes require validation
and observable evidence.

## Source of truth

The canonical endpoint and routing rules are governed by the T4H Bridge
Constitution and endpoint registry in `bridge-constitution-troy`. This repository
implements compatibility only and must conform to those rules.
