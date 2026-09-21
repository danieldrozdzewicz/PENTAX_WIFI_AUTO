import tempfile
import unittest
from pathlib import Path

from pentax_sync.camera import RemotePhoto
from pentax_sync.state import StateStore
from pentax_sync.storage import download_atomic


class FakeCamera:
    def __init__(self, payload=None, content_type=None):
        self.payload = payload
        self.content_type = content_type

    def download(self, photo, output):
        payload = self.payload
        if payload is None:
            payload = b"II*\x00camera-image-bytes" if photo.filename.lower().endswith((".dng", ".pef")) else b"\xff\xd8\xffcamera-image-bytes"
        output.write(payload)
        media_type = self.content_type or ("image/x-adobe-dng" if photo.filename.lower().endswith((".dng", ".pef")) else "image/jpeg")
        return len(payload), media_type


class StorageTests(unittest.TestCase):
    def test_download_is_finalized_and_hashed(self):
        with tempfile.TemporaryDirectory() as tmp:
            photo = RemotePhoto("DCIM/100PENTX", "IMG001.DNG")
            path, size, digest = download_atomic(FakeCamera(), photo, Path(tmp))
            self.assertTrue(path.read_bytes().startswith(b"II*\x00"))
            self.assertEqual(size, len(b"II*\x00camera-image-bytes"))
            self.assertEqual(len(digest), 64)
            self.assertFalse(path.with_name(path.name + ".part").exists())

    def test_existing_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "IMG001.JPG").write_bytes(b"old")
            photo = RemotePhoto("DCIM/100PENTX", "IMG001.JPG")
            path, _, _ = download_atomic(FakeCamera(), photo, root)
            self.assertEqual((root / "IMG001.JPG").read_bytes(), b"old")
            self.assertEqual(path.name, "IMG001_2.JPG")

    def test_error_document_is_never_finalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            photo = RemotePhoto("100_1503", "IMG001.DNG")
            with self.assertRaisesRegex(IOError, "error document"):
                download_atomic(FakeCamera(b'{"errCode":400}', "application/json"), photo, Path(tmp))
            self.assertFalse((Path(tmp) / "IMG001.DNG").exists())

    def test_sqlite_deduplicates_by_camera_path_and_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            photo = RemotePhoto("DCIM/100PENTX", "IMG001.JPG", 3)
            local = root / photo.filename
            local.write_bytes(b"jpg")
            state = StateStore(root / "state.db")
            self.assertFalse(state.is_downloaded(photo))
            state.mark(photo, "DOWNLOADED", local_path=str(local), size=3, sha256="abc")
            self.assertTrue(state.is_downloaded(photo))
            state.close()


if __name__ == "__main__":
    unittest.main()
