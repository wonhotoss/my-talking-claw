# Day 6: TTS 엔진 교체 (MeloTTS → Piper), 그리고 기기 선택의 근거

## 목표

"이 시스템을 라즈베리파이4와 IdeaPad S210에서 돌리면 어느 쪽이 나은가"로 시작했는데,
재보니 **질문이 틀렸다**. 기기가 아니라 TTS 엔진이 병목이었다. day-6은 그 측정과, 거기서 나온
엔진 교체다.

## 측정 — 기기가 아니라 엔진이었다

컨테이너를 `docker run --cpus=N`으로 묶어 코어 수 효과만 분리했다(Ryzen 3 3300X, Zen2 4C/8T).
문장은 `"네, 확인했습니다. 지금 서울은 흐리고 비가 조금 내리고 있어요."`, 워밍업 후 3회 중앙값.

| 코어 | MeloTTS | RTF | Piper `ko_KR-kss-medium` | RTF | 배수 |
|---|---|---|---|---|---|
| 1 | (17.5s, Amdahl 외삽) | 2.61 | **494.6 ms** | **0.095** | **27×** |
| 2 | 10,210 ms | 1.53 | **333.5 ms** | **0.067** | **23×** |
| 4 | 6,546 ms | 0.98 | **287.1 ms** | **0.057** | **17×** |

**MeloTTS는 이 데스크탑에서 이미 RTF 0.98이다.** 코어당 성능 배수를 곱하면 파이 33~46초,
아이디어패드 33~49초(Celeron이면 71~92초). 어느 기기를 골라도 못 쓴다.

시간이 어디로 가는지 갈라봤다(3.88초 오디오, 4스레드):

| 단계 | 시간 | 비중 |
|---|---|---|
| g2p + BERT (`kykim/bert-kor-base`) | 306 ms | 8% |
| **VITS `infer` (디코더/보코더)** | **3,550 ms** | **92%** |

BERT가 범인이 아니라 **44.1kHz 보코더**다. Piper medium은 22.05kHz — 출력 샘플이 절반이고,
이게 배수의 큰 축이다. 그래서 "MeloTTS를 ONNX로 내보내서 2~3배" 같은 최적화로는 못 넘는다.

Amdahl로 깨끗하게 맞는다(Zen2-코어-초): `MeloTTS T(n) = 2.89 + 14.64/n`. **직렬분 2.9초**는
코어를 아무리 늘려도 안 내려간다.

> day-4.md:11의 "Piper: 공식 한국어 음성 없음 → 제외"는 현재 기준 **사실이 아니었다**.
> `rhasspy/piper-voices`의 `voices.json`에 `ko_KR-kss-medium`이 정식 등재돼 있다.
> day-N은 이력이라 손대지 않고 [platform-notes.md](platform-notes.md)에 근거와 함께 적었다.

## 대가 셋 중 둘이 실측으로 사라졌다

Piper를 제외했던 이유를 하나씩 재봤다.

### 1. viseme 타임라인 — 안 깨진다

Piper 1.6.0에 **`piper.patch_voice_with_alignment`**가 있다. VITS의 `w_ceil`(음소별 오디오
샘플 수)을 ONNX 그래프 출력으로 승격시키는 공식 도구다. 그 뒤
`PiperVoice.load(..., include_alignments=True)`가 `AudioChunk.phoneme_alignments`를 채운다.

4개 문장 전부 검증:

```
phonemes= 88  sum=115200  audio=115200  match=True
phonemes= 93  sum=108288  audio=108288  match=True
phonemes= 70  sum= 76800  audio= 76800  match=True
phonemes=120  sum=134912  audio=134912  match=True
gcd(num_samples) = 256 samples = 11.6 ms   (MeloTTS: 11.6 ms @44.1kHz)
```

커버리지 **100.00%**, 시간 해상도가 MeloTTS와 **동일**하다. 오히려 이득인 지점이 있다 —
day-5가 감수한 내부 구현 의존(`SynthesizerTrn.infer`의 어텐션, 그래서 커밋 SHA 고정)이
**공개 API로 대체된다**.

### 2. g2p 품질 — 대부분 기우, 영어 차용어만 약점

같은 4문장을 두 엔진으로 합성해 whisper small로 받아쓰고 원문과 비교했다. ASR은 사람 귀가
아니다 — 발음/g2p 오류엔 민감하고 억양·자연스러움엔 눈이 멀었다.

| | melo CER | piper CER | 읽는 법 |
|---|---|---|---|
| 평문 | **0.0%** | **0.0%** | 동률. 일상 문장에서 차이 없음 |
| 숫자 | 28.0% | **8.0%** | Piper 우세(예상 밖). melo가 "기온은 영하"를 뭉갬 |
| 영어 혼용 | 55.2% | 58.6% | CER은 비슷하나 **내용이 다르다** — melo는 "Claude Code"를 "클로드 코드"로 정확히 읽고 Piper는 "블러코도"로 뭉갠다. 둘 다 GitHub에서 실패해 상쇄됐을 뿐 |
| 장문 | **8.5%** | 11.9% | melo가 3.4%p 앞선다. 유일하게 체감 차이가 있을 수 있는 항목 |

영어 약점은 원인이 분명하다: melo엔 `melo/text/ko_dictionary.py`의 `english_dictionary`가 있고
Piper엔 대응물이 없다. 서비스 계층 치환 사전으로 대응 가능(아래 deferred).

### 3. 라이선스 — 이건 남는다

**CC BY-NC-SA 4.0**(KSS 데이터셋 상속). 개인 프로젝트엔 무관하지만 제품화 경로는 막힌다.
한국어 음성이 이거 하나뿐이라 목소리 선택지도 없다.

## 아키텍처 — 코드는 하나, 이미지는 둘

질문은 "tts 모듈을 melo 전용으로 두고 piper용을 따로 만들 것인가"였다. 전제가 반만 맞았다.

**"엔진을 스위칭하면 빌드 의존성이 배가된다"는 이미지가 하나일 때만 참이다.** 그리고 의존성은
이미 Dockerfile에만 있었다 — `tts_engine.py`는 melo를 lazy import하고(독스트링에 이유까지
적혀 있다), `viseme.py`는 torch/melo 프리를 명시하고, `pyproject.toml`은 엔진을 락에서 뺐다.
day-4가 "이전 가능한 별도 서버"를 위해 만들어둔 이음매가 그대로 쓸 수 있는 상태였다.

복제했다면 `server.py`(117줄, 엔진 종속 0) + `viseme.py` 알고리즘(~288줄) +
`test_server.py`(103줄, 엔진을 monkeypatch로 가짜화)까지 **450~550줄이 두 벌**이 됐다.
`merge_spans`의 연속성 불변식, `absorb_shortest`의 편향, `snap_contiguous`의 ulp 보정 —
day-5가 "조용히 깨질 뻔한 것들"이라 부른 부분들이다.

엔진 차이를 전수 확인했더니 **전부 기존 이음매 아래에 떨어졌다**. `server.py`, `merge_spans`,
`rms_envelope`, `/speak` 스키마에 닿는 게 하나도 없다.

```
phone / RPi monitor → frontend
  ├ /api/*   → backend :8000 → agent gateway :3000
  ├ /voice/* → STT service :8100 (faster-whisper)
  └ /tts/*   → TTS service   melo :8200  |  piper :8201     ← 같은 app/, 같은 계약
```

## 구현한 것

### `tts/app/speech.py` (신규) — 엔진 무관한 절반

계약 타입(`speech_result`/`speech_segment`/`speech_piece`) + `build_speech()` 오케스트레이션
(오프셋, 조각 간 무음, 자막 segments, WAV 인코딩, viseme 병합, RMS envelope) + `speech_engine`
베이스. `viseme.py`와 같은 성질을 유지한다 — torch/melo/piper를 안 물어서 어디서든 임포트되고
단위 테스트된다.

**정렬 단위를 샘플로 통일**한 게 핵심이다. Piper는 `num_samples`를 직접 주고, MeloTTS의 mel
프레임은 정수배로 변환된다(변환과 드리프트 크래시 검사는 melo 어댑터에 남는다 — 엔진별 지식이
있는 곳이라). 덕분에 `build_spans`가 두 엔진에 그대로 쓰인다.

### `tts/app/viseme.py` — 테이블이 파라미터로

`symbol_table`(vowels/closed/silence/transparent)을 도입하고 모듈 전역 대신 인자로 받는다.
자모 표와 espeak IPA 표 **둘 다 여기 산다** — 순수 데이터고 입모양 판단 근거가 모여 있는 곳이라.
`frame_counts`/`seconds_per_frame` → `sample_counts`/`sample_rate`로 정리.

IPA 표는 **실측 인벤토리 43심볼**이다(21 jungseong 전부 + 두 활음 계열 + 종성 + 숫자 + 영어
차용어 + 문장부호를 덮는 코퍼스에서 뽑았다). IPA가 이미 조음 위치 기준이라 자모 표보다 오히려
쉽다 — `ɐ ʌ ə → a`, `ɛ e → e`, `i ɯ ɪ → i`, `o ɔ → o`, `u ʊ → u`, `p b m → m`.
day-5가 판단 콜로 남긴 `ᅳ → i`도 `ɯ → i`로 그대로 이어진다.

### `tts/app/engines/{melo,piper}.py` + 팩토리

둘 다 모델을 lazy import해서 상대 엔진이 없는 이미지에서도 모듈 트리가 로드된다.
`TTS_ENGINE`으로 고른다. 어댑터가 하는 일은 좁다 — 문장 조각 + 음소 심볼 + 심볼별 샘플 수.

### `tts/Dockerfile.{melo,piper}` + compose 두 서비스

piper는 멀티스테이지다. voice 스테이지에서 모델을 받아 `patch_voice_with_alignment`를 돌리고,
런타임 이미지엔 `onnx`(~50MB, 패치에만 필요)를 안 싣는다. 패치 안 된 모델을 물리면 `/speak`이
502로 거절한다 — 타임라인이 조용히 사라지는 것보다 낫다.

| | melo | piper |
|---|---|---|
| 이미지 | 5.53 GB | **649 MB** |
| 모델 | HF 캐시 ~0.65GB(볼륨) | **63 MB, 이미지에 내장** |
| RSS | 2.3~2.5 GB | **252 MB** |
| 콜드 스타트 | 24.7 s | **1.0~1.3 s** |
| arm64 빌드 | **안 됨**(아래) | 그대로 됨 |

## Piper 통합에서 실제로 걸린 것

넷 다 코드 읽기로는 안 나왔고 실행해보고 나왔다.

1. **`AudioChunk`에 text 필드가 없다.** `phonemes`는 있는데 그게 어느 문장에서 나왔는지가
   없다 → 자막 `segments`를 못 채운다. 문장 분할을 어댑터가 직접 하고, espeak가 더 쪼개면
   청크를 되붙여 매핑을 유지한다.
2. **`SynthesisConfig.normalize_audio` 기본값이 `True`**고 **문장마다 독립 피크 정규화**를 한다.
   그대로 두면 문장 간 음량이 튀고, `envelope`이 실제 음량과 분리된다 — 얼굴 입 벌림이 그 값을
   쓴다. 게다가 `rms_envelope`이 이미 발화 단위로 정규화하니 이중이다. 껐다.
3. **espeak IPA에서 `-`는 단어 내부 분절 구분자다**(했습 → `t - s -`, 학교 → `k -`).
   자모 표에선 같은 글자가 구두점이라 silence인데, 여기서 silence로 두면 **단어 중간에 입이
   쉰다**. transparent로 간다 — `~`와 같은 판단, 같은 이유.
4. **`TTS_VISEME_MIN_SECONDS`를 piper만 0.02로.** 40ms 그대로 뒀더니 "만나서 반갑습니다"에서
   닫힌 입이 **4개 중 1개만** 남았다. 원인은 테이블이 아니라 길이였다 — espeak가 `m`/`p`를
   4곳 다 내는데 **23/35/35/46ms**(melo는 58/104/58/70ms)라 바닥에 걸려 `absorb_shortest`가
   먹었다. 이 목소리가 25% 빠르게 말하는 게 그대로 드러난 것.

## 조용히 깨질 뻔한 것들

- **`frontend/vite.config.js`가 `.ts`보다 우선한다.** 셋(`ts`/`js`/`d.ts`)이 다 커밋돼 있고
  Vite의 config 탐색 순서는 `.js`가 먼저다. `.ts`만 고치면 **아무 일도 안 일어난다**.
  day-5가 "`tsc -b`가 커밋된 `vite.config.js`를 재생성하는 함정"이라 적어둔 그것을 실제로
  밟았다. README에 명시했다.
- **`Dockerfile.melo`는 라즈베리파이(arm64)에서 빌드가 안 된다.** 고정한
  `download.pytorch.org/whl/cpu` 인덱스에 torch 2.2.2 aarch64 휠이 없다(1.13.1까지만).
  PyPI엔 있다. Piper로 가면 이 문제 자체가 사라진다 — `piper-tts` 1.6.0과 `onnxruntime` 둘 다
  aarch64 휠이 있다.
- **melo의 조각 간 무음은 speed로 나뉜다**(`int((rate * 0.05) / speed)`, 업스트림 verbatim).
  공유 오케스트레이션으로 옮기면서 이 나눗셈을 흘릴 뻔했다. 어댑터에서 초 단위로 미리 나눈다.

## 검증

### 유닛 (Windows, 엔진 없이)

**78 passed** (backend 3 / agent-gateway 6 / voice 5 / **tts 64**), 프론트 빌드 성공.
tts는 24 → 64로 늘었고 추가분은 IPA 인벤토리 커버, 테이블 배타성(한 심볼이 두 그룹에 안 들어감),
`speech.py` 오케스트레이션(타일링·드리프트 크래시·blank 배선·WAV 포맷)이다.

### melo 회귀 — 시퀀스가 완전히 동일

리팩터 전/후 컨테이너에 같은 문장으로 `/speak`:

```
before: x e x a i e i m i a x i m a u i o m i a o i m e i o i a o x m a m a m i m i a x
after:  x e x a i e i m i a x i m a u i o m i a o i m e i o i a o x m a m a m i m i a x
```

40스팬 완전 일치, 세그먼트·최단 스팬(46.4ms) 동일. duration만 다른 건 duration predictor가
확률적이라 예상된 것.

### piper `/speak` 계약

스팬 연속성 **정확** 일치(근사 아님), 첫 `start == 0`, 마지막 `end == duration ==` 디코드한
WAV 길이, envelope 길이 `ceil(duration × 50)` · 피크 정규화 · 0..1, segments 타일링 및 원문
복원, 입모양이 8종 집합 이내. 전부 통과.

### 양순음 배치

`만나서 반갑습니다` — melo 4곳 / piper 4곳, 시퀀스도 근사:

```
melo : x m a m a m i m i a x      (11 스팬)
piper: x m a m a m a i m i a x    (12 스팬)
```

### 의존성 분리

piper 이미지에 `torch` 없음, melo 이미지에 `piper` 없음. 이미지를 나눈 목적이 실제로 지켜진다.

### 실기 — 전체 루프

| 홉 | melo | piper |
|---|---|---|
| STT (`/voice/transcribe`, 6.73s WAV) | 869 ms (RTF 0.129) | 동일 |
| 에이전트 (`claude -p`) | 6,929 ms | 5,889 ms |
| TTS | 9,735 ms (RTF 1.023) | **982 ms (RTF 0.156)** |
| **턴 합계** (녹음/재생 제외) | **16.7 s** | **6.9 s** |

같은 문장으로 TTS만 A/B(melo가 낸 응답을 양쪽에 그대로):

| | 합성 | 오디오 | RTF | 샘플레이트 | viseme |
|---|---|---|---|---|---|
| melo | 7,631 ms | 9.48 s | 0.805 | 44,100 Hz | 30 |
| **piper** | **453 ms** | 6.56 s | **0.069** | 22,050 Hz | 38 |

**17배.** TTS가 턴의 58% → 14%로 내려가고 이제 **에이전트 왕복이 병목**이다.
기기로 옮겨도 이 부분은 안 변한다(망 대기).

**사용자 확인: 확실히 빠르고 품질은 용인 가능한 수준.** → 프론트 기본을 piper로 전환.

## 기기 선택 — 이제 답할 수 있다

Piper 전환으로 투영이 바뀐다([platform-notes.md](platform-notes.md) §3):

| | RPi4 | S210 / i3 | S210 / Celeron |
|---|---|---|---|
| 턴 지연 (MeloTTS) | ~44 s | ~46 s | ~87 s |
| **턴 지연 (Piper)** | **6~11 s** | **5~10 s** | **7~12 s** |
| RAM 요구 | 8GB → **4GB** | 8GB → **4GB** | 동일 |

파이에서 **TTS를 1코어에 묶어도 RTF 0.49~0.78**로 실시간 이하다. 나머지 3코어를 STT와 얼굴
렌더에 줄 수 있다 — MeloTTS로는 상상할 수 없던 여유고, day-5의 60fps 얼굴이 실제로 가능해지는
조건이다. 최종 기기는 **RPi4**(4GB로 충분), 아이디어패드는 x86 회귀 벤치로.

## 남은 확인 (수동, 실제 디바이스)

- **iPhone Chrome LAN 엔드투엔드** — piper로 재확인. 재생 중 `#/face` ↔ `#/` 왕복해서 소리가
  안 끊기는지(day-4에서 두 번 깨졌던 iOS 오디오 언락 경로).
- **발화 속도** — piper가 25% 빠르다. day-5의 자막 스윕이 3.8~8.0자/s 대역을 전제하는데
  벗어나면 하이라이트가 앞서간다.
- **단어 사이 짧은 쉼** — viseme이 30 → 38로 는 건 espeak 공백 심볼(23ms)이 살아남아서다
  (melo엔 공백 토큰이 없어 문장 중간에 입을 안 쉰다). 자연스러운지가 판단 지점.
- **영어 차용어** — 실측에서 확인된 유일한 명확한 약점. "Claude Code" 같은 걸 시켜보면 바로
  드러난다.
- 파이 실기: Chromium 키오스크 부팅, TTS를 1코어에 묶은 상태의 프레임 예산.

## 미룬 개선 (deferred)

### 1. 영어→한글 치환 사전 — 확인된 유일한 명확한 약점

**현재**: Piper가 "Claude Code"를 "블러코도"로 읽는다. melo는 `english_dictionary`로 정확히 읽는다.
**왜 안 했나**: 엔진 교체 자체를 먼저 검증하고 싶었고, 대응책이 분명해서 급하지 않았다.
**착수 지점**: `melo/text/ko_dictionary.py::english_dictionary`를 참고 삼아 **우리 서비스
계층**에 둔다 — 엔진 밖이라 나중에 엔진을 또 바꿔도 살아남는다.

### 2. `length_scale` 튜닝

**현재**: `TTS_SPEED=1.0`. Piper가 같은 문장을 25% 빠르게 말한다(6.56s vs 9.48s).
**착수 지점**: `SynthesisConfig.length_scale`(어댑터의 `synthesis_config`). 0.85 근처부터.
day-5의 자막 스윕 검증을 같이 돌려야 한다.

### 3. 닫힌 입 보호 vs 바닥값 낮추기

**현재**: `TTS_VISEME_MIN_SECONDS`를 엔진별로(piper 0.02) 줬다.
**대안**: `absorb_shortest`가 닫힌 입(`m`)은 절대 흡수하지 않게 한다. 닫힌 입은 지각적으로
가장 두드러져서 짧은 깜빡임보다 사라지는 쪽이 나쁘다 — 더 원칙적이다.
**왜 안 했나**: 공유 코드의 의미를 바꾸는 일이라 melo 회귀까지 같이 봐야 하는데, melo에서는
양순음이 전부 58ms 이상이라 당장 문제가 없다.
**착수 지점**: [tts/app/viseme.py](tts/app/viseme.py)의 `absorb_shortest`.

### 4. 단어 경계 `x` 구간

**현재**: 바닥을 0.02로 내리면서 espeak 공백 심볼이 살아남는다.
**대안**: `' '`를 `ipa_silence`에서 `ipa_transparent`로 옮긴다.
**왜 안 했나**: 단어 사이에 입이 잠깐 쉬는 게 자연스럽다고 봤는데, 실측이 아니라 판단이다.

### 5. 음질을 귀로 정밀 비교하지 않았다

**현재**: 사용자가 "용인 가능"으로 확인했지만, ASR 라운드트립은 억양·자연스러움엔 무력하다.
특히 장문에서 melo가 3.4%p 앞선 게 체감상 어느 정도인지는 별도 판단이 필요하다.
**착수 지점**: 두 엔진이 8200/8201에 동시에 떠 있으므로 같은 문장을 번갈아 치면 된다.

### 6. NC 라이선스

**현재**: `ko_KR-kss-medium`은 CC BY-NC-SA 4.0. 제품화 경로가 막힌다.
**대안**: MeloTTS 유지(MIT) + 서버 배치, 또는 `piper-tts[train]`으로 자체 파인튜닝.
데이터셋·시간 비용은 산정 안 했다.

### 7. 프록시 타깃 전환이 파일 두 개를 손으로 맞춰야 한다

**현재**: `vite.config.ts`와 `vite.config.js`를 동시에 고쳐야 A/B가 전환된다.
**대안**: 환경변수(`VITE_TTS_TARGET`)로 빼거나, 커밋된 `vite.config.js`/`.d.ts`를 추적에서
빼고 `.ts` 하나만 남긴다. 후자가 근본적이지만 `tsc -b` 설정을 건드려야 한다.
**왜 안 했나**: 범위 확장이라 물어보고 하기로 했다.

### 8. day-5에서 넘어온 것들

`word2ph` 음절 단위 자막, HF 리비전 고정(melo 한정), 전송량(Opus). 문장 단위 스트리밍은
**의미가 없어졌다** — 전체 합성이 1초 안쪽이라 첫 소리까지 시간을 더 줄일 여지가 없다.
day-4부터 최우선이던 "TTS 지연"은 이번에 닫혔다.
