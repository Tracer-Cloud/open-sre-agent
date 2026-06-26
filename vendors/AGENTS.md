## Vendor Package Boundary

`vendors/` is a legacy compatibility area for third-party code that has not yet
been migrated to the canonical package split.

- Do not add new agent-callable tools here. New `@tool(...)` functions and
  `BaseTool` subclasses belong under `tools/`.
- Do not add new reusable external API clients here. New clients belong under
  `services/<vendor>/client.py`.
- Keep integration config, normalization, storage, and verification wiring under
  `integrations/`.
- When touching an existing vendor-hosted tool, prefer migrating the tool surface
  to `tools/` and the reusable client to `services/` in the same change. Do not
  leave compatibility-only forwarding modules behind after imports and tests are
  migrated.

