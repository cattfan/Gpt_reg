# Architecture modules

- **core/** — Protocols, exceptions, RunContext
- **phases/registry.py** — `reg_mode` → RegistrationPhase
- **mail/modes.py** — MailModeSpec registry
- **web/jobs/registry.py** — SSE job kinds

Adding a module: implement contract → register → settings keys → test `check_*_registry.py`.
