import unittest

from pentax_sync.camera import extract_photos


class CameraParsingTests(unittest.TestCase):
    def test_parses_flat_photo_records(self):
        photos = extract_photos({"files": [{"path": "DCIM/100PENTX/IMGP0001.JPG", "size": 42}]})
        self.assertEqual(len(photos), 1)
        self.assertEqual(photos[0].camera_path, "DCIM/100PENTX")
        self.assertEqual(photos[0].filename, "IMGP0001.JPG")
        self.assertEqual(photos[0].size, 42)

    def test_parses_nested_directory_map(self):
        photos = extract_photos({"DCIM": {"100PENTX": {"IMGP0002.PEF": {"size": 99}}}})
        self.assertEqual(photos[0].remote_path, "DCIM/100PENTX/IMGP0002.PEF")
        self.assertEqual(photos[0].size, 99)

    def test_structural_list_name_is_not_a_camera_directory(self):
        photos = extract_photos({"photos": [{"filename": "IMGP0003.JPG", "size": 12}]})
        self.assertEqual(photos[0].camera_path, "")

    def test_pentax_v1_directory_listing_keeps_folder_for_download_path(self):
        photos = extract_photos({"errCode": 200, "dirs": [{"name": "100_1503", "files": ["_IMG3936.DNG"]}]})
        self.assertEqual(photos[0].camera_path, "100_1503")
        self.assertEqual(photos[0].remote_path, "100_1503/_IMG3936.DNG")

    def test_pentax_latest_info_dir_and_file_fields(self):
        photos = extract_photos({"captured": True, "dir": "100_1503", "file": "_IMG3936.DNG", "datetime": "2023-03-15T21:51:35"})
        self.assertEqual(photos[0].camera_path, "100_1503")
        self.assertEqual(photos[0].timestamp, "2023-03-15T21:51:35")

    def test_filters_unsupported_files(self):
        photos = extract_photos({"files": ["DCIM/100PENTX/IMG001.MOV", "DCIM/100PENTX/IMG001.DNG"]})
        self.assertEqual([photo.filename for photo in photos], ["IMG001.DNG"])


if __name__ == "__main__":
    unittest.main()
