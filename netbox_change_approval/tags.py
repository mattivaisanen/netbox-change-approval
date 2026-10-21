"""
The one reserved Tag this plugin manages: applied to an object the moment a
pending *create* is written for real (see gating.gate() / signals.py), removed
on approval, nothing further needed on rejection (the object itself gets
deleted instead — see models.ChangeRequest.reject()).

Only CREATE uses this. UPDATE/DELETE still hold the real write until approved —
there's no "duplicate creation" risk for those, which is the specific problem
this solves (see README "Pending creates are visible immediately").
"""
PENDING_TAG_SLUG = 'pending-approval'
PENDING_TAG_NAME = 'Pending Approval'
PENDING_TAG_COLOR = 'ff9800'  # amber — distinct from the create/update/delete action colors


def get_pending_tag():
    from extras.models import Tag

    tag, _created = Tag.objects.get_or_create(
        slug=PENDING_TAG_SLUG,
        defaults={'name': PENDING_TAG_NAME, 'color': PENDING_TAG_COLOR},
    )
    return tag
