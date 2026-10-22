class ObjectLocked(Exception):
    """Raised when an object already has a pending ChangeRequest against it."""
    def __init__(self, change_request, message=None):
        self.change_request = change_request
        super().__init__(message or (
            f'This object has a pending change (request #{change_request.pk}) '
            f'awaiting approval — resolve it before making another change.'
        ))


class ChangeConflict(Exception):
    """
    Raised when applying an approved UPDATE would overwrite something that
    changed after the request was submitted (via the API, a script, a bypass
    user, ...), or when the target object no longer exists. Nothing is written;
    the request stays pending so an approver can reject it and the submitter
    can resubmit against the current state.

    `fields` is a list of field names whose current value no longer matches the
    request's recorded "old" value (empty when the object itself is gone).
    """
    def __init__(self, object_repr, fields, missing=False):
        self.object_repr = object_repr
        self.fields = list(fields)
        self.missing = missing
        if missing:
            message = f'{object_repr} no longer exists.'
        else:
            message = (
                f'{object_repr} was changed after this request was submitted '
                f'(fields: {", ".join(self.fields)}). Approving would overwrite those newer edits.'
            )
        super().__init__(message)


class ChangeApprovalRequired(Exception):
    """
    Raised from the pre_delete gate to abort a delete. Unlike the create/update path
    (which raises django.core.exceptions.ValidationError, natively caught by
    ModelForm), deletes in NetBox's generic ObjectDeleteView/BulkDeleteView call
    instance.delete() directly, outside of any form-clean cycle — so this is caught
    by ChangeApprovalMiddleware.process_exception() instead, which turns it into a
    redirect + a message.

    `change_request` may be *unsaved* (the new-pending-request case — see
    gating.gate()'s docstring for why it must not be saved before this exception
    is raised). The middleware saves it, once the transaction this delete was
    running in has finished rolling back.

    `locked=True` means `change_request` is instead a *pre-existing*, already-saved
    request blocking this one (ObjectLocked) — there's nothing new to save, just a
    different message to show.
    """
    def __init__(self, change_request, locked=False):
        self.change_request = change_request
        self.locked = locked
        super().__init__('A change approval decision is required before this delete can proceed.')
