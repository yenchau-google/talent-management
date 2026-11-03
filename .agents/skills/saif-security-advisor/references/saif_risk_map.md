# SAIF Risk Map: Official 15 Risks -> 24 Controls + Agent Extension

Canonical Public URLs:
- Risks: https://saif.google/secure-ai-framework/risks
- Controls: https://saif.google/secure-ai-framework/controls
- SAIF Map: https://saif.google/secure-ai-framework/saif-map
- Focus on Agents: https://saif.google/secure-ai-framework/focus-on-agents

## 1. 15 SAIF Risks -> Official Controls

| Risk | Who Mitigates | Official SAIF Controls |
| :--- | :--- | :--- |
| Data Poisoning | Model Creators | Training Data Sanitization; Secure-by-Default ML Tooling; Model and Data Integrity Management; Model and Data Access Controls; Model and Data Inventory Management |
| Unauthorized Training Data | Model Creators | Training Data Sanitization; Training Data Management |
| Model Source Tampering | Model Creators | Secure-by-Default ML Tooling; Model and Data Integrity Management; Model and Data Access Controls; Model and Data Inventory Management |
| Excessive Data Handling | Creators, Consumers | User Data Management; User Transparency and Controls |
| Model Exfiltration | Creators, Consumers | Model and Data Inventory Management; Model and Data Access Controls; Secure-by-Default ML Tooling |
| Model Deployment Tampering | Creators, Consumers | Secure-by-Default ML Tooling |
| Denial of ML Service | Model Consumers | Application Access Management |
| Model Reverse Engineering | Model Consumers | Application Access Management |
| Insecure Integrated Component | Model Consumers | Agent Permissions |
| Prompt Injection | Creators, Consumers | Input Validation and Sanitization; Adversarial Training and Testing; Output Validation and Sanitization |
| Model Evasion | Creators, Consumers | Adversarial Training and Testing |
| Sensitive Data Disclosure | Creators, Consumers | Privacy Enhancing Technologies; User Data Management; Output Validation and Sanitization; Agent Permissions; Agent User Control; Agent Observability |
| Inferred Sensitive Data | Creators, Consumers | Training Data Management; Output Validation and Sanitization |
| Insecure Model Output | Model Consumers | Output Validation and Sanitization |
| Rogue Actions | Model Consumers | Agent Permissions; Agent User Control; Agent Observability; Output Validation and Sanitization |

## 2. Agent Extension Controls (`focus-on-agents`)
- **Agent User Control:** Ensure user approval for any actions performed by agents/plugins that alter user data or act on the user's behalf.
- **Agent Permissions:** Use least privilege as the upper bound on agent permissions. Minimize the tools an agent may use and the actions it may take.
- **Agent Observability:** Make an agent's actions, tool use, and reasoning transparent and auditable through structured logging.
- **Agent Surface Notes:** Guard against poisoned untrusted strings in data cells/notes, deceptive tool descriptions, and unsanitized Markdown/image URLs in rendered outputs.
