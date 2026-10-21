class ObjectLocked(Exception):
    """Raised when an object already has a pending ChangeRequest against it."""
    def __init__(self, change_request):
        self.change_request = change_request
        super().__init__(
            f'This object has a pending change (request #{change_request.pk}) '
            f'awaiting approval — resolve it before making another change.'
        )


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
