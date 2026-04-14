import unittest

from robot_brain import parse_text_command


ROBOTS = [
    {"id": "Mata01", "robot_type": "turbopi"},
    {"id": "Tony01", "robot_type": "tonypi"},
]


class RobotBrainParseTests(unittest.TestCase):
    def test_parse_say_command(self):
        result = parse_text_command("Tell Mata01 to say hello class", ROBOTS)
        self.assertTrue(result["ok"])
        self.assertEqual(result["target_robot_id"], "Mata01")
        self.assertEqual(result["intent"]["action"], "say")
        self.assertEqual(result["intent"]["arguments"]["text"], "hello class")

    def test_parse_master_mode(self):
        result = parse_text_command("Put Tony01 into swarm mode", ROBOTS)
        self.assertEqual(result["intent"]["action"], "master_mode")
        self.assertEqual(result["intent"]["arguments"]["mode"], "swarm")

    def test_parse_fleet_stop(self):
        result = parse_text_command("Stop all robots now", ROBOTS)
        self.assertEqual(result["target_scope"], "fleet")
        self.assertEqual(result["intent"]["action"], "allstop")

    def test_parse_catalog_only_wave(self):
        result = parse_text_command("Make Tony01 wave", ROBOTS)
        self.assertEqual(result["intent"]["action"], "catalog_only")
        self.assertFalse(result["intent"]["executable"])


if __name__ == "__main__":
    unittest.main()
