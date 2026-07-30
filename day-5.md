# Day 5: 말하는 얼굴 (viseme 립싱크)

## 목표

최종 배포 형태는 **자체 모니터를 단 라즈베리 파이**다. 그 화면에 점·선으로 그린 간단하고 귀여운
얼굴을 풀스크린으로 띄우고, 에이전트가 말할 때 **음성에 맞춰 입이 움직이게** 한다.

핵심 질문: **시간대별 실제 입모양(a/e/i/o/u)까지 갈 수 있는가, 아니면 발성 타이밍에 맞춰 입만
뻥긋할 수밖에 없는가.** 음성 입출력이 아직 전부 웹 프론트에 있으므로 얼굴도 웹 프론트에 먼저
붙여 가능성을 검증했다.

**결론: 진짜 viseme이 된다. 추가 모델 0, 추가 추론 비용 0.**

## 왜 되는가

MeloTTS는 VITS 계열이고, `melo/models.py`의 `SynthesizerTrn.infer`가
`w_ceil = torch.ceil(w)` → `attn = commons.generate_path(w_ceil, attn_mask)` 를 계산한 뒤
`return o, attn, y_mask, (...)` 로 **정렬 행렬을 이미 내보내고 있다.** 그런데
`melo/api.py`의 `tts_to_file`이 `...infer(...)[0][0, 0]` 으로 **오디오만 뽑고 나머지를 버린다.**

- `generate_path`의 docstring이 `path: [b, 1, t_y, t_x]` 를 명시 → `attn.sum(2)`가 **토큰별 프레임 수**.
- MeloTTS-Korean `config.json`: `sampling_rate 44100`, `hop_length 512` → **1프레임 = 11.61 ms**.
  (코드는 `model.hps.data`에서 읽고 하드코딩하지 않는다.)
- `length_scale = 1./speed`가 `ceil` **이전에** 곱해지므로 `TTS_SPEED`는 이미 반영돼 있다.
- 한국어 음소가 **한글 자모**라(`'하늘' → ['ᄒ','ᅡ','ᄂ','ᅳ','ᆯ']`), 초성/중성/종성이 서로 다른
  코드포인트다 → 중성만 골라내면 그게 곧 모음 viseme.

즉 필요한 건 새 모델이 아니라 **버려지던 값을 줍는 것**뿐이었다.

## 아키텍처

```
phone / RPi monitor → frontend :5173
  ├ /api/*   → backend :8000 → agent gateway :3000
  ├ /voice/* → STT service :8100 (faster-whisper)
  └ /tts/*   → TTS service :8200 (MeloTTS-Korean, Docker)
       ├ POST /synthesize  audio/wav                        [기존, 무변경]
       └ POST /speak       JSON: audio + viseme 타임라인      ← 신설

frontend
  ├ /        음성 콘솔 (기존 타임라인 UI)
  └ #/face   풀스크린 얼굴 (립싱크)                            ← 신설
```

프록시는 손대지 않았다. `/tts` 프리픽스가 이미 8200으로 접두사를 벗겨 전달한다.

## 구현한 것

### `tts/app/viseme.py` (신규) — 순수 로직

torch도 melo도 import하지 않아 Windows에서 그대로 테스트된다. numpy만 쓴다.

- **모음 21개 → viseme**. 입술로 보이는 것(개구도·원순성·좌우 벌림)만 기준으로 한다. 혀 위치는
  안 보이므로 서로 다른 모음이 같은 viseme을 공유한다.
  - `ᅳ → i`. /ɯ/는 /u/의 **비원순** 짝이다. `u`로 매핑하면 은/는/를마다 입을 오므리게 되는데,
    그게 한국어의 절반이다. 한국어 립싱크에서 가장 흔한 버그.
  - `ᅥ → a`. /ʌ/는 비원순 중개모음이라 `e`로 보내면 입이 잘못 옆으로 벌어진다. 아/어가
    똑같이 보이는 건 감수 — 상용 viseme 세트도 대부분 그렇게 한다.
  - 이중모음·활음은 **핵모음**을 따른다(`ᅪ → a`, `ᅬ → e`, `ᅱ → i`). 활음 구간은 토큰
    앞 20~30%뿐이라 활음을 따르면 나머지 내내 입이 틀린다.
- **자음은 조음결합**. 양순음 `ᄆ ᄇ ᄈ ᄑ ᆷ ᆸ`만 `m`(닫힘)을 강제하고, 나머지는 **가장 가까운
  모음**의 모양을 물려받는다(동점이면 뒤쪽 = 예측적 조음결합).
  - "뒤 모음을 따른다"가 아니라 "가장 가까운"인 게 중요하다. 한글 → `ᄒ ᅡ ᆫ ᄀ ᅳ ᆯ`에서
    종성 `ᆫ`이 뒤를 따르면 다음 음절의 ㅡ로 **한 음절 먼저 튄다**. 가장 가까운 쪽을 쓰면
    종성은 제 음절 모음을, 초성은 제 음절 모음을 가져간다.
  - 감사합니다 → `a m a m i a`. ㅁ 두 곳에서 정확히 입이 닫힌다.
- **blank는 인덱스로 식별한다.** `add_blank`가 끼워 넣는 blank의 id는 0이고 그건 `"_"`로
  디코드되는데, 진짜 앞뒤 pad 토큰과 문자 그대로 구별이 안 된다. 위치(짝수 인덱스)로만 안다.
- **모르는 심볼은 크래시가 아니라 투명 처리**한다. 심볼 표는 모델 `config.json`에서 오고
  (`TTS.__init__`의 `symbols = hps.symbols`) **다국어 통합 220개**다. 타임라인은 겉모습일 뿐이라
  입모양 하나 때문에 발화 전체를 502로 죽이는 게 더 나쁘다. `AGENTS.md`의 "이른 크래시" 원칙에서
  의도적으로 벗어난 지점이고, 대신 **한국어 인벤토리 전수 커버를 테스트로 고정**했다.
- **후처리**: 인접 동일 viseme 병합 → 40ms 미만 구간은 더 **긴** 이웃에 흡수 → 경계 스냅 →
  마지막 `end`를 오디오 길이에 맞춤.

### `tts/app/tts_engine.py` — `speak()`가 새 기본형

`tts_to_file`의 루프를 그대로 인라인했다. 인라인하는 유일한 이유가 `attn`을 잡는 것이므로,
문장 분할·추론 인자·문장 간 무음·샘플레이트·WAV subtype은 **전부 업스트림과 동일**하게 뒀다.

- `synthesize(text)`는 `self.speak(text).audio` 한 줄로 위임 → `/synthesize` 계약과 기존 테스트
  무변경.
- **`attn`은 device에서 줄여서 가져온다**: `attn.sum(2)[0,0]...cpu()`. 전체 행렬은 문장당
  ~600KB이고, `cuda`에서는 문장마다 sync + 전송이 된다.
- 문장 조각마다 `int(sr * 0.05 / speed)` 무음을 붙인다 — `audio_numpy_concat`과 동일. **마지막
  조각 뒤에도 붙는다**. 이걸 빼먹으면 타임라인이 통째로 밀린다.
- WAV는 `soundfile.write(io.BytesIO(), ...)`로 메모리에 직접 쓴다. 기본 subtype이 `PCM_16`이라
  출력 포맷은 그대로고, 덤으로 **임시파일 왕복이 사라졌다**.
- 정렬이 어긋나면 조용히 넘어가지 않고 크래시한다: `infer` 반환 arity, `attn.dim() != 4`,
  `len(samples) % total_frames`, `len(frame_counts) != len(symbols)`.

### `tts/app/server.py` — `POST /speak`

`{audio_base64, media_type, sample_rate, duration, visemes[], segments[], envelope[], envelope_hz}`.
`/synthesize`와 `/health`는 무변경.

- `visemes[{start, end, viseme}]` — `a e i o u m n x`. 연속이고 마지막 `end == duration`.
- `segments[{start, end, text}]` — MeloTTS가 한 번에 합성한 문장 조각과 그 오디오 구간(자막용).
- `envelope[]` — 0..1 정규화 RMS. 입 벌림 정도와 자막 스윕 가중치에 쓴다.

**오디오와 타임라인은 반드시 한 번의 합성에서 같이 나와야 한다.** duration predictor가
확률적(`noise_scale_w=0.8`)이라, 메타데이터를 별도 엔드포인트로 빼면 두 번째 호출이 **다른
음소 길이**를 내놓아 타임라인이 오디오와 어긋난다. 추론이 2배가 되는 것보다 이게 더 큰 이유다.
헤더에 싣기엔 타임라인이 크고(~수 KB, nginx 기본 버퍼에 걸린다), multipart는 브라우저 파싱이
번거롭다 → base64가 남는다(+33%, LAN에서 수십 ms).

### `tts/Dockerfile` — MeloTTS 커밋 고정

`infer()`의 내부 반환 형태에 의존하게 됐으므로 핀 없는 `git clone --depth 1`은 위험하다.
`209145371cff8fc3bd60d7be902ea69cbdb7965a`(2024-12-24, 1년 넘게 정지)로 고정.
`--depth 1`은 임의 SHA checkout과 못 쓰므로 `git init` + `fetch --depth 1 origin <sha>`로 바꿨다.

### 프론트엔드

- **`frontend/src/speech_track.ts`** (신규) — `/tts/speak` 페치, base64 디코드, 커서 기반 viseme
  조회, envelope 선형 보간. React·DOM 없음.
- **`frontend/src/face_shapes.ts`** (신규) — viseme별 입모양 수치, 입 경로 생성, 스무딩 수학,
  표정 선택. 순수.
- **`frontend/src/face_view.tsx`** (신규) — SVG 얼굴 + rAF 루프.
- **`frontend/src/app.tsx`** — `speak_text`가 `/tts/speak`을 쓰고, 해시 레이아웃 분기와 트랙 ref를
  추가.

**해시 라우트 `#/face`**. 파이가 Chromium 키오스크로 바로 부팅할 수 있어야 해서 뷰 토글이 아니라
URL이어야 했다. `useSyncExternalStore`로 `location.hash`를 **구독만** 한다 —
`useState`로 미러링하면 `AGENTS.md`의 "조회 가능한 값을 state로 만들지 말라"에 걸린다.

**동기화**는 rAF에서 `audio.currentTime`을 읽는 방식. `AudioContext`는 쓰지 않았다 —
`createMediaElementSource`는 엘리먼트당 1회만 가능하고 출력을 영구히 그래프로 우회시키며,
`unlock_audio()`가 조심스럽게 다루는 iOS 오디오 세션과 얽힌다. 타이밍상 이득도 없다
(`currentTime` 해상도 ≲16ms vs viseme 구간 60~90ms).

**스무딩**은 전부 시간상수 기반(프레임당 alpha가 아니라 `dt` 기반)이라 파이가 30fps로 떨어져도
같은 모션이 나온다. 턱은 임계감쇠 스프링의 **닫힌 해**를 쓴다 — 명시적 오일러는 `omega*dt`가
커지면 발산하는데, 파이가 200ms 멈추면 실제로 도달하는 영역이다.

**표정 5종**: 대기(깜빡임·호흡) / 듣는 중(맥동 링) / 생각 중 / 말하는 중(립싱크) /
마이크 거부. 표정 전환은 전부 CSS(합성 프로퍼티)로, JS는 입·눈·눈썹바운스만 쓴다.

### 상태 표시와 자막 (첫 실기 테스트 후 수정)

처음엔 생각 중일 때 `···` 세 점을 띄웠는데, **STT→LLM 구간까지만 보이고 사라졌다.**
원인은 표시가 아니라 상태 관리였다: `run_turn`의 `finally { set_turn("idle") }`가
`void speak_text(...)` 직후에 실행되므로, **가장 긴 구간인 TTS 합성(수 초) 동안 얼굴이 대기
상태로 돌아가 있었다.**

`turn`(`idle`/`recording`/`processing`)을 더 잘게 쪼갠 **`activity` 하나로 교체**했다:
`waiting → recording → transcribing → thinking → synthesizing → speaking`.
`synthesizing`은 `speak_text`가 직접 소유하고(await하지 않는 호출이므로), `speaking`은 오디오
엘리먼트의 `onPlay`/`onEnded`가 구동한다. `is_speaking`은 `activity === "speaking"`으로
흡수되어 **상태 개수는 그대로인데 정보는 늘었다.**

마이크 잠금은 `transcribing`/`thinking` 동안만 — 합성·재생 중에는 눌러서 말을 끊을 수 있어야
하므로 기존 동작을 유지했다.

`···` 자리에는 이제 메신저처럼 **현재 상태 문구**가 뜬다:
대기 중 / 듣고 있어요 / 받아쓰는 중 / 생각하는 중 / 목소리 만드는 중 / 말하는 중.

말할 때는 같은 자리에 **노래방 자막**이 나온다.

- 서버 `/speak`가 `segments[{start, end, text}]`를 함께 준다. MeloTTS가 이미 문장 조각 단위로
  합성하고 있어서 조각별 샘플 오프셋은 **공짜로 알 수 있다** — 문장 경계는 추정이 아니라 실측이다.
- 다만 MeloTTS의 `split_sentences_into_pieces`는 **최소 길이만 있고 최대가 없다.** 실측에서
  `"안녕하세요. 오늘 서울 날씨는 맑고 조금 덥습니다."`가 5.5초 한 덩어리로 나왔다. 읽기엔 너무
  길어서 **표시용으로만** 문장 단위로 다시 쪼갠다(길면 어절 단위 줄바꿈).
- 조각 안에서 새로 만든 경계는 글자 수 비율을 **음량 곡선에 통과시켜** 배치한다. 이미 보내는
  envelope를 그대로 쓰므로 추가 데이터가 없고, 쉼표 구간에서 자막이 앞서나가지 않는다.
- 스윕도 같은 가중치를 쓴다. 즉 **문장 경계는 정확하고, 문장 안 위치는 보간**이다 — 음절 단위로
  정확히 하려면 MeloTTS의 `word2ph`가 필요한데 추론 경로에서 버려진다.
- 자막 한 줄은 18자로 제한했다. **줄바꿈이 일어나면 안 되기 때문**이다 — 하이라이트가 오른쪽에서
  잘라내는 `clip-path`라서, 두 줄이 되면 두 줄이 동시에 쓸린다. 글자 수 제한과 폰트 크기를
  같이 맞춰뒀다.

**콘솔 복귀 버튼**도 추가했다. 원래는 우하단 `opacity: 0.08`짜리 투명 링크여서 사실상 없는
것과 같았다. 우상단에 아이콘 + "콘솔" 라벨로 실제로 보이게 바꿨다(평소 55%, 호버·포커스 시 100%).

## 조용히 깨질 뻔한 것들

구현하면서 잡은, 겉으로는 안 보이는 함정들. 전부 코드에 주석으로 남겼다.

1. **`<audio>`의 자식 인덱스.** React는 자식을 위치로 재조정한다. 콘솔 분기가 형제 3개를 내고
   얼굴 분기가 1개를 내면 `<audio>`가 인덱스 3↔1을 오가며 **뷰 전환마다 언마운트/재생성**된다.
   그러면 `audio_unlocked_ref`는 여전히 `true`라 다시 언락하지 않고 → **iOS에서 TTS 영구 무음.**
   콘솔 본문을 프래그먼트 하나로 감싸 두 분기 모두 자식이 `[branch, audio]`가 되게 했다.
2. **`turn`과 `is_speaking`은 실제로 어긋나 있다.** `run_turn`의 `finally { set_turn("idle") }`이
   `speak_text`가 await 중일 때 실행되므로 **재생 내내 `turn === "idle" && is_speaking === true`**다.
   표정 우선순위에서 `is_speaking`을 `turn`보다 먼저 봐야 한다.
3. **CSS `transform`이 SVG `transform` 속성을 이긴다.** 같은 노드에 CSS가 transform을 걸면 JS의
   `setAttribute`는 무시된다 → 눈썹을 중첩 그룹 2개로 나눠 바깥은 CSS(표정), 안쪽은 JS(바운스)
   전용으로 했다. CSS가 transform하는 SVG 요소엔 `transform-box: fill-box`도 필수.
4. **리렌더가 rAF의 쓰기를 덮어쓴다.** `app_shell`은 턴마다 `log_system` 때문에 여러 번
   리렌더된다. JS가 구동하는 노드(`d`, `ry`, 안쪽 `transform`)의 JSX 속성을 계산값으로 두면
   리렌더마다 입이 음절 도중에 되돌아간다 → 전부 모듈 상수로 고정.
5. **iOS 언락 무음 WAV.** `unlock_audio()`는 `muted = true`로 무음 WAV를 튼다. rAF 루프의
   재생 판정에 `!audio.muted`가 없으면 첫 탭에서 입이 뻐끔거린다.
6. **프레임 경계 vs 샘플 경계.** 조각 내부 경계는 프레임 수에서, 조각 간 이음매는 샘플 수에서
   나오므로 마지막 ulp에서 어긋날 수 있다. 클라이언트가 `end`로 구간을 넘기므로 `snap_contiguous`로
   경계를 **정확히** 맞춘다.

## 성능

파이에서 60fps로 SVG를 돌리는 게 목표라 렌더링 규칙을 못박았다.

- rAF 루프는 **setter를 절대 호출하지 않는다.** 캐시한 노드 4개에 `setAttribute`만 한다.
- 좌표를 `toFixed(1)`로 양자화하고 직전 문자열과 비교해 **안 바뀌면 쓰지 않는다.** 스무딩이
  수렴하면 문자열이 같아져 **쉬는 얼굴의 DOM 쓰기가 0**이 된다.
- 호흡은 `<svg>`의 CSS transform(합성)이다. 이걸 매 프레임 SVG 속성으로 쓰면 얼굴 전체가 매
  프레임 재래스터된다 — 파이에서 할 수 있는 가장 나쁜 짓.
- 말하지 않을 땐 ~30fps로 스로틀.
- 트랙은 props/state가 아니라 ref로 전달 → **발화당 리렌더 0회.**

**지연은 늘지 않는다.** `attn`은 원래 계산되던 값이고, 임시파일 왕복과 30만 원소 `.tolist()`
왕복이 사라져 오히려 조금 빨라질 여지가 있다. 첫 요청 지연이 모델 로드에 지배되는 건 그대로다
(day-4의 미해결 과제).

## 검증

### 유닛 (Windows, 엔진 없이)

```text
cd tts; uv run pytest
36 passed
```

`viseme.py`는 torch/melo를 import하지 않으므로 매핑·병합 로직 전체가 엔진 없이 검증된다.
한국어 심볼 인벤토리 전수 커버, 자모별 매핑, 조음결합(종성이 제 음절 모음을 갖는지), blank 흡수,
40ms 흡수, 구간 연속성, envelope 정규화. `/speak` 계약과 502 매핑은 엔진 monkeypatch로.
기존 `/synthesize` 테스트 3개도 그대로 통과(회귀 없음).

### 실제 합성 (Docker) — **입모양이 음소와 정확히 맞는다**

`"안녕하세요. 만나서 반갑습니다."` → 4.195s, 15 구간:

```text
  0.000 - 0.104  x      0.104 - 0.615  a      0.615 - 0.906  e
  0.906 - 1.544  o      1.544 - 1.869  x      1.869 - 1.927  m
  1.927 - 2.438  a      2.438 - 2.543  m      2.543 - 2.833  a
  2.833 - 2.879  m      2.879 - 3.019  i      3.019 - 3.088  m
  3.088 - 3.204  i      3.204 - 3.506  a      3.506 - 4.195  x
```

원문과 대조하면 정확히 맞는다:

| 구간 | 글자 | 근거 |
|---|---|---|
| `a` `e` `o` | 안녕하 / 세 / 요 | ㅏㅕㅏ → `a`, ㅔ → `e`, ㅛ → `o` |
| `x` | (문장 경계) | 마침표 + 조각 간 무음 |
| `m` `a` | **만**나서 | ㅁ에서 입이 닫힘, 아·아·어가 이어져 `a` 한 덩어리 |
| `m` `a` | **반**갑 | ㅂ에서 닫힘 |
| `m` `i` | 갑**습** | 갑의 종성 ㅂ, 습의 ㅡ → `i` |
| `m` `i` `a` | 습**니다** | 습의 종성 ㅂ, ㅣ, ㅏ |

**닫힌 입(`m`) 4개가 ㅁ/ㅂ 자리에 정확히 떨어진다.** 뻥긋이 아니라 진짜 립싱크라는 증거.

원순 모음 확인 `"무료 우유 두 모금."` → 2.279s, 10 구간:
`x → m u`(무) `→ o`(료) `→ u`(우유두, 셋이 한 덩어리) `→ m o`(모) `→ i m`(금) `→ x`.
ㅜ/ㅛ/ㅗ에서 오므리고, ㅡ는 `i`로 간다.

기계 검사도 통과: 첫 구간 `start == 0`, 전 구간 연속(1e-9 이내 아님 — **정확히** 일치),
마지막 `end == duration == ` 디코드한 WAV 길이, `len(envelope) == ceil(duration * 50)`,
최단 구간 46.4ms ≥ 40ms 바닥, envelope는 0..1 피크 정규화. 출력 포맷은 44.1kHz/mono/16bit로
day-4와 동일.

### 지연 — 늘지 않았다 (+27ms, 0.7%)

같은 문장, 워밍업 후 3회, 파이썬 클라이언트(요청 시작 → 응답 전량 수신):

| | 실측 | 중앙값 | 전송량 |
|---|---|---|---|
| `/synthesize` | 3998 / 4006 / 4091 ms | **4006 ms** | 352KB |
| `/speak` | 4031 / 4033 / 4065 ms | **4033 ms** | 482KB (+37%) |

차이 27ms = base64 + JSON 직렬화 비용. 설계대로 `attn`은 공짜다(원래 계산되던 값).

> **측정 함정**: PowerShell `Invoke-WebRequest`로 재면 `/speak`이 `/synthesize`보다 **2배 빨라
> 보인다**(2.2s vs 4.1s). 서버가 아니라 클라이언트 아티팩트다 — Windows PowerShell 5.1의
> `Invoke-WebRequest`가 바이너리 `audio/wav` 응답 본문을 훨씬 느리게 처리한다. 다시 잴 일이
> 있으면 PowerShell 말고 다른 클라이언트로 재라.

### 자막 분할 (실제 오디오 기준)

`segments`가 타임라인을 빈틈없이 덮고 텍스트를 원문으로 복원하는지, 그리고 표시용 재분할이
읽을 만한 단위로 떨어지는지 확인했다. `"네, 확인했습니다. 지금 서울은 흐리고 비가 조금 내리고
있어요. 오후에는 개서 맑아질 예정이니까 우산은 접이식으로 챙기시면 충분할 것 같습니다."`
→ 13.475초, 합성 조각 **2개 → 자막 6줄**:

```text
   0.000 -  2.000  (2.000s, 10자, 5.0자/s)  네, 확인했습니다.
   2.000 -  4.900  (2.900s, 16자, 5.5자/s)  지금 서울은 흐리고 비가 조금
   4.900 -  6.703  (1.803s,  8자, 4.4자/s)  내리고 있어요.
   6.703 -  8.840  (2.137s, 17자, 8.0자/s)  오후에는 개서 맑아질 예정이니까
   8.840 - 11.620  (2.780s, 18자, 6.5자/s)  우산은 접이식으로 챙기시면 충분할
  11.620 - 13.475  (1.855s,  7자, 3.8자/s)  것 같습니다.
```

전 줄 연속, 18자 이내, 마지막 줄이 `duration`에서 끝나고, 발화 속도는 3.8~8.0자/s 안에 든다
(한국어 자연 발화 대역). 8.0자/s인 줄은 조각 안에서 새로 만든 경계라 보간 오차가 있는 쪽이다.

### 프론트

```text
cd frontend; npm run build   → 성공 (tsc -b + vite build)
git status                   → vite.config.js / .d.ts 미변경
cd tts; uv run pytest        → 36 passed (segments 추가 후에도)
```

(`tsc -b`가 커밋된 `vite.config.js`를 재생성하는 함정이 있는데, 이번엔 프록시를 안 건드려서
실제로 그대로인 걸 확인했다.)

## 남은 확인 (수동, 실제 디바이스)

- iPhone Chrome LAN 엔드투엔드 — day-4에서 두 번 깨졌던 iOS 오디오 언락 경로 회귀.
  재생 중 `#/face` ↔ `#/` 왕복해서 소리가 안 끊기는지(=`<audio>`가 안 죽는지) 확인.
- 파이 실기에서 Chromium 키오스크 부팅 + 프레임 예산 실측.
- `ᅳ → i` / `ᅥ → a` 는 판단 콜이다. 실제 얼굴에서 보고 어색하면 각각 `n` / `e`가 대안.
- `TTS_SPEED != 1.0`에서 타임라인 정합(코드상 맞지만 미검증).

## 미룬 개선 (deferred)

이번에 **의식적으로 안 한 것들**을 근거와 착수 지점까지 남긴다. 나중에 다시 판단하지 않아도
바로 집을 수 있도록.

### 1. `word2ph`로 음절 단위 자막 — 지금의 가장 큰 근사

**현재**: 문장(조각) 경계는 실측이지만, 조각 안에서 새로 만든 경계와 스윕 위치는 글자 수 비율을
음량 곡선에 통과시킨 **보간**이다. 실측에서 줄별 발화 속도가 3.8~8.0자/s로 벌어졌다(같은 문장을
쪼갠 두 줄이 8.0 / 6.5). 즉 최대 수백 ms 어긋난다.

**왜 안 했나**: 음소→글자 매핑에 MeloTTS의 `word2ph`가 필요한데, `melo/utils.py`의
`get_text_for_tts_infer`가 계산해두고 **`del word2ph`로 버린다**(반환은 `bert, ja_bert, phone,
tone, language` 5개뿐).

**착수 지점**:
- `get_text_for_tts_infer`를 쓰지 말고 `melo.text.clean_text(t, language)`를 직접 호출하면
  `norm_text, phone, tone, word2ph`를 다 받는다. 그 뒤 `cleaned_text_to_sequence` +
  `commons.intersperse`를 우리가 하면 지금 코드와 등가다.
- `word2ph[i]`는 BERT 토크나이저 토큰 `tokenized[i]`가 차지하는 음소 수다(`add_blank`면
  `word2ph[i] *= 2; word2ph[0] += 1`). 따라서 토큰 → 음소 구간 → 프레임 구간 → 시각이 확정된다.
- 토큰 → 원문 문자 오프셋은 fast tokenizer의 `return_offsets_mapping=True`로 얻는다.
  (`kykim/bert-kor-base`)
- **주의**: g2pkk가 발음 규칙(연음·경음화·ㅎ탈락)을 적용하므로 자모열은 표기형이 아니라 발음형이다.
  자모를 직접 원문에 매칭하려는 시도는 `좋아요 → 조아요`에서 깨진다. `word2ph` 경로가 유일하게 옳다.

### 2. HF 모델 리비전 고정 — 조용히 깨질 수 있는 유일한 지점

MeloTTS 코드는 커밋 SHA로 고정했지만 **가중치와 config는 안 했다.** `load_or_download_model`이
`myshell-ai/MeloTTS-Korean`의 HF `main`을 받아오고, **심볼 표가 그 `config.json`에 들어 있다**
(`TTS.__init__`의 `symbols = hps.symbols`). HF main이 다시 올라가 심볼 순서가 바뀌면
`symbol_to_id`가 밀려 **viseme 매핑이 에러 없이 어긋난다.**

**착수 지점**: `TTS(..., config_path=..., ckpt_path=...)`로 리비전 고정한 파일을 직접 가리키거나,
`huggingface_hub`의 `revision=` 으로 스냅샷을 박는다. 최소 방어로는 기동 시
`hps.symbols`의 해시를 검증해 다르면 크래시.

### 3. TTS 지연 (day-4부터 계속 최우선)

- **모델 프리로드**: 컨테이너 기동 시 1회 합성해 첫 요청 지연 제거.
- **문장 단위 스트리밍**: `infer_piece`가 이미 조각 단위로 `(samples, symbols, frame_counts)`를
  돌려주고 `speak`가 `offset_samples`를 누적하므로, 제너레이터로 바꾸는 건 국소 변경이다.
  base64-JSON도 조각당 하나씩 보내면 그대로 이어진다(막히는 길 아님).
  **단 envelope는 지금 발화 전체 기준 피크 정규화**라, 스트리밍에선 고정 기준으로 바꿔야
  문장마다 음량이 튀지 않는다. 자막의 `voiced_progress` 가중치도 같이 영향받는다.

### 4. 전송량 (지금 발화당 ~800KB, +37%)

- **Opus**: `soundfile.write(buffer, audio, sr, format="OGG", subtype="OPUS")` 로 7초 ≈ 21KB,
  **28배** 감소. **안 한 이유**: 주 테스트 기기가 iPhone Chrome(=WebKit)인데 Safari의
  OGG/Opus `<audio>` 지원이 iOS 17 근처에서야 들어왔다. 유일하게 실기 검증된 플랫폼을 걸고
  도박할 이유가 없었다. **viseme 타임라인은 초 단위라 코덱·샘플레이트에 독립적**이므로,
  오디오 인코딩만 바꾸면 되고 타임라인 코드는 한 줄도 안 바뀐다.
- **다운샘플**: 44.1k → 22.05k로 절반. 리샘플 CPU가 붙고 44.1k로 학습된 보코더 음질이 둔해진다.
- gzip은 PCM에서 5~10%뿐. 무의미.

### 5. 자막 표시

- **줄바꿈 지원**: 지금은 한 줄 18자로 제한한다. 하이라이트가 오른쪽 `clip-path`라서 두 줄이면
  두 줄이 동시에 쓸리기 때문. 제한을 풀려면 **글자당 `<span>`을 렌더하고 앞 N개에 클래스를
  토글**하면 된다(쓰기는 글자 수 바뀔 때만, 초당 몇 번). 대신 글자 단위 인라인 박스라
  라틴 단어가 중간에 끊길 수 있다.
- **문장 안 경계 오차**는 위 1번(`word2ph`)이 해결한다.

### 6. viseme 품질

- **이중모음 2단 렌더링**: `ᅪ`(/wa/)를 지금은 핵모음 `a` 하나로 낸다. 프레임 예산을 30/70으로
  쪼개 `o → a`로 보내면 활음이 보인다. 데이터는 이미 있고 `build_spans` 안에서 끝난다.
  v1에서는 과하다고 판단해 뺐다.
- **`ᅳ → i` / `ᅥ → a` 재검토**: 둘 다 판단 콜이다. 어색하면 각각 `n` / `e`가 대안.
  (`ᅳ → u`는 하지 마라 — 은/는/를마다 입을 오므린다.)
- **다국어**: viseme 표는 한국어 전용이다. `TTS_LANGUAGE=EN`이면 오디오·envelope는 정상이고
  타임라인만 전부 중립(`n`)이 된다. ARPABET 표를 더하는 건 순수 추가 작업.

### 7. 얼굴 / 기기

- 파이 실기: Chromium 키오스크 부팅, 프레임 예산 실측, `xset s off -dpms`(Screen Wake Lock보다
  확실하다고 보고 Wake Lock은 안 넣었다).
- 응답 내용에 따른 감정 표정, 눈동자 추적.
- 파이 음성 루프(마이크 직결 + 웨이크워드 + barge-in)와 얼굴 화면 결합 — `hardware-notes.md`.

### 8. 이번 범위에서 손대지 않은 기존 부채

- `stop_speaking`이 `tts_url_ref`의 object URL을 revoke하지 않는다. 다음 발화가 덮어쓰므로
  한 개만 남고 무해하다. **기존 동작이라 의도적으로 안 건드렸다**(AGENTS.md: 최소·국소).
- `tts/pyproject.toml`은 `requires-python = ">=3.11"`인데 `Dockerfile`은 `python:3.10-slim`이다.
  지금은 FastAPI/uvicorn만 쓰므로 무해하지만 불일치다.
- `README.md`의 "STT 미지원 확인" 절(`:197~`)은 day-2.5(`5584088`)에서 삭제된 UI
  (STT 진단 패널, 오디오 미터, 수동 입력폼)를 아직 설명한다. 문서 부채.
- 커밋된 빌드 산출물(`frontend/vite.config.js`, `.d.ts`, `*.tsbuildinfo`)이 소스와 나란히
  추적된다. 런타임에서 `.js`가 `.ts`를 이기므로 조용히 드리프트할 수 있다.
