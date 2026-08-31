# bad-mcp — T4H legacy integration surface

**Governance status: legacy / compatibility surface.**

`bad-mcp` is not the canonical MCP system, not the T4H Bridge, and not the Super-Agent. It must not become a second public control plane.

## T4H architectural separation

```text
                         T4H
                          │
              ┌───────────┴───────────┐
              │                       │
         MCP SYSTEM              SUPER-AGENT
              │                       │
        own authority            own authority
        own scopes               own scopes
        own permissions          own permissions
        own capabilities         own capabilities
              │                       │
        MCP tools/services       agents/workers
              │                       │
              └───────────┬───────────┘
                          ▼
                      BOUNDARIES
```

The two systems are independent. Super-Agent authority is not inherited from MCP, and MCP authority is not inherited from the Super-Agent.

## Compatibility rules

- Preserve existing integrations only while they are being migrated or replaced.
- Do not add another public MCP control plane here.
- Do not add Bridge authority here.
- Do not add Super-Agent authority here.
- Treat `api/mcp_v2.py` as a legacy compatibility endpoint unless and until it is replaced by the canonical MCP implementation.
- Remove legacy endpoint implementations only after dependency inventory, client migration, and live verification.
- No runtime logs, credentials, tokens, or transient deployment artifacts are to be committed.

## Canonical direction

The MCP estate is being rationalised around governed capability paths. Risk and persistence are independent dimensions:

- low risk: broad ordinary capabilities
- BAU: approved high-value/frequent capability sets
- high risk: specialist capabilities and privileged access

Persistence may be one-shot, session, or permanent according to policy.

`bad-mcp` is reference material for migration and compatibility, not the destination architecture.
