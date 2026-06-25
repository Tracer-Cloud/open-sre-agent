"""Per-vendor integration verifier modules.

Each module registers itself with the plugin registry (see
``app.integrations.verification``) at import time. The single
loader at ``app/integrations/_verifiers_loader.py`` imports every
module here to fire the ``@register_verifier`` decorators.

Naming: one file per service, named after the canonical service key
(``aws.py``, ``mongodb.py``, ``slack.py``, …). The folder name
already conveys "verifier", so the suffix is dropped here. The
``app/services/<vendor>/verifier.py`` modules use the same plugin
contract; they live next to their vendor client instead of here.
"""
