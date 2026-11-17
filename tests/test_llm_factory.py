import unittest

from langchain_openai import ChatOpenAI

from app.config import settings
from app.llm_factory import make_chat_model, model_name, parse_model_spec, with_fallback
from app.services.usage_service import _price_key, _token_cost


class ModelSpecTests(unittest.TestCase):
    def test_parse_spec_defaults_to_configured_provider(self):
        old = settings.llm_provider
        try:
            settings.llm_provider = "openai"
            self.assertEqual(parse_model_spec("gpt-4o"), ("openai", "gpt-4o"))
            self.assertEqual(parse_model_spec("anthropic:claude-sonnet-4-5"), ("anthropic", "claude-sonnet-4-5"))
            self.assertEqual(parse_model_spec("Google_GenAI:gemini-2.5-flash"), ("google_genai", "gemini-2.5-flash"))
            self.assertEqual(model_name("openai:gpt-4.1-mini"), "gpt-4.1-mini")
            self.assertEqual(model_name("gpt-4o"), "gpt-4o")
        finally:
            settings.llm_provider = old

    def test_openai_spec_builds_chat_openai(self):
        m = make_chat_model("gpt-4o-mini", temperature=0)
        self.assertIsInstance(m, ChatOpenAI)
        self.assertEqual(m.model_name, "gpt-4o-mini")
        m2 = make_chat_model("openai:gpt-4.1-nano")
        self.assertEqual(m2.model_name, "gpt-4.1-nano")

    def test_unknown_provider_without_package_raises_clear_error(self):
        with self.assertRaises((RuntimeError, ValueError)) as ctx:
            make_chat_model("nonexistent_provider:foo")
        self.assertIn("nonexistent_provider", str(ctx.exception))

    def test_with_fallback_passthrough_and_wrap(self):
        primary = make_chat_model("gpt-4o-mini")
        self.assertIs(with_fallback(primary, None), primary)
        wrapped = with_fallback(primary, make_chat_model("gpt-4.1-nano"))
        self.assertTrue(hasattr(wrapped, "fallbacks"))
        self.assertEqual(len(wrapped.fallbacks), 1)


class PricingKeyTests(unittest.TestCase):
    def test_price_key_strips_prefix_and_date(self):
        self.assertEqual(_price_key("anthropic:claude-sonnet-4-5"), "claude-sonnet-4-5")
        self.assertEqual(_price_key("gpt-4o-2024-08-06"), "gpt-4o")
        self.assertEqual(_price_key("gpt-4o-mini"), "gpt-4o-mini")
        self.assertEqual(_price_key("gpt-4.1-mini-2025-04-14"), "gpt-4.1-mini")
        self.assertAlmostEqual(_token_cost("openai:gpt-4o", 1_000_000, 0), 2.50)


if __name__ == "__main__":
    unittest.main()
