"""
On/off switches for optional features (Change Approval → Settings).

Each one is a bool setting in plugin_settings.SETTINGS, on unless set to
False. Turning a feature off hides its buttons and pages and refuses its
actions; data it made before stays as it is:

- scheduled_approvals: approvers can't give an "Apply at" time. Requests
  already scheduled still apply at their time and can still be applied now
  or unscheduled.
- send_back: approvers can't send a request back for changes (Reject
  still works). Requests already sent back can still be revised or
  discarded, and a submitter can still retract a request to edit it.
- comments: nobody can add comments. Earlier comments stay readable.
- stand_ins: the Away page is closed and stand-ins named there stop
  deciding for anyone; approvals go to the group members again.
- notifications: no NetBox notifications (the bell) from this plugin.
  Event Rules (webhooks, scripts) still run.
- widget_drawings: the Pending Approvals widget shows only its line of text
  when nothing is waiting, without the drawing.
"""
from .plugin_settings import get_setting

SCHEDULED_APPROVALS = 'scheduled_approvals'
SEND_BACK = 'send_back'
COMMENTS = 'comments'
STAND_INS = 'stand_ins'
NOTIFICATIONS = 'notifications'
WIDGET_DRAWINGS = 'widget_drawings'

ALL = (SCHEDULED_APPROVALS, SEND_BACK, COMMENTS, STAND_INS, NOTIFICATIONS, WIDGET_DRAWINGS)


def on(key):
    """Whether feature `key` is turned on."""
    if key not in ALL:
        raise KeyError(key)
    return get_setting(key, True) is not False
