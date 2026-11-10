"""One source identity for runtime evidence and exact-release feature checks.

Render's documented Git commit identifies the actual deployed source. An operator
pin may agree with it, but must never label different code as an older release.
Local/test execution without a platform commit retains its explicit fallback.
"""
import os
import re


def revision(fallback: str = '') -> str:
    actual = os.getenv('RENDER_GIT_COMMIT', '')
    configured = os.getenv('RUNTIME_REVISION', '')
    if actual:
        if not re.fullmatch(r'[a-f0-9]{40}', actual):
            raise RuntimeError('Invalid deployed source revision.')
        if configured and configured != actual:
            raise RuntimeError('Runtime revision pin differs from deployed source revision.')
        return actual
    return configured or fallback
