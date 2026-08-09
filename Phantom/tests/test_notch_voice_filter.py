import unittest
from audio.stt import has_wake_word


class TestNotchVoiceFilterLogic(unittest.TestCase):
    def check_should_process(self, text: str, opened_by_hotkey: bool) -> bool:
        is_wake_word_present = has_wake_word(text)
        return is_wake_word_present or opened_by_hotkey

    def test_voice_filtering_matrix(self):
        # Case 1: Wake word spoken, NOT opened by hotkey -> SHOULD process
        self.assertTrue(self.check_should_process("Phantom what is the weather", opened_by_hotkey=False))

        # Case 2: Wake word NOT spoken, NOT opened by hotkey -> SHOULD IGNORE
        self.assertFalse(self.check_should_process("what is the weather", opened_by_hotkey=False))

        # Case 3: Wake word NOT spoken, WAS opened by hotkey -> SHOULD process
        self.assertTrue(self.check_should_process("what is the weather", opened_by_hotkey=True))

        # Case 4: Wake word spoken, WAS opened by hotkey -> SHOULD process
        self.assertTrue(self.check_should_process("Phantom what is the weather", opened_by_hotkey=True))


if __name__ == "__main__":
    unittest.main()
