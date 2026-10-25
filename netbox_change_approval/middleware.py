from contextlib import contextmanager
from urllib.parse import urlparse
from uuid import uuid4

from django.contrib import messages
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect
from django.urls import Resolver404, resolve
from django.utils.http import url_has_allowed_host_and_scheme

from netbox.views.generic import BulkDeleteView, BulkEditView, BulkImportView, BulkRenameView, ObjectEditView
from utilities.api import is_api_request

from .bulk import BatchCollector, collecting
from .context import batch_scope
from .discovery import is_gated_model
from .exceptions import ChangeApprovalRequired

BULK_VIEW_CLASSES = (BulkEditView, BulkDeleteView, BulkImportView, BulkRenameView)


def bulk_view_mode(view_class):
    """The BatchCollector mode for a bulk edit/import/delete view, else None."""
    if not view_class or not issubclass(view_class, BULK_VIEW_CLASSES):
        return None
    return BatchCollector.HOLD if issubclass(view_class, BulkImportView) else BatchCollector.RAISE


@contextmanager
def background_job_scope(request):
    """
    NetBox request processor (registered in __init__.py). NetBox can run a bulk
    edit/import/delete, or a REST API bulk write, as a background job. The
    worker calls the view directly, without any middleware, so
    ChangeApprovalMiddleware never set up the per-object handling for it: the
    first object needing approval failed the whole job, and nothing was
    queued. Request processors do run in the worker, so this sets up the same
    handling there. The job log gets the summary the web UI would have shown.

    In a normal web request this runs too, around the middleware; the
    middleware's own handling then takes over, so this only acts for jobs
    and API bulk writes.
    """
    match = ChangeApprovalMiddleware._resolve_match(request)
    if ChangeApprovalMiddleware._is_api_bulk_write(request, match):
        with collecting(BatchCollector.REFUSE):
            yield
        return
    job = getattr(request, 'job', None)
    mode = bulk_view_mode(getattr(match.func, 'view_class', None) if match else None)
    if job is None or mode is None:
        yield
        return
    with batch_scope(uuid4()), collecting(mode) as collector:
        yield
    _log_batch(job, collector)


def _log_batch(job, collector):
    from .models import ChangeRequest

    logger = getattr(job, 'logger', None)
    if logger is None:
        return
    queued = list(ChangeRequest.objects.filter(pk__in=[cr.pk for cr in collector.queued]).order_by('pk'))
    if queued:
        logger.info(
            f'{len(queued)} change(s) submitted for approval instead of being saved '
            f'(requests {", ".join(f"#{cr.pk}" for cr in queued)}).'
        )
    if collector.locked:
        logger.warning(
            f'{len(collector.locked)} object(s) skipped: they already have a pending change '
            f'(requests {", ".join(f"#{cr.pk}" for cr in collector.locked)}).'
        )


class ChangeApprovalMiddleware:
    """
    Four jobs:

    1. Detect bulk edit/import/delete requests up front (by resolving the URL
       ourselves — resolver_match isn't populated this early in the normal
       middleware chain) and open a shared batch_id for the duration of the
       request, so every object touched by that one bulk action is grouped under
       one ChangeRequest.batch_id instead of each creating an unrelated row.

    2. On a GET to a gated model's *add* view with no return_url already set,
       redirect once to the same URL with `?return_url=<wherever the user came
       from>` appended — see _redirect_add_with_return_url for why.

    3. After a gated create, drop the view's own generic "Created <Model> <obj>"
       flash message, leaving just signals.py's more informative one — see
       _drop_duplicate_create_message for why both exist in the first place.

    4. Catch ChangeApprovalRequired, raised from the pre_delete gate (signals.py)
       to abort a delete that needs approval. Unlike the create/update path (a
       plain ValidationError, natively caught and displayed by Django's ModelForm),
       a delete has no form-clean cycle to catch it, so this is the backstop.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    #: Whether this process has synced the approver menu permission yet
    #: (see __init__.py: not done while apps load).
    _approver_permission_synced = False

    def _sync_approver_permission_once(self):
        if ChangeApprovalMiddleware._approver_permission_synced:
            return
        ChangeApprovalMiddleware._approver_permission_synced = True
        from .permissions import sync_is_approver_permission
        try:
            sync_is_approver_permission()
        except Exception:
            import logging
            logging.getLogger('netbox_change_approval').exception('Could not sync the approver permission')

    def __call__(self, request):
        self._sync_approver_permission_once()
        match = self._resolve_match(request)
        view_class = getattr(match.func, 'view_class', None) if match else None

        if self._is_api_bulk_write(request, match):
            # See BatchCollector.REFUSE.
            with collecting(BatchCollector.REFUSE):
                return self.get_response(request)

        if (mode := bulk_view_mode(view_class)) is not None:
            with batch_scope(uuid4()), collecting(mode) as collector:
                response = self.get_response(request)
            self._report_batch(request, collector)
            return response

        if view_class and issubclass(view_class, ObjectEditView):
            redirected = self._redirect_add_with_return_url(request, view_class, match)
            if redirected is not None:
                return redirected

        response = self.get_response(request)
        self._drop_duplicate_create_message(request)
        return response

    #: Substrings only *our own* signals.finish_pending_create messages contain —
    #: matching against our own wording (which we control) rather than NetBox's
    #: generic one (which we don't) is what makes _drop_duplicate_create_message
    #: below robust to NetBox changing its own message text.
    _OWN_CREATE_MESSAGE_MARKERS = ('awaiting approval', 'auto-approved by rule')

    @classmethod
    def _drop_duplicate_create_message(cls, request):
        """
        For a gated create, signals.finish_pending_create's own messages.success()
        call (fired from inside form.save(), via the post_save signal) says what
        was created *and* who needs to approve it — strictly more useful than
        NetBox's own generic `messages.success(request, "Created <Model> <obj>")`
        a few lines later in ObjectEditView.form_valid(), which then shows up
        right alongside it. signals.py sets `request._nbca_suppress_default_
        create_message` precisely so this can drop that trailing duplicate.

        Rather than guess at queue position (Django's message storage merges
        freshly-queued messages with ones already loaded from a previous
        request's cookie/session in ways that aren't obvious from the `.add()`
        call site — a first version of this tried popping the storage's internal
        `_queued_messages` list and it silently dropped the wrong one), this
        reads every message back out through the public `messages.get_messages()`
        API, keeps only the ones carrying our own markers, and re-queues just
        those — relying on BaseStorage.update()'s documented behavior that once a
        storage has been iterated (`self.used = True`, which get_messages()
        triggers), only messages added *after* that point get persisted.
        """
        if not getattr(request, '_nbca_suppress_default_create_message', False):
            return
        if getattr(request, '_messages', None) is None:
            return
        own_messages = [
            m for m in messages.get_messages(request)
            if any(marker in str(m.message) for marker in cls._OWN_CREATE_MESSAGE_MARKERS)
        ]
        for m in own_messages:
            messages.add_message(request, m.level, m.message, extra_tags=m.extra_tags)

    @staticmethod
    def _report_batch(request, collector):
        """
        One summary for a bulk action that skipped objects needing approval
        (see bulk.py). NetBox's own "Updated/Deleted/Imported N objects" message
        counts the queued objects as done, so it is replaced. When objects were
        only skipped as locked, NetBox's message stays and a warning is added.
        """
        from .models import ChangeRequest

        if not collector.queued and not collector.locked:
            return
        if getattr(request, '_messages', None) is None:
            return

        queued = list(
            ChangeRequest.objects.filter(pk__in=[cr.pk for cr in collector.queued]).order_by('pk')
        )
        if collector.queued and not queued:
            return  # the bulk action failed and rolled back; NetBox already showed why

        if queued:
            kept = [m for m in messages.get_messages(request) if m.level != messages.SUCCESS]
            for m in kept:
                messages.add_message(request, m.level, m.message, extra_tags=m.extra_tags)

            approvers = ', '.join(dict.fromkeys(str(cr.approver_names) for cr in queued))
            ids = ', '.join(f'#{cr.pk}' for cr in queued)
            messages.success(
                request,
                f'{len(queued)} change(s) submitted for approval (requests {ids}). '
                f'Approval sent to: {approvers}.',
            )
            if collector.applied:
                messages.info(
                    request,
                    f'{collector.applied} other object(s) needed no approval and were saved.',
                )
        if collector.locked:
            ids = ', '.join(f'#{cr.pk}' for cr in collector.locked)
            messages.warning(
                request,
                f'{len(collector.locked)} object(s) skipped: they already have a pending change '
                f'(requests {ids}). Resolve those first.',
            )

    @staticmethod
    def _is_api_bulk_write(request, match):
        """A REST API update or delete sent to a list endpoint (many objects)."""
        if match is None or request.method not in ('PUT', 'PATCH', 'DELETE'):
            return False
        if 'pk' in match.kwargs:
            return False
        try:
            return is_api_request(request)
        except Exception:  # no API URLs configured
            return False

    @staticmethod
    def _resolve_match(request):
        path = getattr(request, 'path_info', None) or getattr(request, 'path', None)
        if not path:
            return None
        try:
            return resolve(path)
        except Resolver404:
            return None

    @staticmethod
    def _redirect_add_with_return_url(request, view_class, match):
        """
        NetBox's generic ObjectEditView redirects to the new object's own detail
        page on success unless the form carried a `return_url` — fine for a normal
        create, but for a *gated* one, landing back on a list is what lets someone
        add several objects in a row without each one bouncing them off to a
        single-object page (which also tempts re-clicking "Add" if it's not
        obvious the first one already went through — see docs/usage.md "As an
        editor"). Rather than hardcode per-model redirect targets,
        reuse whatever page the user actually came from: on the initial GET to the
        add form (not a POST, and only when the URL doesn't already carry a
        return_url — e.g. from a "Clone" link), redirect once to the same URL with
        `?return_url=<Referer>` appended. NetBox's own form/view honors that
        return_url for both the Cancel button and the post-save redirect — no
        further plugin code needed once it's there.
        """
        if request.method != 'GET' or 'return_url' in request.GET:
            return None
        if match.kwargs.get('pk'):
            return None  # this is an edit view, not an add view
        queryset = getattr(view_class, 'queryset', None)
        model = queryset.model if queryset is not None else None
        if model is None or not is_gated_model(model):
            return None
        referer = request.META.get('HTTP_REFERER')
        if not referer or not url_has_allowed_host_and_scheme(
            referer, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
        ):
            return None
        # NetBox's own return_url handling (utilities.views.get_return_url(),
        # via utilities.request.safe_for_redirect()) calls
        # url_has_allowed_host_and_scheme(url, allowed_hosts=None) — which,
        # unlike our same-origin check above, rejects *any* absolute URL
        # outright (allowed_hosts=None means "no host is ever acceptable"),
        # relative-only by design. Strip the scheme/host before handing it off,
        # or NetBox would silently discard it and fall back to the created
        # object's own page — exactly the redirect this is meant to avoid.
        parsed = urlparse(referer)
        relative_referer = parsed.path + (f'?{parsed.query}' if parsed.query else '')
        params = request.GET.copy()
        params['return_url'] = relative_referer
        return HttpResponseRedirect(f'{request.path}?{params.urlencode()}')

    def process_exception(self, request, exception):
        if not isinstance(exception, ChangeApprovalRequired):
            return None

        change_request = exception.change_request

        # The transaction this delete was running in (NetBox's delete collector —
        # see netbox/models/deletion.py) has already rolled back by the time an
        # exception reaches a middleware's process_exception, which is exactly why
        # the save is deferred to here rather than done in the signal handler that
        # raised this (see gating.gate() / signals.gate_delete).
        if change_request.pk is None:
            change_request.save()

        if is_api_request(request):
            return self._api_response(request, change_request, exception.locked)

        if exception.locked:
            messages.error(
                request,
                f'This object has a pending change (request #{change_request.pk}) '
                f'awaiting approval — resolve it before deleting it.',
            )
        elif getattr(change_request, '_nbca_amended', False):
            # The submitter updated their own pending request (amend.py).
            messages.success(
                request,
                f'Request #{change_request.pk} updated (revision {change_request.revision}) '
                f'and sent to the approvers again.',
            )
        else:
            # Auto-approved deletes never reach here — gate() lets those proceed
            # normally instead of raising (see its docstring) — so this is always
            # a genuinely new pending request.
            messages.success(
                request,
                f'Change submitted for approval. Approval sent to: {change_request.approver_names}.',
            )

        # Redirect to the object itself (still intact — the delete was blocked),
        # not the ChangeRequest's own page: the user attempting this delete
        # already has view permission on the object (that's how they got here),
        # but very likely does *not* have view permission on this plugin's
        # ChangeRequest model unless an admin granted it separately — redirecting
        # there would trade a friendly message for a 403. The object's own detail
        # page also now shows the pending-change banner, which is more useful
        # anyway.
        model = change_request.content_type.model_class()
        try:
            obj = model.objects.get(pk=change_request.object_id)
            return redirect(obj.get_absolute_url())
        except model.DoesNotExist:
            return redirect(change_request.get_absolute_url())

    @staticmethod
    def _api_response(request, change_request, locked):
        """
        REST API clients get JSON, not a redirect to an HTML page: 202 Accepted
        when the change was submitted for approval (nothing changed yet), 409
        Conflict when the object already has a pending change.
        """
        body = {
            'detail': (
                f'This object has a pending change (request #{change_request.pk}) awaiting '
                f'approval. Resolve it before making another change.'
                if locked else
                f'Change submitted for approval as request #{change_request.pk}. '
                f'Nothing is changed until it is approved.'
            ),
            'change_request': {
                'id': change_request.pk,
                'action': change_request.action,
                'status': change_request.status,
                'object_id': change_request.object_id,
                'display_url': request.build_absolute_uri(change_request.get_absolute_url()),
                'approver_group': (
                    change_request.approver_group.name if change_request.approver_group_id else None
                ),
            },
        }
        return JsonResponse(body, status=409 if locked else 202)
