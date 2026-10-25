from netbox.plugins import PluginConfig


class NetBoxChangeApprovalConfig(PluginConfig):
    name = 'netbox_change_approval'
    verbose_name = 'Change Approval'
    description = (
        'Require approval-group sign-off on changes to NetBox objects before they take '
        'effect, with auto-approval rules and a bypass permission for trusted users.'
    )
    version = '0.3.3'
    author = 'Matti'
    base_url = 'change-approval'
    # Only NetBox 4.7 is tested. Older releases lack hooks this plugin uses
    # (object actions, BulkEditView.pre_save_operations, delete signal origin).
    min_version = '4.7.0'

    default_settings = {
        # Approver group of the Default rule (bottom of Approval Rules) when
        # that rule leaves its group blank. Also the group the Default rule
        # starts with when it's created. Edit the Default rule instead.
        'default_approver_group': 'Change Approvers',
        # Dotted labels ('app_label.model_name') to exclude from gating entirely, on top
        # of this plugin's own models (which are always excluded). Useful for noisy,
        # low-stakes, or infrastructure-internal models you never want to gate.
        'exempt_models': [],
        # Only the starting "Expire after (days)" of the Default rule, when
        # migration 0017 creates it. Edit the Default rule instead.
        'expire_pending_after_days': None,
        # How pending objects look in lists and rack elevations: 'by_action'
        # (green new / blue edit / red delete, with a label and the requested
        # values) or 'outline' (the earlier single yellow outline).
        'pending_row_style': 'by_action',
        # Components of a pending new device/VM (interfaces, ports, IPs on
        # them, ...) belong to its request: only its submitter can change them,
        # and approving or rejecting the device covers them. False turns it off.
        'group_components_with_parent': True,
        # Field work sheet (worksheet.py): physical jobs from approved changes.
        'work_sheets': True,
        # Change sets (changesets.py): collect changes, submit them as one.
        'change_sets': True,
        # NetBox's address as users reach it (e.g. 'https://netbox.example.com'),
        # for full links in webhook data. Empty: links are paths only.
        'netbox_url': '',
    }

    middleware = [
        'netbox_change_approval.middleware.ChangeApprovalMiddleware',
        'netbox_change_approval.amend.AmendMiddleware',
        'netbox_change_approval.components.ChangeLogLabelMiddleware',
    ]

    template_extensions = 'template_content.template_extensions'
    menu = 'navigation.menu'

    def ready(self):
        super().ready()

        from . import backstop, permissions, signals  # noqa: F401  (connects pre_delete/post_save receivers)
        from . import widgets  # noqa: F401  (runs @register_widget)
        from . import expiry  # noqa: F401  (registers the hourly expiry job)
        from . import scheduling  # noqa: F401  (registers the scheduled-apply job)
        from . import notifications
        from .bulk import patch_bulk_views
        from .discovery import wire_up_gated_models
        from .rack_styling import patch_rack_elevation_svg
        from .table_styling import patch_netbox_table

        wire_up_gated_models()
        from . import object_tab
        object_tab.register()
        from netbox.utils import register_request_processor

        from .middleware import background_job_scope
        if background_job_scope not in registry_request_processors():
            register_request_processor(background_job_scope)
        notifications.connect()
        from . import worksheet
        worksheet.connect()
        from . import changesets
        changesets.connect()
        backstop.connect()
        patch_bulk_views()
        patch_netbox_table()
        patch_rack_elevation_svg()
        from . import notification_by
        notification_by.patch()

        from . import branching
        branching.connect()

        # Keeping the "Approvals" menu permission in sync with the approver
        # groups needs the database, which Django (and netbox-branching)
        # don't want touched while apps are still loading. So it runs after
        # every `migrate` (part of every install and upgrade) and once per
        # process on its first request (covers a changed
        # default_approver_group); rule changes resync it themselves
        # (signals.py).
        from django.db.models.signals import post_migrate
        post_migrate.connect(
            _sync_after_migrate, sender=self,
            dispatch_uid='netbox_change_approval:sync_after_migrate',
        )


def _sync_after_migrate(**kwargs):
    from .permissions import sync_is_approver_permission
    sync_is_approver_permission()


def registry_request_processors():
    from netbox.registry import registry
    return registry['request_processors']


config = NetBoxChangeApprovalConfig
