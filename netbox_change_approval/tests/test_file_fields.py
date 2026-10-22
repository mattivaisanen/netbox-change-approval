import shutil
import tempfile

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from dcim.models import DeviceType, Manufacturer
from netbox.context import current_request
from users.models import Group, User

from .. import gating
from ..models import ActionChoices
from .utils import fake_request

# A 1x1 PNG.
PNG = bytes.fromhex(
    '89504e470d0a1a0a0000000d4948445200000001000000010806000000'
    '1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082'
)


class HeldFileUploadTests(TestCase):
    """A file or image uploaded in an edit that waits for approval is stored
    right away, so approving the edit links the object to a real file."""

    @classmethod
    def setUpTestData(cls):
        cls.approvers = Group.objects.create(name='Change Approvers')
        cls.approver = User.objects.create(username='approver')
        cls.approver.groups.add(cls.approvers)
        cls.alice = User.objects.create(username='alice')
        manufacturer = Manufacturer.objects.create(name='M', slug='m')
        cls.device_type = DeviceType.objects.create(manufacturer=manufacturer, model='Switch', slug='switch')

    def setUp(self):
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=media)
        override.enable()
        self.addCleanup(override.disable)

    def _hold_image_upload(self):
        request = fake_request(self.alice)
        token = current_request.set(request)
        try:
            device_type = DeviceType.objects.get(pk=self.device_type.pk)
            device_type.front_image = SimpleUploadedFile('front.png', PNG, content_type='image/png')
            change_request = gating.gate(device_type, ActionChoices.UPDATE, request)
        finally:
            current_request.reset(token)
        self.assertIsNotNone(change_request)
        return change_request, change_request.diff['front_image']['new']

    def test_rejected_withdrawn_or_expired_edit_deletes_its_file(self):
        for close in (
            lambda cr: cr.reject(self.approver, 'no'),
            lambda cr: cr.retract(self.alice),
            lambda cr: cr.expire(),
        ):
            change_request, stored_name = self._hold_image_upload()
            self.assertTrue(default_storage.exists(stored_name))
            with self.captureOnCommitCallbacks(execute=True):
                close(change_request)
            self.assertFalse(default_storage.exists(stored_name))

    def test_approved_image_points_at_stored_file(self):
        change_request, _name = self._hold_image_upload()
        stored_name = change_request.diff['front_image']['new']
        self.assertTrue(stored_name.startswith('devicetype-images/'), stored_name)
        self.assertTrue(default_storage.exists(stored_name))
        self.assertFalse(DeviceType.objects.get(pk=self.device_type.pk).front_image)

        change_request.apply(user=self.approver)

        device_type = DeviceType.objects.get(pk=self.device_type.pk)
        self.assertEqual(device_type.front_image.name, stored_name)
        with device_type.front_image.open('rb') as handle:
            self.assertEqual(handle.read(), PNG)
