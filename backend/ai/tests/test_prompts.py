from django.test import SimpleTestCase

from ai.prompts import ALL_PROMPTS, PROMPT_VERSION, SYSTEM_POLICY, prompts_fingerprint

# Pinned together. Changing ANY runtime prompt text changes the fingerprint;
# update both PROMPT_VERSION (ai/prompts/__init__.py) and these two values in
# the same reviewed change (phase section 74).
PINNED_VERSION = "askbooks-2026-09-16.1"
PINNED_FINGERPRINT = "39c35188c253514188e314dad777f88c0028e60a63d4bafd75c79eb5f0fa3c87"


class PromptVersioningTests(SimpleTestCase):
    def test_prompt_changes_require_a_version_bump(self):
        self.assertEqual(PROMPT_VERSION, PINNED_VERSION)
        self.assertEqual(prompts_fingerprint(), PINNED_FINGERPRINT, "prompt text changed: bump PROMPT_VERSION and re-pin")

    def test_system_policy_states_the_non_negotiable_rules(self):
        policy = SYSTEM_POLICY.lower()
        for phrase in ("never calculate", "not instructions", "never invent a source_id", "never reveal", "cannot post"):
            self.assertIn(phrase, policy)

    def test_prompts_stay_concise(self):
        self.assertLess(sum(len(text) for text in ALL_PROMPTS.values()), 6_000)
