import unittest
from unittest import mock

from app.config import settings
from app.services.health_monitor import HealthMonitor


class _Repo:
    pass


class HealthStateMachineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.hm = HealthMonitor(_Repo())

    async def test_alerts_on_transition_and_recovery(self):
        sent = []

        async def fake_alert(subject, body):
            sent.append(subject)
            return True

        with mock.patch("app.services.health_monitor.send_alert", side_effect=fake_alert):
            # first time down -> alert
            await self.hm._handle_state(("iris_down", "Iris 응답 없음", "detail"))
            self.assertEqual(self.hm._status, "iris_down")
            # same state again, not stale -> no new alert
            await self.hm._handle_state(("iris_down", "Iris 응답 없음", "detail"))
            # recovery -> alert
            await self.hm._handle_state(None)
            self.assertEqual(self.hm._status, "ok")

        self.assertEqual(len(sent), 2)  # down + recovery
        self.assertIn("Iris", sent[0])
        self.assertIn("복구", sent[1])

    async def test_no_alert_when_healthy(self):
        sent = []

        async def fake_alert(subject, body):
            sent.append(subject)
            return True

        with mock.patch("app.services.health_monitor.send_alert", side_effect=fake_alert):
            await self.hm._handle_state(None)
        self.assertEqual(sent, [])


class SpendParsingTests(unittest.IsolatedAsyncioTestCase):
    async def test_costs_api_sums_amounts(self):
        from app.services import alert_service

        payload = {
            "data": [
                {"results": [{"amount": {"value": 1.5}}, {"amount": {"value": 0.25}}]},
                {"results": [{"amount": {"value": 2.0}}]},
            ]
        }

        class _Resp:
            status_code = 200

            def json(self):
                return payload

        class _Client:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, *a, **k):
                return _Resp()

        old_key = settings.openai_admin_key
        settings.openai_admin_key = "sk-admin-test"
        try:
            with mock.patch.object(alert_service.httpx, "AsyncClient", _Client):
                total = await alert_service.fetch_openai_spend_usd(1)
        finally:
            settings.openai_admin_key = old_key
        self.assertAlmostEqual(total, 3.75)

    async def test_no_admin_key_returns_none(self):
        from app.services import alert_service

        old_key = settings.openai_admin_key
        settings.openai_admin_key = None
        try:
            self.assertIsNone(await alert_service.fetch_openai_spend_usd(1))
        finally:
            settings.openai_admin_key = old_key


if __name__ == "__main__":
    unittest.main()
