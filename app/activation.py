"""Default-OFF release gates. Environment flags are never a public activation API."""
import os
import re
from . import db


def revision():
    from .runtime_revision import revision as source_revision
    return source_revision()


def enabled(feature,brand=None):
    if brand is not None and not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',brand):
        return False
    prefix = 'KPS_' + feature.upper() + ('_'+brand.upper().replace('-','__') if brand is not None else '')
    if os.getenv(prefix + '_ENABLED') != 'true':
        return False
    sha = revision()
    if (not re.fullmatch(r'[a-f0-9]{40}', sha)
            or os.getenv(prefix + '_ACCEPTANCE_SHA') != sha
            or os.getenv(prefix + '_OWNER_APPROVAL_SHA') != sha):
        raise db.error(503, 'FEATURE_RELEASE_NOT_APPROVED',
                       'هذه الميزة بانتظار اعتماد الإصدار. / This feature awaits exact-release approval.')
    return True


def require(feature,brand=None):
    if not enabled(feature,brand):
        raise db.error(409, 'FEATURE_DISABLED', 'الميزة غير مفعلة. / Feature is disabled.')
