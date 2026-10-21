from django.contrib import messages
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from extras.validators import CustomValidator

from .context import current_batch
from .exceptions import ChangeApprovalRequired, ObjectLocked
from .gating import gate
from .models import ActionChoices


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
            auto_applied = getattr(instance, '_nbca_auto_applied', None)
            if auto_applied is not None and request is not None:
                del instance._nbca_auto_applied
                messages.info(
                    request,
                    _('Auto-approved by rule (request #%(pk)s).') % {'pk': auto_applied.pk},
                )
            return

        # A pending update must block the save. In a normal single-object edit,
        # raise ChangeApprovalRequired instead of a ValidationError: it escapes
        # the form, and ChangeApprovalMiddleware.process_exception() turns it
        # into a redirect to the (unchanged) object with one green message, the
        # same way a gated delete is handled. A ValidationError would re-render
        # the form with a red error next to the green message.
        #
        # Bulk edit/import still use ValidationError: those views run inside
        # their own transaction and handle ValidationError per object, and an
        # escaping exception would roll back the saved ChangeRequest.
        if request is not None and current_batch.get() is None:
            raise ChangeApprovalRequired(change_request)

        if request is not None:
            messages.success(
                request,
                _('Change submitted for approval. Approval sent to: %(approvers)s.')
                % {'approvers': change_request.approver_names},
            )
        self.fail(_('Submitted for approval as request #%(pk)s.') % {'pk': change_request.pk})
