from django.contrib import messages
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from extras.validators import CustomValidator

from .exceptions import ObjectLocked
from .gating import gate
from .models import ActionChoices, StatusChoices


class ApprovalGateValidator(CustomValidator):
    """
    Registered (by discovery.wire_up_gated_models) under CUSTOM_VALIDATORS for every
    gated model. Runs on every create/update, inside instance.clean() — see
    discovery.py for exactly how and why that fires.
    """

    def validate(self, instance, request):
        action = ActionChoices.UPDATE if instance.pk else ActionChoices.CREATE

        try:
            change_request = gate(instance, action, request)
        except ObjectLocked as exc:
            self.fail(str(exc))

        if change_request is None:
            return

        # fail() always raises ValidationError, which Django's ModelForm renders
        # as a red field/form error — there's no way around that part, it's what
        # actually blocks the save. But that's a poor home for the real news, so
        # the clear, positive-toned version goes out as its own flash message;
        # the form error itself stays short and just points at it.
        if request is not None:
            if change_request.status == StatusChoices.APPLIED:
                messages.success(
                    request,
                    _('Change auto-approved by rule and applied immediately (request #%(pk)s).')
                    % {'pk': change_request.pk},
                )
            else:
                messages.success(
                    request,
                    _('Change submitted for approval. Approval sent to: %(approvers)s.')
                    % {'approvers': change_request.approver_names},
                )

        if change_request.status == StatusChoices.APPLIED:
            self.fail(_('Applied automatically as request #%(pk)s.') % {'pk': change_request.pk})
        else:
            self.fail(_('Submitted for approval as request #%(pk)s.') % {'pk': change_request.pk})
