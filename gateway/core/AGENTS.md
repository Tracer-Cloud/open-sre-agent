# gateway/core/ — process and leaf infrastructure

Gateway core machinery used by every surface. **Must not** import
`gateway.transports.*` or `gateway.web` (surfaces). Sole exception:
`runtime/manager.py`, the composition root, which wires the transports and
the web surface together. Pinned by `gateway/tests/test_package_borders.py`.

| Package | Role |
|---------|------|
| `runtime/` | Composition root (`manager`), turn handler, approvals, attention, daemon |
| `storage/` | Session bindings + investigation stores |
| `billing/` | Credits client |
| `attachments/` | Attachment helpers |
| `session/` | Gateway chat-context helpers |
| `config/` | Logging / gateway config helpers |

Transports and `web/` may import these packages. Peer chat packages never land
here.
