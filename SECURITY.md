# Security Policy

## Supported Versions

Currently, only the latest active branch (Beta/Phase 4) is supported with security updates.

| Version | Supported          |
| ------- | ------------------ |
| Beta    | :white_check_mark: |
| Older   | :x:                |

## Reporting a Vulnerability

If you discover a security vulnerability within D.A.E.M.O.N., please **do not** open a public issue. Instead, please report it privately.

**Contact:** Email `ay2949032abhishekyadav@gmail.com` with the subject `[SECURITY] D.A.E.M.O.N. Vulnerability`.

Please include the following in your report:
* A description of the vulnerability.
* Steps to reproduce the issue.
* (Optional) Any potential solutions or mitigation strategies.

We will try to acknowledge your report within 48 hours and provide updates as we work on a fix.

## Best Practices & Disclaimers

### API Keys
D.A.E.M.O.N. uses third-party APIs (Groq, Gemini, OpenAI). **Never** share your `.env` file or commit it to a public repository. The `.gitignore` is configured to ignore this file by default, but always verify before pushing.

### Local Execution Permissions
D.A.E.M.O.N. is designed to execute system-level commands and run custom Python/C-skills on your behalf. 
* **The daemon runs with your user permissions.** 
* Do not run D.A.E.M.O.N. as `root` or `Administrator` unless you fully understand the risks. 
* If using a cloud LLM, be aware of what system information you allow the LLM to access and process. For maximum privacy, switch to the `ollama` backend for full local execution.
