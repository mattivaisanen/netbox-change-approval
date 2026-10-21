from netbox.plugins import PluginMenu, PluginMenuItem

#: A top-level `menu` (singular) gets its own nav tab, like "Organization" or
#: "IPAM" — the flat `menu_items` tuple most plugins export instead gets
#: nested under the generic "Plugins" dropdown. See netbox.plugins.PluginMenu
#: and NetBox core's PluginConfig._load_resource('menu') vs ('menu_items').
#:
#: Per-item visibility (utilities/templatetags/navigation.py:nav()) only
#: supports two *static* checks, decided once at process startup from the
#: object below — not per-request: `permissions` (a list of Django permission
#: strings, checked via user.has_perms(), which passes automatically for
#: superusers) and `staff_only` (despite the name, actually checks
#: user.is_superuser in this NetBox version, not is_staff). There's no "NOT"
#: version of `permissions` — hiding "My Requests" specifically *from*
#: approvers can't be expressed this way, so it stays visible to every
#: authenticated user (including approvers, who may also submit their own
#: changes and want to track them). "Approvals" *can* be gated properly,
#: just not directly off group membership: permissions.
#: sync_is_approver_permission() keeps a real ObjectPermission's groups in
#: sync with whoever is currently an approver (every ApprovalRoutingRule.
#: approver_group, plus the default_approver_group), triggered automatically
#: whenever a routing rule changes — see its docstring and DEVELOPER.md "The
#: top-level nav tab, and why the ticker moved to the Approvals page".
menu = PluginMenu(
    label='Change Approvals',
    icon_class='mdi mdi-check-decagram-outline',
    groups=(
        ('Change Approval', (
            PluginMenuItem(
                link='plugins:netbox_change_approval:changerequest_approvals',
                link_text='Approvals',
                permissions=['netbox_change_approval.is_approver_changerequest'],
            ),
            PluginMenuItem(
                link='plugins:netbox_change_approval:changerequest_mine',
                link_text='My Requests',
            ),
            PluginMenuItem(
                link='plugins:netbox_change_approval:changerequest_list',
                link_text='All Change Requests',
            ),
            PluginMenuItem(
                link='plugins:netbox_change_approval:approvalrule_list',
                link_text='Approval Rules',
                permissions=['netbox_change_approval.view_approvalrule'],
            ),
        )),
    ),
)
