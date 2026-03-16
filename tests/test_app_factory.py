import unittest

from app import create_app


class AppFactoryTests(unittest.TestCase):
    def test_create_app_has_expected_routes(self):
        app = create_app()
        paths = {route.path for route in app.routes}
        self.assertIn("/chat", paths)
        self.assertIn("/rooms", paths)
        self.assertIn("/debug", paths)

    def test_room_routes_are_registered(self):
        app = create_app()
        route_map = {}
        for route in app.routes:
            if not hasattr(route, "methods"):
                continue
            route_map.setdefault(route.path, set()).update(route.methods)
        self.assertIn("GET", route_map["/rooms"])
        self.assertIn("POST", route_map["/rooms/{room_id}"])
        self.assertIn("DELETE", route_map["/rooms/{room_id}"])


if __name__ == "__main__":
    unittest.main()
