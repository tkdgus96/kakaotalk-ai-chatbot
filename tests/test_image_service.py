import time
import unittest

from app.services import image_service as img
from app.services.iris_service import _extract_image_urls, parse_iris_webhook

_PHOTO_ATTACH = (
    '{"cs":"ABC","s":66345,"w":422,"h":499,"mt":"image/jpg",'
    '"url":"https://talk.kakaocdn.net/dna/x/y/z/i.jpg?credential=c&expires=1&signature=s",'
    '"thumbnailUrl":"https://talk.kakaocdn.net/dna/x/y/z/i.jpg?convert=resize"}'
)


class ExtractImageUrlTests(unittest.TestCase):
    def test_single_photo_type2(self):
        urls = _extract_image_urls(2, _PHOTO_ATTACH)
        self.assertEqual(len(urls), 1)
        self.assertTrue(urls[0].startswith("https://talk.kakaocdn.net/"))

    def test_multiphoto_imageurls_list(self):
        att = {"imageUrls": ["https://a/1.jpg", "https://a/2.jpg"]}
        self.assertEqual(_extract_image_urls(71, att), ["https://a/1.jpg", "https://a/2.jpg"])

    def test_multiphoto_thl_structure(self):
        att = {"C": {"THL": [{"TH": {"THU": "https://a/1.jpg"}}, {"TH": {"THU": "https://a/2.jpg"}}]}}
        self.assertEqual(_extract_image_urls(71, att), ["https://a/1.jpg", "https://a/2.jpg"])

    def test_non_image_returns_empty(self):
        self.assertEqual(_extract_image_urls(1, ""), [])
        self.assertEqual(_extract_image_urls(1, "not json"), [])


class ParseWebhookImageTests(unittest.TestCase):
    def test_photo_message_parses_with_image_urls(self):
        m = parse_iris_webhook(
            {"msg": "", "room": "방", "sender": "김상현", "json": {"chat_id": 7, "type": 2, "attachment": _PHOTO_ATTACH}}
        )
        self.assertIsNotNone(m)
        self.assertEqual(len(m.image_urls), 1)

    def test_empty_text_no_image_is_ignored(self):
        m = parse_iris_webhook({"msg": "", "room": "방", "sender": "김상현", "json": {"chat_id": 7, "type": 1}})
        self.assertIsNone(m)


class ImageRefAndCacheTests(unittest.TestCase):
    def tearDown(self):
        img._recent_image.clear()

    def test_references_image(self):
        self.assertTrue(img.references_image("이 사진 뭐야"))
        self.assertTrue(img.references_image("방금 그림 설명해줘"))
        self.assertFalse(img.references_image("오늘 날씨 어때"))

    def test_recent_image_cache_and_ttl(self):
        img.remember_room_image(1, "https://a/x.jpg")
        self.assertEqual(img.get_recent_room_image(1), "https://a/x.jpg")
        img._recent_image[1]["ts"] = time.time() - (img._RECENT_IMAGE_TTL + 10)
        self.assertIsNone(img.get_recent_room_image(1))

    def test_strip_command_prefix(self):
        self.assertEqual(img._strip_command_prefix("!이 사진 뭐야"), "이 사진 뭐야")
        self.assertEqual(img._strip_command_prefix("！그림 설명"), "그림 설명")


class DetectImageGenerationTests(unittest.TestCase):
    def test_extracts_prompt(self):
        self.assertEqual(
            img.detect_image_generation("!한강에서 라면 먹는 고양이 그려줘"),
            "한강에서 라면 먹는 고양이",
        )
        self.assertEqual(img.detect_image_generation("고양이 그림 그려줘"), "고양이 그림")
        self.assertEqual(img.detect_image_generation("!노을 사진 만들어줘"), "노을 사진")
        self.assertEqual(img.detect_image_generation("우주 고양이 생성해줘"), "우주 고양이")
        # gen verb mid-sentence with trailing clarification (reference case)
        self.assertEqual(
            img.detect_image_generation("!사과 먹는 곰 그려줘 방금 올린 사진 참고해서"),
            "사과 먹는 곰 방금 올린 사진 참고해서",
        )

    def test_non_generation_false_positives(self):
        self.assertIsNone(img.detect_image_generation("오늘 뭐 만들어 먹지"))
        self.assertIsNone(img.detect_image_generation("이건 어떻게 만들어졌어?"))
        self.assertIsNone(img.detect_image_generation("그려진 그림 설명해줘"))

    def test_non_generation_returns_none(self):
        self.assertIsNone(img.detect_image_generation("!이 사진 뭐야"))
        self.assertIsNone(img.detect_image_generation("오늘 날씨 어때"))
        self.assertIsNone(img.detect_image_generation("!그려줘"))  # no description


class GeneratedImageCollectionTests(unittest.TestCase):
    def tearDown(self):
        img._generated_by_room.clear()

    def test_collect_and_take_by_room(self):
        img.start_image_collection(7)
        img.collect_generated_image(7, "AAA")
        img.collect_generated_image(7, "BBB")
        self.assertEqual(img.take_generated_images(7), ["AAA", "BBB"])
        # take clears it
        self.assertEqual(img.take_generated_images(7), [])

    def test_rooms_are_isolated(self):
        img.start_image_collection(1)
        img.start_image_collection(2)
        img.collect_generated_image(1, "A")
        self.assertEqual(img.take_generated_images(2), [])
        self.assertEqual(img.take_generated_images(1), ["A"])


class ImageCommandUsageTests(unittest.IsolatedAsyncioTestCase):
    async def test_usage_messages_without_network(self):
        text, imgs = await img.handle_image_command("!그림", [])
        self.assertIn("사용법", text)
        self.assertEqual(imgs, [])
        text, imgs = await img.handle_image_command("!이미지", [])
        self.assertIn("사용법", text)

    async def test_non_image_command_returns_none(self):
        self.assertIsNone(await img.handle_image_command("!보스매주", ["검마"]))


if __name__ == "__main__":
    unittest.main()
