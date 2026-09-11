"""Local speech recognition and the bot's independent Notion API connection."""

import os
import tempfile
import threading

import httpx


class NotionConfigurationError(ValueError):
    pass


async def create_task(title, *, transport=None):
    token = os.environ.get("NOTION_TOKEN", "").strip()
    source = os.environ.get("NOTION_DATA_SOURCE_ID", "").strip()
    if not token or not source:
        raise NotionConfigurationError("Configure NOTION_TOKEN e NOTION_DATA_SOURCE_ID.")
    if not title.strip() or len(title) > 500:
        raise ValueError("Use um título de 1 a 500 caracteres.")
    headers = {"Authorization": f"Bearer {token}", "Notion-Version": "2025-09-03"}
    async with httpx.AsyncClient(timeout=20, transport=transport) as client:
        response = await client.get(
            f"https://api.notion.com/v1/data_sources/{source}", headers=headers
        )
        response.raise_for_status()
        properties = response.json()["properties"]
        title_property = next(
            (name for name, prop in properties.items() if prop.get("type") == "title"), None
        )
        if not title_property:
            raise NotionConfigurationError("A base não tem uma propriedade de título.")
        response = await client.post(
            "https://api.notion.com/v1/pages",
            headers=headers,
            json={
                "parent": {"type": "data_source_id", "data_source_id": source},
                "properties": {
                    title_property: {"title": [{"text": {"content": title.strip()}}]}
                },
            },
        )
        # Do not retry page creation: a lost response may still mean the page exists.
        response.raise_for_status()
        return response.json()["url"]


class SpeechRecognizer:
    """Load once, serialize CPU inference, and keep model files across restarts."""

    def __init__(self):
        self._model = None
        self._lock = threading.Lock()

    def transcribe(self, path):
        with self._lock:
            if self._model is None:
                from faster_whisper import WhisperModel

                self._model = WhisperModel(
                    os.environ.get("WHISPER_MODEL", "base"),
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=2,
                    download_root=os.environ.get("WHISPER_CACHE")
                    or os.path.join(tempfile.gettempdir(), "whisper"),
                )
            segments, _ = self._model.transcribe(path, beam_size=1, vad_filter=True)
            return " ".join(segment.text.strip() for segment in segments).strip()


speech = SpeechRecognizer()
