# On-device Korean TTS service (MeloTTS) as a portable container.
# MeloTTS needs torch + mecab-ko + unidic, which don't install cleanly on
# Windows; this image runs the same anywhere Docker does (dev box, Pi, Linux).
FROM python:3.10-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git build-essential curl \
 && rm -rf /var/lib/apt/lists/*

# CPU-only torch/torchaudio as a matched pair, so MeloTTS neither pulls the
# multi-GB CUDA stack nor ends up with an ABI-mismatched torchaudio. The pip
# constraint keeps these exact versions while MeloTTS installs its other deps.
RUN pip install --no-cache-dir torch==2.2.2 torchaudio==2.2.2 --index-url https://download.pytorch.org/whl/cpu

# MeloTTS + Korean g2p (mecab-ko) + unidic dictionary.
RUN printf "torch==2.2.2\ntorchaudio==2.2.2\n" > /tmp/constraints.txt \
 && git clone --depth 1 https://github.com/myshell-ai/MeloTTS.git /opt/melotts \
 && PIP_CONSTRAINT=/tmp/constraints.txt pip install --no-cache-dir -e /opt/melotts \
 && python -m unidic download \
 && pip install --no-cache-dir python-mecab-ko

# Service framework.
RUN pip install --no-cache-dir "fastapi>=0.115.0" "uvicorn[standard]>=0.30.0"

WORKDIR /app
COPY app /app/app

ENV TTS_ENGINE=melotts \
    TTS_LANGUAGE=KR \
    TTS_SPEAKER=KR \
    TTS_DEVICE=cpu \
    TTS_SPEED=1.0

EXPOSE 8200
CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8200"]
