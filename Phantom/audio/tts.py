import asyncio
import os
import re
import tempfile
import threading
import edge_tts
from utils.logger import log_assistant, log_error

os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = "1"
import pygame

# Initialize pygame mixer once
pygame.mixer.init()

VOICE = "en-US-AvaNeural"

_current_id = 0
_id_lock = threading.Lock()


def clean_markdown_for_tts(text: str) -> str:
    """Strips markdown formatting symbols so TTS never reads 'asterisk' aloud."""
    if not text:
        return text
    # Remove bold: ***text*** or **text**
    text = re.sub(r'\*{1,3}(.*?)\*{1,3}', r'\1', text, flags=re.DOTALL)
    # Remove italic underscores: _text_ or __text__
    text = re.sub(r'_{1,2}(.*?)_{1,2}', r'\1', text, flags=re.DOTALL)
    # Remove inline code backticks: `text`
    text = re.sub(r'`([^`]+)`', r'\1', text)
    # Remove heading markers at line start (## Heading)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Remove remaining bare asterisks/underscores that weren't part of a pair
    text = re.sub(r'[\*_]', '', text)
    return text.strip()


async def _generate_speech(text: str, output_path: str):
    """Generates audio using Edge TTS."""
    communicate = edge_tts.Communicate(text, VOICE)
    await communicate.save(output_path)


def stop():
    """Stop any currently playing or pending TTS immediately."""
    global _current_id
    with _id_lock:
        _current_id += 1
    try:
        pygame.mixer.music.stop()
        pygame.mixer.music.unload()
    except Exception:
        pass


def is_playing() -> bool:
    """Returns True if TTS audio is currently playing."""
    try:
        return pygame.mixer.music.get_busy()
    except Exception:
        return False


def speak(text: str):
    """
    Synthesizes text to speech using Edge TTS and plays it.
    Markdown formatting (*bold*, **bold**, _italic_, etc.) is stripped
    before synthesis so TTS never reads "asterisk" aloud.
    Can be interrupted at any time by calling stop().
    """
    global _current_id

    if not text or not text.strip():
        return

    with _id_lock:
        _current_id += 1
        my_id = _current_id

    # Strip markdown so voice engine never reads asterisks / underscores
    tts_text = clean_markdown_for_tts(text)

    log_assistant(text)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as temp_file:
        temp_filename = temp_file.name

    try:
        if _current_id != my_id:
            return

        asyncio.run(_generate_speech(tts_text, temp_filename))

        if _current_id != my_id:
            return

        pygame.mixer.music.load(temp_filename)
        pygame.mixer.music.play()

        # Wait for playback to finish or be interrupted
        while pygame.mixer.music.get_busy() and _current_id == my_id:
            pygame.time.Clock().tick(20)

    except Exception as e:
        log_error("TTS", f"{e}")
    finally:
        try:
            pygame.mixer.music.stop()
            pygame.mixer.music.unload()
        except Exception:
            pass

        if os.path.exists(temp_filename):
            for _ in range(3):
                try:
                    os.remove(temp_filename)
                    break
                except Exception:
                    time.sleep(0.05)


if __name__ == "__main__":
    speak("Hello! This is a test of the simplified TTS.")
