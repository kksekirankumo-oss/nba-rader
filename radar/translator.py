from functools import lru_cache

from .config import Config


class Translator:
    def __init__(self):
        self.enabled = bool(Config.DEEPL_API_KEY)
        self._client = None
        if self.enabled:
            import deepl
            self._client = deepl.Translator(Config.DEEPL_API_KEY)

    @lru_cache(maxsize=2048)
    def translate(self, text: str) -> str:
        text = (text or '').strip()
        if not text or not self.enabled:
            return ''
        # Headlines/summaries only. Avoid sending huge article bodies.
        text = text[:1800]
        try:
            result = self._client.translate_text(
                text,
                target_lang='ZH-HANS',
                preserve_formatting=True,
                context='NBA basketball news. Keep player and team names recognizable and translate naturally as a Chinese sports headline.'
            )
            return str(result)
        except Exception as exc:
            print(f'[translate] {exc}')
            return ''
