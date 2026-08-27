import unittest

from robot_brain import build_llm_parser_prompt, parse_text_command, parse_text_command_plan


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

    def test_parse_say_text_with_and_does_not_split(self):
        result = parse_text_command_plan("Tell Tony01 to say hello and goodbye", ROBOTS, preferred_robot_id="Tony01")
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["steps"]), 1)
        self.assertEqual(result["intent"]["action"], "say")
        self.assertTrue(result["intent"]["executable"])
        self.assertEqual(result["intent"]["arguments"]["text"], "hello and goodbye")

    def test_parse_multi_step_preserves_per_step_targets(self):
        result = parse_text_command_plan("Tony01 wave then Mata01 say hello", ROBOTS, preferred_robot_id="Tony01")
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["steps"]), 2)
        self.assertEqual(result["steps"][0]["target_robot_id"], "Tony01")
        self.assertEqual(result["steps"][1]["target_robot_id"], "Mata01")
        self.assertEqual(result["steps"][0]["intent"]["arguments"]["command"], "wave")
        self.assertEqual(result["steps"][1]["intent"]["action"], "say")

    def test_parse_filler_prefix_keeps_first_recognized_action(self):
        result = parse_text_command_plan("This is a test message. say hello", ROBOTS, preferred_robot_id="Tony01")
        self.assertTrue(result["ok"])
        self.assertEqual(result["intent"]["action"], "say")
        self.assertTrue(result["intent"]["executable"])
        self.assertEqual(len(result["steps"]), 1)
        self.assertEqual(result["steps"][0]["intent"]["action"], "say")


class BuildLlmParserPromptTests(unittest.TestCase):
    """Regression coverage for the few-shot examples added to the LLM
    fallback prompt. Note: build_llm_parser_prompt's output contract is a
    single flat {action, target_scope, target_robot_id, arguments, summary}
    object — it has no "steps" wrapper. Multi-step chained commands (e.g.
    "wave then say hello") are split and parsed entirely by the rule-based
    parse_text_command_plan() before the LLM is ever consulted; the LLM
    prompt is only invoked as a single-action fallback on the *whole*
    input text when the rule-based parser can't recognize anything at all
    (see _parse_robot_text_request in app.py). So the "multi-step" example
    below targets the eb0185f/18e7a35 bugs (filler text hijacking the
    parsed action, and "and"/"then" inside a spoken `say` phrase getting
    treated as a command separator) within that single-object contract,
    rather than asserting a steps array the downstream code doesn't
    consume.
    """

    def test_prompt_includes_single_step_examples(self):
        prompt = build_llm_parser_prompt("say hello", ROBOTS, preferred_robot_id="Tony01")
        self.assertIn('Text: "Tony01, say hello to the class"', prompt)
        self.assertIn('"action": "say"', prompt)
        self.assertIn('Text: "Stop every robot right now"', prompt)
        self.assertIn('"action": "allstop", "target_scope": "fleet"', prompt)

    def test_prompt_includes_filler_and_conjunction_guard_example(self):
        # Regression for eb0185f ("Improve robot brain parsing for chained
        # voice commands") and 18e7a35 ("Fix chained robot command
        # parsing"): a leading non-command filler phrase must not hijack
        # the parsed action, and "and"/"then" inside spoken text must not
        # truncate arguments.text.
        prompt = build_llm_parser_prompt("say hello", ROBOTS, preferred_robot_id="Tony01")
        self.assertIn('Text: "This is a test. Tony01, say hello and welcome everyone"', prompt)
        self.assertIn('"arguments": {"text": "hello and welcome everyone"}', prompt)
        self.assertIn("filler, not a command", prompt)
        self.assertIn("do not truncate arguments.text at", prompt)

    def test_prompt_still_includes_allowlist_and_shape(self):
        prompt = build_llm_parser_prompt("say hi", ROBOTS, preferred_robot_id="Mata01")
        self.assertIn("Allowed actions:", prompt)
        self.assertIn("Required JSON shape:", prompt)
        self.assertIn("Examples:", prompt)


if __name__ == "__main__":
    unittest.main()
