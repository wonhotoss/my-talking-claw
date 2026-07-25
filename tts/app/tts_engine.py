import os
import tempfile


class tts_engine:
    """On-device Korean text-to-speech backed by MeloTTS.

    MeloTTS (torch + mecab-ko g2p) is imported lazily on first use so this
    module imports cleanly on hosts where the engine is not installed (e.g. the
    Windows dev box); the service is meant to run in the provided Docker image
    or on Linux. Configuration via environment:

    - TTS_LANGUAGE  (default KR)
    - TTS_SPEAKER   (default KR; key into the model's speaker table)
    - TTS_DEVICE    (default cpu; cuda on a GPU machine)
    - TTS_SPEED     (default 1.0)
    """

    def __init__(self) -> None:
        self.engine = os.environ.get("TTS_ENGINE", "melotts")
        self.language = os.environ.get("TTS_LANGUAGE", "KR")
        self.speaker = os.environ.get("TTS_SPEAKER", "KR")
        self.device = os.environ.get("TTS_DEVICE", "cpu")
        self.speed = float(os.environ.get("TTS_SPEED", "1.0"))
        # Loading the model is expensive, so it is cached lazily on first use.
        self._model = None

    @property
    def model(self):
        if self._model is None:
            from melo.api import TTS

            self._model = TTS(language=self.language, device=self.device)

        return self._model

    def synthesize(self, text: str) -> bytes:
        model = self.model
        speaker_id = model.hps.data.spk2id[self.speaker]

        temp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        try:
            temp.close()
            model.tts_to_file(text, speaker_id, temp.name, speed=self.speed)

            with open(temp.name, "rb") as wav_file:
                return wav_file.read()
        finally:
            os.unlink(temp.name)
