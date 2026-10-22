from netbox.plugins import PluginConfig


class NetBoxChangeApprovalConfig(PluginConfig):
    name = 'netbox_change_approval'
    verbose_name = 'Change Approval'
    description = (
        'Require approval-group sign-off on changes to NetBox objects before they take '
        'effect, with auto-approval rules and a bypass permission for trusted users.'
    )
    version = '0.1.0'
    author = 'Matti'
    base_url = 'change-approval'
    # Only NetBox 4.7 is tested. Older releases lack hooks this plugin uses
    # (object actions, BulkEditView.pre_save_operations, delete signal origin).
    min_version = '4.7.0'

    default_settings = {
        # Name of the Django Group used as the fallback approver group when no
        # ApprovalRoutingRule matches a given object. Must exist in the database.
        'default_approver_group': 'Change Approvers',
        # Dotted labels ('app_label.model_name') to exclude from gating entirely, on top
        # of this plugin's own models (which are always excluded). Useful for noisy,
        # low-stakes, or infrastructure-internal models you never want to gate.
        'exempt_models': [],
        # Days after which a pending request nobody decided expires, for rules
        # that don't set their own. None (or 0) means never.
        'expire_pending_after_days': None,
        # How pending objects look in lists and rack elevations: 'by_action'
        # (green new / blue edit / red delete, with a label and the requested
        # values) or 'outline' (the earlier single yellow outline).
        'pending_row_style': 'by_action',
    }

    middleware = [
        'netbox_change_approval.middleware.ChangeApprovalMiddleware',
    ]

    template_extensions = 'template_content.template_extensions'
    menu = 'navigation.menu'

    def ready(self):
        super().ready()

        # Local import: must happen after Django's app registry is populated, which is
        # only guaranteed once ready() runs.
        from django.db.utils import OperationalError, ProgrammingError

        from . import backstop, permissions, signals  # noqa: F401  (connects pre_delete/post_save receivers)
        from . import widgets  # noqa: F401  (runs @register_widget)
        from . import expiry  # noqa: F401  (registers the hourly expiry job)
        from . import notifications
        from .bulk import patch_bulk_views
        from .discovery import wire_up_gated_models
        from .rack_styling import patch_rack_elevation_svg
        from .table_styling import patch_netbox_table

        wire_up_gated_models()
        from netbox.utils import register_request_processor

        from .middleware import background_job_scope
        if background_job_scope not in registry_request_processors():
            register_request_processor(background_job_scope)
        notifications.connect()
        backstop.connect()
        patch_bulk_views()
        patch_netbox_table()
        patch_rack_elevation_svg()

        try:
            # Covers process start and default_approver_group config changes, which
            # don't go through ApprovalRoutingRule at all (signals.py's post_save/
            # post_delete receivers handle every *other* trigger for this). Swallow
            # DB errors here specifically because this can run before the first
            # `migrate` has ever created this plugin's tables.
            permissions.sync_is_approver_permission()
        except (OperationalError, ProgrammingError):
            pass


def registry_request_processors():
    from netbox.registry import registry
    return registry['request_processors']


config = NetBoxChangeApprovalConfig
