from django.contrib import messages
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from extras.validators import CustomValidator

from .bulk import BatchCollector
from .context import batch_collector, current_batch
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
        collector = batch_collector.get()

        try:
            change_request = gate(instance, action, request)
        except ObjectLocked as exc:
            if collector is None:
                if _is_api(request):
                    # The middleware answers 409 Conflict with the blocking request.
                    raise ChangeApprovalRequired(exc.change_request, locked=True)
                self.fail(str(exc))
            self._defer(instance, collector, ChangeApprovalRequired(exc.change_request, locked=True))
            return

        if change_request is None:
            auto_applied = getattr(instance, '_nbca_auto_applied', None)
            if auto_applied is not None and request is not None:
                del instance._nbca_auto_applied
                messages.info(
                    request,
                    _('Auto-approved by rule %(rule)s (request #%(pk)s).') % {
                        'rule': auto_applied.approving_rule, 'pk': auto_applied.pk,
                    },
                    fail_silently=True,
                )
            return

        # Bulk edit/import: skip this object and keep going; bulk.py records the
        # request and the middleware shows one summary message at the end.
        if collector is not None:
            self._defer(instance, collector, ChangeApprovalRequired(change_request))
            return

        # A pending update must block the save. In a normal single-object edit,
        # raise ChangeApprovalRequired instead of a ValidationError: it escapes
        # the form, and ChangeApprovalMiddleware.process_exception() turns it
        # into a redirect to the (unchanged) object with one green message, the
        # same way a gated delete is handled. A ValidationError would re-render
        # the form with a red error next to the green message.
        #
        # Fallback for a batch with no collector open (not normally reached:
        # the middleware opens both together): a plain form error.
        if request is not None and current_batch.get() is None:
            raise ChangeApprovalRequired(change_request)

        if request is not None:
            messages.success(
                request,
                _('Change submitted for approval. Approval sent to: %(approvers)s.')
                % {'approvers': change_request.approver_names},
                fail_silently=True,
            )
        self.fail(_('Submitted for approval as request #%(pk)s.') % {'pk': change_request.pk})

    @staticmethod
    def _defer(instance, collector, exc):
        """Bulk edit raises so bulk.py can roll back this object's savepoint.
        Bulk import can't be wrapped per record before validation, so the
        exception is parked on the instance and BulkImportView._save_object()
        skips the save instead (see bulk.py). A REST API bulk update fails the
        object with a validation error (see BatchCollector.REFUSE)."""
        if collector.mode == BatchCollector.REFUSE:
            raise ValidationError(refusal_message(exc))
        if collector.mode == BatchCollector.HOLD:
            instance._nbca_held = exc
            return
        raise exc


def _is_api(request):
    from utilities.api import is_api_request

    try:
        return request is not None and is_api_request(request)
    except Exception:
        return False


def refusal_message(exc):
    """Per-object error for a REST API bulk update/delete (BatchCollector.REFUSE)."""
    if exc.locked:
        return str(ObjectLocked(exc.change_request))
    return _(
        'This change needs approval. Bulk API requests are all-or-nothing, so it '
        'was not submitted: send it on its own (to this object\'s own URL) to '
        'submit it for approval.'
    )
