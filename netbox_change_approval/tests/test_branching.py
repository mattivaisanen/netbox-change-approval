from contextvars import ContextVar
from unittest import mock

from django.test import TestCase

from dcim.models import Manufacturer
from users.models import User

from .. import branching, gating
from ..models import ActionChoices, ChangeRequest
from .utils import fake_request


class BranchingTests(TestCase):
    """With netbox-branching, approval covers main only: branch edits and
    branch merges/reverts/syncs aren't gated (see branching.py)."""

    def setUp(self):
        self.user = User.objects.create(username='brancher')
        self.obj = Manufacturer.objects.create(name='Acme', slug='acme')
        self.obj.description = 'changed'

    def test_main_is_gated(self):
        self.assertIsNotNone(gating.gate(self.obj, ActionChoices.UPDATE, fake_request(self.user)))

    def test_merge_replay_is_not_gated(self):
        class FakeBranch:
            def merge(inner_self):
                return gating.gate(self.obj, ActionChoices.UPDATE, fake_request(self.user))

        FakeBranch.merge = branching._mark_replay(FakeBranch.merge)
        self.assertIsNone(FakeBranch().merge())
        self.assertFalse(ChangeRequest.objects.exists())
        self.assertFalse(branching.outside_main())  # reset after the merge

    def test_edit_inside_branch_is_not_gated(self):
        active_branch = ContextVar('active_branch', default=None)
        fake_module = mock.Mock(active_branch=active_branch)
        with mock.patch.dict('sys.modules', {'netbox_branching': mock.Mock(), 'netbox_branching.contextvars': fake_module}):
            token = active_branch.set('some branch')
            try:
                self.assertIsNone(gating.gate(self.obj, ActionChoices.UPDATE, fake_request(self.user)))
            finally:
                active_branch.reset(token)
        self.assertFalse(ChangeRequest.objects.exists())

    def test_own_models_never_branch(self):
        self.assertIs(branching._own_models_stay_in_main(ChangeRequest), False)
        self.assertIsNone(branching._own_models_stay_in_main(Manufacturer))
