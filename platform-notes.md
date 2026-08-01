# 실행 플랫폼 비교 — 라즈베리파이 4 vs Lenovo IdeaPad S210 Touch

프론트를 뺀 전 시스템(STT + TTS + backend + agent-gateway)을 두 기기에서 돌리는 경우를 비교한다.
이어서 프론트까지 기기가 들고 가는 형태(마이크 + 스피커 + 얼굴 화면)도 같이 본다.

## 결론 (먼저)

1. **지금의 MeloTTS로는 두 기기 모두 실패한다.** 데스크탑(Zen2 4코어)에서 이미 RTF 0.98이다 —
   6.7초 음성에 6.5초. 투영하면 **파이 33~46초 / 아이디어패드 33~49초**(Celeron이면 71~92초).
2. **Piper `ko_KR-kss-medium`을 실측했더니 같은 조건에서 17~27배 빨랐다.** RTF 0.98 → **0.057**.
   **1코어로도 RTF 0.095**다. 이 하나로 두 기기 다 살아난다 — 턴 지연 40~90초에서 **5~12초**로.
3. **viseme 타임라인은 안 깨진다.** 이전 판단은 틀렸다. Piper 1.6.0에
   `piper.patch_voice_with_alignment`가 있어 `w_ceil`을 ONNX 출력으로 노출하고,
   `include_alignments=True`로 음소별 샘플 수를 받는다. 실측 결과 **커버리지 100.00%,
   시간 해상도 11.6ms로 MeloTTS와 동일**하다. day-5의 계약이 거의 그대로 옮겨간다.
4. TTS를 Piper로 바꾸면 **RAM 요구가 8GB → 4GB로 내려간다**(TTS RSS 2.4GB → 0.25GB).
   기기 선택의 비용 구조 자체가 바뀐다.
5. 두 질문에 답이 다르다.
   - **"오늘 당장 데스크탑에서 서비스를 내린다"** → **아이디어패드**. x86이라 지금 이미지가 그대로 돈다.
   - **"최종 기기 형태(얼굴 + 마이크 + 스피커)"** → **RPi4**(Piper 전환 시 4GB로 충분). 4코어라
     TTS가 돌아도 얼굴에 코어를 남길 수 있고, 팬리스 3~7W라 마이크 옆이 조용하다.
6. 대가는 셋 중 **둘이 사라지고 하나가 남았다**. viseme(해결) · g2p 품질(대부분 해결, 영어 차용어만
   MeloTTS 우세) · **CC BY-NC-SA 4.0 라이선스(남음)**.

---

## 1. 측정 (실측, 이 데스크탑)

AMD Ryzen 3 3300X (Zen2 4C/8T @3.8GHz, AVX2, DDR4 듀얼채널), 16GB.
컨테이너를 `docker run --cpus=N`으로 묶어 코어 수 효과만 분리했다. Piper는 onnxruntime이
OpenMP를 안 쓰므로 `SessionOptions.intra_op_num_threads`로 스레드 수를 맞췄다.
문장은 `"네, 확인했습니다. 지금 서울은 흐리고 비가 조금 내리고 있어요."`, 워밍업 1회 후 3회 중앙값.

### TTS — MeloTTS vs Piper

| 코어 | MeloTTS (44.1kHz) | RTF | Piper ko_KR-kss-medium (22.05kHz) | RTF | 배수 |
|---|---|---|---|---|---|
| 1 | (17.5s, 외삽) | 2.61 | **494.6 ms** | **0.095** | **27×** |
| 2 | 10,210 ms | 1.53 | **333.5 ms** | **0.067** | **23×** |
| 4 | 6,546 ms | 0.98 | **287.1 ms** | **0.057** | **17×** |
| 4C/8T | 4,396 ms | 0.65 | — | — | |

(오디오 길이가 달라서 — MeloTTS 6.7초, Piper 5.0초 — 배수는 RTF 비로 계산했다.
Piper가 같은 문장을 약 25% 빠르게 말한다. `length_scale`로 조정 가능.)

| | MeloTTS | Piper |
|---|---|---|
| 모델 로드 (콜드) | **24.7 s** (4코어) / 30.6 s (2코어) | **1.0 ~ 1.3 s** |
| RSS (모델 로드 후) | **2.3 ~ 2.5 GB** | **252 MB** (로드 직후 155MB) |
| Docker 이미지 | **5.53 GB** | **605 MB**¹ |
| 모델 파일 | HF 캐시 ~0.65GB (ckpt + BERT) | **63.2 MB** |
| 런타임 | torch 2.2.2 + transformers + mecab-ko + unidic | onnxruntime + espeak-ng (번들) |

¹ 일회성 패치용 `onnx` 패키지 포함. 런타임엔 불필요하므로 실제 배포 이미지는 더 작다.

**Piper는 병렬 확장이 거의 없다** (1→4코어 1.72배). 뒤집으면 **코어가 하나뿐이어도 RTF 0.095**라는
뜻이고, 약한 다코어 기기에 이보다 좋은 성질은 없다.

### MeloTTS는 왜 느린가

컨테이너 안에서 단계를 갈라 재봤다 (3.88초 오디오, 4스레드):

| 단계 | 시간 | 비중 |
|---|---|---|
| g2p + BERT (`kykim/bert-kor-base`) | 306 ms | 8% |
| **VITS `infer` (디코더/보코더)** | **3,550 ms** | **92%** |

BERT가 범인이 아니다. **44.1kHz 보코더**다. 같은 VITS 계열인데 Piper medium은 22.05kHz —
출력 샘플이 절반이다. 이게 17~27배 차이의 큰 축이다(모델 크기 차이가 나머지).

### STT (참고 — 엔진 교체 대상 아님)

| 코어 | whisper base int8 | RTF |
|---|---|---|
| 1 | 1,799 ms | 0.267 |
| 2 | 1,126 ms | 0.167 |
| 4 | 785 ms | 0.117 |

RSS 187 MB. 6.7초 오디오 기준. 두 워크로드 다 Amdahl로 깨끗하게 맞는다 (Zen2-코어-초):

```
MeloTTS  T(n) = 2.89 + 14.64/n     (직렬 20%, T(∞)=2.9s — 코어로 못 푸는 바닥)
Whisper  T(n) = 0.44 +  1.36/n     (직렬 25%, T(1) 예측 1.81s vs 실측 1.80s)
```

---

## 2. 두 기기 제원

| | Raspberry Pi 4 | IdeaPad S210 Touch (2013) |
|---|---|---|
| CPU | Cortex-A72 ×4 @1.5GHz (일부 1.8) | Celeron 1017U/1037U · Pentium 2117U · **Core i3-3217U** 중 하나 |
| 코어/스레드 | 4C/4T | 2C/2T (i3만 2C/4T) |
| SIMD | NEON 128-bit, **dotprod 없음** | i3: AVX(FP만 256-bit) · **Celeron/Pentium: AVX 자체 없음** |
| RAM | 1/2/4/8 GB LPDDR4 | 2 or 4 GB DDR3L, SO-DIMM 1슬롯 (최대 8GB) |
| 저장 | microSD / USB SSD | **500GB 5400rpm HDD** |
| 화면 | 없음 (별도) | **11.6" 1366×768 터치 내장** |
| 오디오 | **아날로그 입력 없음**, 3.5mm 출력은 PWM 품질 | 내장 마이크 + 스피커 + 3.5mm 콤보 |
| 전력 | 3~7W, 팬리스 가능 | 12~25W, **팬 있음** |
| 기타 | GPIO, USB3, GbE | 배터리(UPS), 키보드, Wi-Fi 2.4GHz n |

> S210 제원은 대표 구성이다. **CPU 변종에 따라 숫자가 갈리므로 실기 확인이 필요하다** (§4).

### 코어당 성능 배수 (추정 — 여기가 유일한 추정 구간이다)

Zen2 @3.8GHz 코어를 1.0으로 놓았다. §1의 실측치에 이 배수를 곱해 §3을 만든다.

| 코어 | ONNX FP32 (Piper) | torch FP32 (MeloTTS) | int8 GEMM (STT) | 근거 |
|---|---|---|---|---|
| Cortex-A72 @1.5GHz | ×5 ~ 8 | ×5 ~ 7 | ×8 ~ 10 | NEON 128-bit / 1.5GHz. int8은 **SDOT 없음**(A76부터)이라 격차가 큼. ORT의 MLAS는 ARM NEON 커널이 잘 최적화돼 있어 하단(×5)이 현실적 |
| i3-3217U @1.8GHz | ×4 ~ 6 | ×4 ~ 6 | ×5 ~ 7 | AVX는 FP만 256-bit, **정수 SIMD는 128-bit**(AVX2부터 256) + FMA 없음 |
| Celeron 1037U @1.8GHz | ×7 ~ 9 | ×7 ~ 9 | ×6 ~ 8 | AVX 없음 = SSE4.2 폭, 캐시 2MB, HT 없음 |

int8 A72 배수(×8~10)는 hardware-notes.md의 "RPi4 base 한국어는 실시간 언저리"와 역산해 맞춘
값이라 신뢰도가 상대적으로 높다. FP32 배수는 이론 FLOPS 기반이라 폭이 넓다.

---

## 3. 투영 — 프론트 제외, 전 시스템 실행

사용자 발화 3초 → 약 5초 분량 응답 1턴 기준.

| | 데스크탑 | RPi4 (4×A72) | S210 / i3-3217U | S210 / Celeron 1037U |
|---|---|---|---|---|
| **TTS — MeloTTS** | 4.4 s (RTF 0.65) | 33 ~ 46 s | 33 ~ 49 s¹ | 71 ~ 92 s |
| **TTS — Piper** | **0.29 s (RTF 0.057)** | **1.4 ~ 2.3 s** (RTF 0.28~0.46) | **1.1 ~ 1.6 s**¹ (RTF 0.21~0.32) | **2.3 ~ 3.0 s** (RTF 0.46~0.59) |
| STT base int8 | 0.4 s | 2.8 ~ 3.5 s | 2.1 ~ 3.0 s¹ | 3.0 ~ 4.0 s |
| 에이전트 (`claude -p`, 망 대기) | 2 ~ 5 s | 동일 | 동일 | 동일 |
| **턴 지연 — MeloTTS** | ~8 s | **~44 s** | **~46 s** | **~87 s** |
| **턴 지연 — Piper** | ~3 s | **6 ~ 11 s** | **5 ~ 10 s** | **7 ~ 12 s** |
| TTS 콜드 스타트 — MeloTTS | 24.7 s | 2 ~ 5 분 | 2 ~ 5 분 | 4 ~ 8 분 |
| TTS 콜드 스타트 — Piper | 1.2 s | **6 ~ 10 s** | **5 ~ 8 s** | **8 ~ 11 s** |

¹ HT 이득 20% 반영.

MeloTTS에서는 두 기기의 차이(44초 vs 46초)가 무의미했다 — 엔진이 전부였다.
**Piper로 바꾸면 파이가 현재 데스크탑 체감(~8초)과 같은 대역에 들어온다.**

파이에서 눈여겨볼 것: **TTS를 1코어에 묶어도**(`--cpus=1`) 494.6ms × 5~8 = 2.5~4.0초, **RTF
0.49~0.78로 여전히 실시간 이하**다. 나머지 3코어를 STT와 얼굴 렌더에 그대로 줄 수 있다.
MeloTTS로는 상상할 수 없던 여유다.

### 메모리 예산 — 엔진 교체로 4GB가 열린다

| 컴포넌트 | MeloTTS 구성 | Piper 구성 |
|---|---|---|
| TTS | 2.3 ~ 2.5 GB | **0.25 GB** |
| STT (whisper base int8) | 0.19 GB | 0.19 GB |
| backend + agent-gateway | ~0.1 GB | ~0.1 GB |
| `claude` CLI (Node, 턴마다) | 0.3 ~ 0.6 GB | 0.3 ~ 0.6 GB |
| OS + Docker 데몬 | 0.3 ~ 0.8 GB | 0.3 ~ 0.8 GB |
| **소계 (프론트 제외)** | 3.2 ~ 4.2 GB | **1.1 ~ 1.9 GB** |
| \+ Chromium 키오스크 (얼굴) | 0.4 ~ 0.7 GB | 0.4 ~ 0.7 GB |
| **소계 (기기 자체 프론트)** | **3.7 ~ 4.9 GB** | **1.5 ~ 2.6 GB** |

- MeloTTS 유지 시: **RPi4 8GB 필수**, S210도 8GB 증설 필요.
- **Piper 전환 시: RPi4 4GB로 충분**, S210은 4GB 그대로 간다. 파이 본체 가격이 한 단계 내려간다.

### 저장소

| | MeloTTS | Piper |
|---|---|---|
| 이미지 + 모델 | ~6.2 GB | **~0.67 GB** |
| microSD 적정 | 32GB+ / USB SSD 권장 | 16GB로도 여유 |

S210의 500GB는 공간이 문제가 아니라 **5400rpm + 12년 된 디스크**가 문제다. MeloTTS면 2.5GB
모델 로드로 콜드 스타트가 분 단위가 되고, Piper면 63MB라 사실상 무시된다. 그래도 고장 위험 때문에
**SSD 교체(약 2.5만원) 권장**은 유지한다.

---

## 4. 아키텍처/빌드 블로커

### RPi4 — Piper로 가면 블로커가 사라진다

MeloTTS를 유지하는 경우에만 문제가 있다. [tts/Dockerfile:17](tts/Dockerfile#L17):

```dockerfile
RUN pip install --no-cache-dir torch==2.2.2 torchaudio==2.2.2 --index-url https://download.pytorch.org/whl/cpu
```

인덱스를 실제로 확인했다.

| 소스 | torch 2.2.2 aarch64 |
|---|---|
| `download.pytorch.org/whl/cpu` | **없음** — aarch64는 **1.13.1까지만**. x86_64/win_amd64뿐 |
| PyPI (기본 인덱스) | **있음** — `torch-2.2.2-cp310-cp310-manylinux2014_aarch64.whl`, torchaudio 동일 |

→ 파이에서 이 줄은 `No matching distribution found`로 죽는다. 고치려면 arm64에서 `--index-url`을
떼면 된다(aarch64엔 CUDA 변종이 없어 PyPI 휠이 이미 CPU 전용).

```dockerfile
RUN case "$(uname -m)" in \
      aarch64) pip install --no-cache-dir torch==2.2.2 torchaudio==2.2.2 ;; \
      *) pip install --no-cache-dir torch==2.2.2 torchaudio==2.2.2 --index-url https://download.pytorch.org/whl/cpu ;; \
    esac
```

**Piper로 가면 이 문제 자체가 없다.** 필요한 휠을 다 확인했다:

| 패키지 | aarch64 휠 |
|---|---|
| `piper-tts==1.6.0` | ✓ `cp39-abi3-manylinux_2_17_aarch64...whl` (espeak-ng 번들 포함) |
| `onnxruntime` | ✓ `manylinux_2_17_aarch64` |

torch도, mecab-ko도, unidic도, transformers도 필요 없다. 5.53GB 이미지를 arm64로 재빌드하는
작업(QEMU 몇 시간)이 통째로 사라지고 605MB 이미지가 아무 데서나 뜬다.
**64비트 OS는 여전히 필수**다.

### S210 — 재빌드 없음. 대신 AVX가 관문이다

x86-64라 지금 이미지가 그대로 돈다. 이게 아이디어패드의 가장 큰 실질 이점이다.

CPU 변종이 Celeron/Pentium이면 **AVX가 아예 없다**(Ivy Bridge에서 AVX는 i3 이상에만 활성).
확인 순서:

```bash
lscpu | grep -oE 'avx[0-9_]*|sse4_2'                        # avx 없으면 Celeron/Pentium
python -c "import onnxruntime; print(onnxruntime.__version__)"       # Piper 경로
python -c "import ctranslate2; print(ctranslate2.get_supported_compute_types('cpu'))"  # STT 경로
python -c "import torch; print(torch.__version__)"                   # MeloTTS 유지 시에만
```

- 실패 모드는 `Illegal instruction (core dumped)`.
- CTranslate2는 `CT2_FORCE_CPU_ISA=GENERIC` 탈출구가 있다. 느려지지만 뜬다.
- onnxruntime은 SSE2까지 폴백하므로 Piper 경로가 가장 안전하다.
- **AVX 유무가 §3의 i3 행과 Celeron 행 차이**다 — Piper 기준 턴 5~10초 vs 7~12초로 좁혀진다
  (MeloTTS면 46초 vs 87초였다). **엔진을 바꾸면 CPU 변종 리스크도 같이 작아진다.**

기타:
- 뚜껑 닫고 서버로 쓰려면 `/etc/systemd/logind.conf`의 `HandleLidSwitch=ignore`.
- 12년 된 배터리는 **팽창 여부를 먼저 확인**해라. 부풀었으면 분리하고 AC 전용. 멀쩡하면 공짜 UPS다.
- Wi-Fi 2.4GHz n뿐이지만 페이로드가 작다(응답 1턴 = 업로드 ~30KB, 다운로드 482KB). 문제없다.

---

## 5. Piper 실측 상세 — 대가가 실제로 얼마인가

day-4.md:11의 "Piper 공식 한국어 음성 없음 → 제외"는 현재 기준 **사실이 아니다**.
`rhasspy/piper-voices`의 `voices.json`에 `ko_KR-kss-medium`이 정식 등재돼 있다
(63.2MB ONNX, 22.05kHz, 화자 1, KSS 데이터셋, LibriTTS-R 파인튜닝, espeak-ng g2p).

이전 문서에서 대가로 셋을 들었다. 실측해보니 **둘은 사라지고 하나가 남았다.**

### (1) viseme 타임라인 — 안 깨진다 (이전 판단이 틀렸다)

Piper 1.6.0에 **`piper.patch_voice_with_alignment`**가 들어 있다. VITS의 `w_ceil`(Ceil 노드
출력 = 음소별 오디오 샘플 수)을 ONNX 그래프 출력으로 승격시키는 공식 도구다.

```bash
python -m piper.patch_voice_with_alignment ko_KR-kss-medium.onnx --output ko_KR-kss-medium-aligned.onnx
# INFO: Marked tensor as output: /Ceil_output_0     (모델 크기 +18 bytes)
```

그 뒤 `PiperVoice.load(..., include_alignments=True)` →
`voice.synthesize(text, include_alignments=True)`가 `AudioChunk`를 내는데, 여기에
`.phonemes` / `.phoneme_ids` / `.phoneme_id_samples` / `.phoneme_alignments`가 실려 온다.
`PhonemeAlignment`는 `.phoneme`(문자열)과 `.num_samples`(정수).

실측 검증 — 4개 문장 전부:

```
phonemes=  88  sum=115200  audio=115200  match=True
phonemes=  93  sum=108288  audio=108288  match=True
phonemes=  70  sum= 76800  audio= 76800  match=True
phonemes= 120  sum=134912  audio=134912  match=True
gcd(num_samples) = 256 samples = 11.6 ms   (MeloTTS: 11.6 ms @44.1kHz)
```

**커버리지 100.00%**(합이 오디오 샘플 수와 정확히 일치), **시간 해상도 11.6ms로 MeloTTS와 동일**.
day-5가 `TTS_VISEME_MIN_SECONDS=0.04`로 잡은 40ms 바닥도 그대로 유효하다.

[tts/app/tts_engine.py](tts/app/tts_engine.py)로의 포팅은 거의 1:1이다:

| 지금 (MeloTTS) | Piper |
|---|---|
| `infer_piece` → `piece_result(samples, symbols, frame_counts)` | `AudioChunk(.audio_float_array, .phonemes, .phoneme_alignments[].num_samples)` |
| `seconds_per_frame = (len(samples)/total_frames)/rate` ([:147](tts/app/tts_engine.py#L147)) | 불필요 — `num_samples`가 이미 샘플 단위. `1/rate`만 곱한다 |
| `len(samples) % total_frames != 0` 크래시 검사 ([:141](tts/app/tts_engine.py#L141)) | `sum(num_samples) == len(audio)` — 더 강한 검사이고 실측 통과 |
| `blank_indices`(add_blank 격자) | 없음 — 빈 집합 |
| 조각 = `split_sentences_into_pieces` → `speech_segment` | 청크 = 문장 단위 → 그대로 대응 |
| MeloTTS 커밋 SHA 고정(내부 어텐션 의존) | **불필요** — `include_alignments`는 공개 API다 |

마지막 줄이 오히려 이득이다. day-5가 감수한 "내부 구현 계약"(README:145의 커밋 SHA 고정, day-5의
심볼 순서 해시 검증 제안)이 **공식 API로 대체된다.**

바뀌는 건 **viseme 매핑 테이블의 키**다. MeloTTS는 한글 자모, Piper는 espeak-ng IPA다.
넓은 코퍼스 8문장에서 뽑은 심볼 목록:

```
distinct symbols: 43
모음  ɐ i ɯ ʌ o ɛ u e ɔ ə ʊ ɪ            (12)
자음  t n h s q ɾ p d m j w ɡ ɫ ŋ ɕ ʃ k ʑ b l r  (21)
표시  ˈ ˌ ʲ ː                            (4, 강세/장음 — 이웃에 흡수)
경계  ␣ - . , ? !                        (6, → 무음 x)
```

43개짜리 테이블이고, **IPA가 이미 조음 위치 기준이라 자모 테이블보다 오히려 쉽다**:
`ɐ ʌ → a` / `ɛ e → e` / `i ɯ ɪ → i` / `o ɔ → o` / `u ʊ → u` / `m p b → m`(양순음) /
나머지 자음 → `n` / 경계 → `x`. day-5가 판단 콜로 남긴 `ᅳ → i`도 `ɯ → i`로 그대로 이어진다.

### (2) g2p 품질 — 대부분 기우였다. 영어 차용어만 MeloTTS 우세

같은 4문장을 두 엔진으로 합성해 **whisper small로 받아쓰고** 원문과 비교했다. ASR은 사람 귀가
아니다 — 발음/g2p 오류에는 민감하고 자연스러움·억양에는 눈이 멀었다. CER보다 **받아쓴 문장 자체**가
근거다.

| | 원문 / 받아쓴 결과 | CER |
|---|---|---|
| **S1 평문** | `네, 확인했습니다. 지금 서울은 흐리고 비가 조금 내리고 있어요.` | |
| melo | 네, 확인했습니다. 지금 서울은 흐리고 비가 조금 내리고 있어요. | **0.0%** |
| piper | 네, 확인했습니다. 지금 서울은 흐리고 비가 조금 내리고 있어요. | **0.0%** |
| **S2 숫자** | `지금 시각은 오후 3시 47분이고, 기온은 영하 2.5도입니다.` | |
| melo | 지금 시각은 오후 3시 47분이고, **비오는 영화 잇스 오도**입니다. | 28.0% |
| piper | 지금 시각은 오후 3시 47분이고 기온은 영하 **1호** 도입니다. | **8.0%** |
| **S3 영어 혼용** | `Claude Code로 GitHub 리포지토리를 클론했습니다.` | |
| melo | **클로드 코드로 환암** 리포지토리를 클론했습니다. | 55.2% |
| piper | **블러코도 그 터브리** 포지토리를 클론했습니다. | 58.6% |
| **S4 장문** | (day-5의 그 문장) | |
| melo | …오후에는 **계속 말가지** 예정이니까… | **8.5%** |
| piper | …흐리고 **귀가** 조금…**우에는 계속** 맑아질…**접이쉬로**… | 11.9% |

읽는 법:
- **평문은 동률**(둘 다 0%). 일상 대화 문장에서 차이가 없다.
- **숫자는 Piper가 낫다**(8% vs 28%). MeloTTS가 "기온은 영하"를 뭉갰다. 예상 밖이었다.
- **영어 차용어는 MeloTTS가 낫다.** CER은 비슷하지만 내용이 다르다 — MeloTTS는 "Claude Code"를
  **"클로드 코드"로 정확히** 읽었고 Piper는 "블러코도"로 뭉갰다. 두 엔진 다 GitHub은 실패해서
  CER이 상쇄됐을 뿐이다. 원인이 분명하다: MeloTTS엔 `melo/text/ko_dictionary.py`의
  `english_dictionary`가 있고 Piper엔 대응물이 없다.
  → **대응책이 싸다**: 우리 서비스 계층에서 Piper에 넘기기 전에 영어 토큰을 한글로 치환한다.
    작은 사전 하나면 되고, 어차피 우리가 통제하고 싶은 지점이다.
- **장문은 MeloTTS가 3.4%p 앞선다.** Piper가 "비가→귀가", "접이식으로→접이쉬로"처럼 흘렸다.
  체감 차이가 있을 수 있는 유일한 항목이다.

Piper가 같은 문장을 **약 25% 빠르게** 말한다(S1 5.0s vs 6.7s, S4 10.7s vs 13.4s).
`length_scale`로 맞출 수 있다.

### (3) 라이선스 — 이건 남는다

**CC BY-NC-SA 4.0** (KSS 데이터셋 상속). 개인 프로젝트엔 무관하지만 **제품화 경로는 막힌다**.
그리고 한국어 음성이 **이거 하나뿐**이라 목소리 선택지가 없다. 상업적 사용이 시야에 들어오면
그때 다시 봐야 하는 항목이다 — 대안은 MeloTTS 유지(MIT) + 서버 배치, 또는 자체 학습
(Piper는 `piper-tts[train]`으로 파인튜닝 경로가 열려 있다).

### MeloTTS를 ONNX로 내보내는 선택지는 이제 매력이 없다

torch 제거로 이미지는 줄지만, 측정이 말해준다 — 시간의 92%가 44.1kHz 보코더고 샘플레이트는
모델이 학습된 값이라 못 낮춘다. int8로 2~3배를 빼도 **RTF ~2**, 파이에서 5초 음성에 10초다.
**Piper가 이미 RTF 0.057인데 들일 이유가 없다.** MeloTTS는 "여력 있는 CPU/GPU에 두는 엔진"으로
남긴다.

### 문장 단위 스트리밍 (day-5 deferred #3)의 순서가 바뀌었다

RTF 5에서는 첫 문장 2초 분량도 10초라 스트리밍이 안 구해줬다. **Piper에서는 전체 합성이 1~2초라
스트리밍의 실익도 같이 사라진다.** deferred #3은 이제 "MeloTTS를 서버에 유지하는 구성"에서만
의미가 있다.

---

## 6. "기기 자체 프론트" (마이크 + 스피커 + 얼굴)

| | RPi4 | S210 Touch |
|---|---|---|
| 화면 | **없음** — 별도 구매 | **11.6" 터치 내장, 0원** |
| 스피커 | 없음 (3.5mm는 PWM 품질) | 내장 |
| 마이크 | **아날로그 입력 자체가 없음** | 내장 (단일, 무지향) |
| 배터리 | 없음 | 있음 (열화 확인 필요) |
| 얼굴 렌더 60fps | VideoCore VI, day-5가 이걸 전제로 설계됨 | HD4000 @1366×768, 여유 |
| **TTS 중 프레임 유지** | 4코어 → **1코어 예약 가능**. Piper는 1코어로도 RTF 0.5 이하 | 2코어 → 예약 불가. Piper면 멈춤이 1~2초로 짧아짐 |
| 소음 | 팬리스 3~7W, **무음** | 부하 시 팬, **마이크 바로 옆** |
| 형태 | 목표 형태 (day-5) | 클램셸 노트북 — 생물처럼 안 보인다 |
| 추가 비용 | 모니터 + 마이크/스피커 + PSU + 케이스 | 0원 (마이크 개선 시 ReSpeaker 4.7만) |

**코어 예약이 결정적이었는데, Piper가 그 압력을 크게 낮췄다.** MeloTTS에서는 4코어를 40초간
100% 먹어 2코어 기기의 얼굴이 통째로 멈췄다. Piper면 파이는 TTS를 1코어에 묶고 3코어를
얼굴·STT에 주면 되고, 2코어 아이디어패드도 멈춤이 1~2초로 줄어 참을 만해진다.
day-5가 "파이가 200ms 멈춰도 발산 안 하게" 넣어둔 시간상수 스무딩이 이제 실제로 커버 가능한
범위 안에 들어온다.

**팬 소음이 마이크로 들어가는 문제는 그대로다.** 상시 청취 기기에서 이건 결함이다.
아이디어패드는 CPU를 쓰는 바로 그 순간 팬이 돌고 내장 마이크가 그 옆에 있다. 다만 Piper로
부하 구간이 40초 → 1~2초로 줄면 팬이 아예 안 돌 가능성도 있다 — **실기에서 확인할 항목**이다.
파이는 팬리스가 가능하고(지속 부하가 짧아져 스로틀링 압력도 함께 감소), 히트싱크 케이스면 충분하다.

**마이크는 두 기기 공통으로 ReSpeaker Lite가 답이다.** USB UAC2라 x86/arm 구분 없이 꽂으면
된다(hardware-notes.md의 결정 그대로). 아이디어패드 내장 마이크는 AEC가 없어 barge-in이 안 되고
팬 소음까지 먹으므로 "0원으로 시작"은 프로토타입 한정이다.

### 비용

| | RPi4 구성 | S210 구성 |
|---|---|---|
| 본체 | **Piper면 4GB로 충분 → 6.5~8만** (8GB 10~12만 불필요, 보유 시 0) | **0원** (보유) |
| 화면 | 공식 7" 터치 ~9만 / 소형 HDMI 4~7만 | 0원 |
| 마이크+스피커 | ReSpeaker Lite 39,900 + 스피커 ~7,000 | 동일 (프로토타입은 0원) |
| 저장 | Piper면 16GB microSD로도 충분 1~2만 | SSD 교체 2.5만 (권장) |
| 전원/케이스 | 2~3만 | 0원 |
| RAM | (본체 포함) | **Piper면 증설 불필요** |
| **합계** | **19 ~ 29만원** | **0 ~ 7.4만원** |

> 파이 본체/화면/주변은 시세 추정. ReSpeaker/스피커는 hardware-notes.md의 실측가.

---

## 7. 권고

**순서가 바뀌었다.** 이전 결론은 "TTS가 병목이니 기기 선택 전에 엔진부터"였는데, 그 엔진 검증이
끝났고 결과가 좋다. 이제 기기를 먼저 정해도 된다.

**단계 1 — TTS를 Piper로 교체한다.** 이게 가장 먼저다. 기기와 무관하게 지금 데스크탑에서도
4.4초 → 0.29초다. 작업 범위:
- `tts/` 엔진 교체 — Dockerfile이 5.53GB에서 605MB로, torch/mecab/unidic 전부 제거.
- [tts/app/tts_engine.py](tts/app/tts_engine.py)의 `infer_piece`를 `AudioChunk`로. §5(1) 대응표대로.
- [tts/app/viseme.py](tts/app/viseme.py)의 자모 테이블 → IPA 43심볼 테이블.
- 서비스 계층에 영어→한글 치환 사전 (§5(2)).
- `/speak` 응답 계약(`audio_base64`/`visemes`/`segments`/`envelope`)은 **무변경**. 프론트도 무변경.
- 모델 패치(`patch_voice_with_alignment`)를 이미지 빌드 단계에 넣는다.

**단계 2 — 아이디어패드를 상시 박스로 세운다.** x86이라 재빌드가 없고, Piper면 SSD 교체 없이도
시작할 수 있다(63MB 모델이라 HDD 콜드 스타트가 무의미해짐). 턴 지연 5~12초. 선행 확인은
CPU 변종/AVX(§4)뿐이다. 여기서 전체 루프를 실사용해보며 파이로 갈 준비를 한다.

**단계 3 — 최종 기기는 RPi4.** day-5의 목표 형태 그대로. **Piper 전환 후에는 4GB로 충분**하고,
1코어에 TTS를 묶어도 실시간 이하라 얼굴에 3코어를 줄 수 있다. 팬리스 무음이 상시 청취 기기의
결정적 이점이다. 아이디어패드는 그때도 **x86 회귀 벤치**로 남긴다 — 같은 리눅스인데 재빌드가 없다.

**MeloTTS를 지키고 싶다면** 남는 길은 하나다: TTS만 데스크탑/서버에 두고 기기는 클라이언트로.
`voice/`와 `tts/`가 URL로 주소지정되는 독립 서비스라 프록시 타깃만 바꾸면 된다(day-2/day-4
설계 의도). 파이 기준 턴 지연 ~11초로 Piper 온디바이스와 비슷한데, **데스크탑이 항상 켜져 있어야
한다** = 자율 기기가 아니다. 장문 품질 3.4%p와 MIT 라이선스를 그 대가로 살 만한지가 판단 지점이다.

---

## 8. 실기에서 재야 할 것

§1·§5는 실측이 끝났다. 남은 건 **§2의 코어당 배수 추정**이고, 두 기기에서 아래를 돌리면
§3의 투영이 실측으로 바뀐다. 조건이 §1과 동일하므로 숫자를 바로 비교할 수 있다.

```bash
# 1) CPU 능력 (S210에서 특히 중요)
lscpu | grep -E 'Model name|^CPU\(s\)|Thread'; lscpu | grep -oE 'avx[0-9_]*|sse4_2' | sort -u
free -g

# 2) TTS — Piper. 605MB 이미지라 기기에서 직접 빌드해도 몇 분이다.
docker build -t piper-bench .                      # piper-tts==1.6.0 + onnx
docker run --rm -v piper_voices:/voices piper-bench \
  python -m piper.download_voices ko_KR-kss-medium --data-dir /voices
docker run --rm -v piper_voices:/voices piper-bench \
  python -m piper.patch_voice_with_alignment /voices/ko_KR-kss-medium.onnx \
    --output /voices/ko_KR-kss-medium-aligned.onnx
for N in 1 2 4; do
  docker run --rm --cpus=$N -e BENCH_THREADS=$N -e USE_ALIGNED=1 \
    -v piper_voices:/voices -v "$PWD:/app" piper-bench python /app/bench_piper.py
done

# 3) STT — 6.7초 한국어 WAV로 1/2/4 스레드
python bench_stt.py sample_ko.wav
```

> `bench_piper.py` / `bench_stt.py` / `phonemes.py` / `roundtrip.py`와 생성된 WAV들은 아직
> 스크래치패드에만 있고 커밋되지 않았다. 실기에 들고 갈 거면 `bench/`로 옮긴다.

같이 확인할 것:
- 파이: Chromium 키오스크 `#/face` 프레임 예산 — **TTS를 1코어에 묶은 상태에서** (day-5:313의
  미확인 항목). Piper 전환 후엔 통과할 가능성이 높지만 실측 대상이다.
- 파이: 히트싱크 없이 지속 부하 스로틀링 — Piper면 부하 구간이 짧아 압력이 크게 준다.
- S210: **Piper 부하(1~2초)에서 팬이 도는지** — 안 돌면 내장 마이크 구성이 살아난다.
- S210: 배터리 팽창 여부, HDD SMART.
- 양쪽: ReSpeaker Lite USB AEC 실사용 품질 (hardware-notes.md:134의 미확인 항목).
- **Piper 음질을 귀로 확인** — ASR 라운드트립은 억양·자연스러움에 눈이 멀었다. §5(2)의 4문장
  WAV를 직접 들어보고 장문 품질 저하가 실제로 거슬리는지 판단해야 한다. 이게 엔진 교체 결정의
  마지막 관문이다.

## 미룬 것 (deferred)

통합(커밋 `85ebd73`)에서 의식적으로 안 한 것부터:

- **바닥값을 낮추는 대신 닫힌 입을 보호하는 쪽을 안 골랐다.** Piper에서 양순음이 40ms 바닥에
  걸려 사라지는 걸 `TTS_VISEME_MIN_SECONDS`를 엔진별로(0.02) 주는 것으로 풀었다. 더 원칙적인
  대안은 `absorb_shortest`가 **닫힌 입(`m`) 구간은 절대 흡수하지 않게** 하는 것이다 — 닫힌
  입은 지각적으로 가장 두드러지는 입모양이라 짧은 깜빡임보다 사라지는 쪽이 나쁘다. 안 한 이유:
  공유 코드의 의미를 바꾸는 일이라 MeloTTS 쪽 회귀까지 같이 봐야 하는데, 지금 melo에서는
  양순음이 전부 58ms 이상이라 당장 문제가 없다. 착수 지점:
  [tts/app/viseme.py](tts/app/viseme.py)의 `absorb_shortest`.
- **Piper의 단어 경계 `x` 구간을 그대로 뒀다.** 바닥을 0.02로 내리면서 espeak의 공백 심볼
  (`' '`, 23.2ms)이 살아남아 문장 중간에 짧은 쉼이 생긴다 — 같은 문장에서 스팬이 melo 40개 대
  piper 47개다. 자모 스트림엔 공백 토큰이 아예 없어서 melo는 문장 중간에 입을 쉬지 않는다.
  단어 사이에 입이 잠깐 쉬는 게 자연스럽다고 보고 뒀지만 실측이 아니라 판단이다. 얼굴에서
  거슬리면 `' '`를 `ipa_transparent`로 옮긴다. 착수 지점: [tts/app/viseme.py](tts/app/viseme.py)의
  `ipa_silence`.

Piper 자체에 대해 남은 것:

- **음질을 귀로 판단하지 않았다.** ASR 라운드트립(§5-2)은 g2p 오류에 민감하지만 억양·자연스러움엔
  무력하다. 특히 S4 장문에서 Piper가 3.4%p 뒤진 게 체감상 어느 정도인지 모른다.
  착수 지점: 생성해둔 8개 WAV(scratchpad `piper/out/`, `melo_out/`)를 나란히 재생.
- **`length_scale` 튜닝을 안 했다.** Piper가 25% 빠르게 말한다. day-5의 자막 스윕이 발화 속도
  3.8~8.0자/s 대역을 전제하므로 함께 재검증이 필요하다. 착수 지점: `SynthesisConfig.length_scale`.
- **영어 치환 사전을 안 만들었다.** §5(2)에서 Piper의 유일한 명확한 약점으로 확인됐고 대응책도
  분명하다. 착수 지점: MeloTTS의 `melo/text/ko_dictionary.py::english_dictionary`를 참고 삼아
  우리 서비스 계층에 둔다(엔진 밖이라 나중에 엔진을 또 바꿔도 살아남는다).
- **코어당 배수는 여전히 이론값이다.** int8/A72 배수만 hardware-notes의 정성 기술과 역산해
  보정했다. §8을 돌리기 전까지 §3의 절대 숫자는 ±40%로 본다. 다만 Piper의 여유가 워낙 커서
  (RTF 0.057, 1코어 0.095) 이 오차로는 "두 기기 다 실시간 이하"라는 결론이 뒤집히지 않는다.
- **`ko_KR-kss-medium` 외 대안을 안 봤다.** NC 라이선스가 걸림돌이 되면 자체 파인튜닝
  (`piper-tts[train]`)이 열려 있으나 데이터셋·시간 비용을 산정하지 않았다.
