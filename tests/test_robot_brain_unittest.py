import unittest

from robot_brain import parse_text_command, parse_text_command_plan


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

    def test_parse_multi_step_with_and(self):
        result = parse_text_command_plan("Wave hello to the class and say hello", ROBOTS, preferred_robot_id="Tony01")
        self.assertTrue(result["ok"])
        self.assertTrue(result["multi_step"])
        self.assertEqual(len(result["steps"]), 2)
        self.assertEqual(result["steps"][0]["intent"]["action"], "catalog_only")
        self.assertEqual(result["steps"][1]["intent"]["action"], "say")

    def test_parse_multi_step_with_commas(self):
        result = parse_text_command_plan("center camera, say hello", ROBOTS, preferred_robot_id="Mata01")
        self.assertTrue(result["ok"])
        self.assertTrue(result["multi_step"])
        self.assertEqual(len(result["steps"]), 2)
        self.assertEqual(result["steps"][0]["intent"]["action"], "camera_center")
        self.assertEqual(result["steps"][1]["intent"]["action"], "say")

    def test_parse_multi_step_with_sentences_and_filler(self):
        result = parse_text_command_plan(
            "This is a test. Move forward for 2 seconds. Spin right and say hello.",
            ROBOTS,
            preferred_robot_id="Mata01",
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["multi_step"])
        self.assertEqual(len(result["steps"]), 3)
        self.assertEqual(result["steps"][0]["intent"]["action"], "catalog_only")
        self.assertEqual(result["steps"][0]["intent"]["arguments"]["command"], "forward")
        self.assertEqual(result["steps"][0]["intent"]["arguments"]["duration_s"], 2.0)
        self.assertEqual(result["steps"][1]["intent"]["arguments"]["command"], "turn_right")
        self.assertEqual(result["steps"][2]["intent"]["action"], "say")

    def test_parse_then_split(self):
        result = parse_text_command_plan("wave then say hello", ROBOTS, preferred_robot_id="Tony01")
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["steps"]), 2)
        self.assertEqual(result["steps"][0]["intent"]["arguments"]["command"], "wave")
        self.assertEqual(result["steps"][1]["intent"]["action"], "say")


if __name__ == "__main__":
    unittest.main()
