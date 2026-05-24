# Auth Setup — Three-Vendor Panel

## OAuth CLI (default, $0 per call)

```bash
claude login          # Auditor → Claude Max subscription
codex login           # Engineer → ChatGPT/Codex subscription
gemini login          # Adversary → Google account (free tier, ~60 RPM)
```

## API key fallback

If a CLI isn't installed, set the matching env var in `.env`:

```
ANTHROPIC_API_KEY=sk-ant-...   # Auditor fallback
OPENAI_API_KEY=sk-...          # Engineer fallback
GOOGLE_API_KEY=...             # Adversary fallback
OPENROUTER_API_KEY=sk-or-...   # Last-resort escape hatch (any persona)
```

## Verify

```bash
python3 -m aec.agent.panel --snapshot fixture.json --control CC6.1
```

The panel auto-detects available transports and logs which one each persona used.
