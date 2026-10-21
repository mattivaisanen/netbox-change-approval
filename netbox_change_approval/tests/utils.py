import uuid

from django.contrib.messages.storage.fallback import FallbackStorage

from core.models import ObjectType
from users.models import ObjectPermission
from utilities.request import NetBoxFakeRequest


def fake_request(user):
    # NetBox's own change-logging signal (core.signals.handle_deleted_object) also
    # fires on pre_delete and requires a non-null request id to log an ObjectChange —
    # give it a real-looking one, same as an actual request would have.
    request = NetBoxFakeRequest({'user': user, 'id': uuid.uuid4(), 'session': {}, 'COOKIES': {}})
    # ApprovalGateValidator posts a messages.success() flash alongside the form
    # error — the messages framework needs real storage attached, which only
    # happens via middleware on an actual HttpRequest.
    request._messages = FallbackStorage(request)
    return request


def grant_bypass(user, model, constraints=None):
    """Grant `user` the bypass_approval custom action for `model`, exactly as an
    operator would via the NetBox Permissions UI."""
    perm = ObjectPermission.objects.create(
        name=f'test-bypass-{model._meta.model_name}',
        enabled=True,
        actions=['bypass_approval'],
        constraints=constraints,
    )
    perm.object_types.set([ObjectType.objects.get_for_model(model)])
    perm.users.set([user])
    return perm
