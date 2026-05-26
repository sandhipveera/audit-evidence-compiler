# Task 020 — Foundation-Sec-8B as 4th panel persona ("The Security Model")

**Goal:** Add Cisco/Splunk's Foundation-Sec-8B-Instruct as a fourth debate voice alongside Claude, GPT, and Gemini. Wins the hackathon's $1K "Best Hosted Models Use" bonus prize. Narrative: three commercial AI vendors debate compliance evidence; Splunk's own open-source security model is the fourth voice.

**Budget:** ~150 LOC + tests. ~2 days.

## Why this is the move

The hackathon offers a $1K bonus for "best Hosted Models use." Foundation-Sec-8B is:
- Developed by Cisco Foundation AI, released and promoted by Splunk
- Available open-weight on HuggingFace: `fdtn-ai/Foundation-Sec-8B-Instruct`
- Callable via HuggingFace Inference API (Featherless.ai backend — no GPU, serverless)
- Specialized for cybersecurity tasks — legitimately better than generic LLMs for threat reasoning

Narrative for submission: "Three commercial AI vendors (Claude, GPT, Gemini) debate the evidence. Splunk's own Foundation AI Security Model is the fourth voice — the one trained specifically on security data."

## Persona: security_model

Role: **The Security Model** — approaches compliance through a threat-intel lens. Where the Auditor reads control language literally and the Adversary tries to break the PASS verdict, the Security Model asks: "Does this control actually reduce real-world attacker success rate?"

Persona file `src/aec/agent/personas/security_model.md`:
```yaml
---
name: security_model
display_name: "Security Model (Foundation-Sec-8B)"
role: security_intelligence
transports:
  - foundation-sec-api    # HuggingFace Inference API via Featherless.ai
  - foundation-sec-local  # ollama fallback (http://localhost:11434)
model: fdtn-ai/Foundation-Sec-8B-Instruct
---
```

System prompt: "You are a security-specialized AI model trained on cybersecurity data. Evaluate compliance evidence through a threat-intelligence lens. Ask: does this control actually stop real attackers? Focus on TTPs (MITRE ATT&CK), attacker pivot points, and whether the evidence shows *detection* of real threats vs. checkbox compliance. Be concise and specific. Output structured JSON."

## Transport: foundation_sec_api.py

```python
# src/aec/agent/transports/foundation_sec_api.py
from huggingface_hub import InferenceClient

class FoundationSecApiTransport:
    name = "foundation-sec-api"
    
    def __init__(self):
        self.model = "fdtn-ai/Foundation-Sec-8B-Instruct"
        self.client = InferenceClient(
            provider="featherless-ai",
            api_key=os.environ["HF_TOKEN"],
        )
    
    async def complete(self, messages: list[dict]) -> str:
        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=1024,
                temperature=0.3,
            )
        )
        return response.choices[0].message.content
```

## Transport: foundation_sec_local.py (ollama fallback)

```python
# For local inference: ollama pull hf.co/roadus/Foundation-Sec-8B-Q4_K_M-GGUF
# Then: http://localhost:11434/v1/chat/completions (OpenAI-compatible)
class FoundationSecLocalTransport:
    name = "foundation-sec-local"
    base_url = "http://localhost:11434/v1"
    model = "hf.co/roadus/Foundation-Sec-8B-Q4_K_M-GGUF:Q4_K_M"
```

## Panel changes

`run_panel()` now runs 4 personas in parallel: auditor, engineer, adversary, security_model.

Consensus: still lowest-verdict-wins. With 4 voters, ties favor the more conservative verdict (FAIL > INSUFFICIENT > PARTIAL > PASS).

## Files to create / modify

- `src/aec/agent/personas/security_model.md`
- `src/aec/agent/transports/foundation_sec_api.py` (~60 LOC)
- `src/aec/agent/transports/foundation_sec_local.py` (~40 LOC)
- `src/aec/agent/panel.py` — add 4th persona, update consensus for 4 voters
- `pyproject.toml` — add `huggingface-hub>=0.23.0` to optional deps
- `tests/test_foundation_sec.py` (~50 LOC) — transport mock + 4-persona panel test
- `docs/submission-text.md` — update to mention Foundation-Sec-8B + bonus prize claim

## Env var

`HF_TOKEN` — HuggingFace token with Inference API access. Free tier supports Foundation-Sec-8B via Featherless.ai.

## Definition of done

- `aec_demo --sample soc2-cc61` shows 4 personas including "Security Model (Foundation-Sec-8B)"
- `audit_trail.jsonl` shows `transport: foundation-sec-api` for the 4th persona
- `HF_TOKEN` missing → graceful degradation to 3-persona panel (not a crash)
- Tests pass

## Demo cue

```
Panel debate (4 personas, parallel):
  Auditor        (Claude Sonnet 4)          → FAIL   [28.2s]
  Engineer       (GPT-5.5)                  → FAIL   [31.4s]  
  Adversary      (Gemini 2.5 Pro)           → FAIL   [26.8s]
  Security Model (Foundation-Sec-8B)        → FAIL   [4.1s]  ← Splunk's own model

Consensus: FAIL (4/4 unanimous — privileged accounts bypassing MFA entirely)
```

Voice-over: "And the fourth voice — Splunk's own Foundation AI Security Model. Trained on cybersecurity data. Four models. One verdict."
