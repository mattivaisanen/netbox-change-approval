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
    min_version = '4.2.0'

    default_settings = {
        # Name of the Django Group used as the fallback approver group when no
        # ApprovalRoutingRule matches a given object. Must exist in the database.
        'default_approver_group': 'Change Approvers',
        # Dotted labels ('app_label.model_name') to exclude from gating entirely, on top
        # of this plugin's own models (which are always excluded). Useful for noisy,
        # low-stakes, or infrastructure-internal models you never want to gate.
        'exempt_models': [],
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

        from . import permissions, signals  # noqa: F401  (connects pre_delete/post_save receivers)
        from . import widgets  # noqa: F401  (runs @register_widget)
        from .discovery import wire_up_gated_models
        from .rack_styling import patch_rack_elevation_svg
        from .table_styling import patch_netbox_table

        wire_up_gated_models()
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


config = NetBoxChangeApprovalConfig
