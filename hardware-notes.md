# 음성 입력 하드웨어 조사 — 내장형→외장형 / 저가→고가

## 배경 (왜 조사했나)

My Talking Claw는 현재 **스마트폰(브라우저 STT/TTS) + RPi 백엔드 + 별도 STT 머신** 3-요소 구성이다.
목표는 이를 **RPi 단일 기기**로 통합하는 것 — 스마트폰을 음성 입출력 장치에서 빼고, RPi에 마이크·스피커를 직접 붙여 자율 대화 기기로 만든다.

핵심 요구사항:
- **마이크 + 스피커를 가능한 한 단일 기기로** (스피커가 또 별도로 필요해지지 않게)
- **핫워드("누구야") 상시 청취**
- **barge-in(재생 중 끼어들기)** — TTS가 나오는 중에도 웨이크워드/발화를 인식
- **가성비**

결론(먼저): **ReSpeaker Lite + 소형 스피커(약 5만원)**로 확정. 근거·대안은 아래.

---

## 먼저 이해해야 할 3가지 (하드웨어 선택의 뼈대)

1. **마이크 하드웨어는 "전처리"만 한다.** 빔포밍·노이즈억제·AEC까지가 마이크(어레이) 역할. **전사(STT)는 별개**로 RPi(또는 서버)의 소프트웨어(Whisper 등)가 한다.
2. **웨이크워드는 (거의 항상) RPi 소프트웨어가 처리한다.** openWakeWord/Porcupine을 상시 실행. RPi4에서 80ms당 5ms 미만으로 매우 가벼움. 마이크가 뭐든 이 부분은 동일.
   - 예외: 온칩 음성인식 모듈(ESP32-S3-BOX 등)은 **고정 명령어 웨이크워드**만 칩에서. 자유 발화 한국어 STT는 못 함.
3. **barge-in을 좌우하는 건 "하드웨어 AEC 유무"다.** 스피커가 울리는 중 마이크가 자기 소리를 상쇄(AEC)해야 끼어들기가 됨.
   - **하드웨어 AEC(전용 DSP 칩)**: XMOS XVF-3000/XVF3800, XU316 → 견고한 barge-in.
   - **소프트웨어 AEC(RPi CPU)**: WebRTC 등 → 되긴 하나 약하고 CPU를 먹음(Whisper와 경쟁).
   - AEC가 성립하려면 **재생 신호가 그 장치를 거쳐야(reference)** 함 → 블루투스 스피커는 이 경로가 끊겨 barge-in이 깨진다. **스피커는 유선으로 그 장치/칩에 물려야** 함.

---

## 종합 비교표 (저가 → 고가, 국내가 = 디바이스마트/다나와 실측)

| # | 제품 | 형태 | 마이크/거리 | HW AEC / barge-in | 스피커 통합 | 국내가 | 한줄 평 |
|---|---|---|---|---|---|---|---|
| 1 | **2-Mics Pi HAT V2.0** [107100001] | 내장(HAT) | 2-mic 근거리 | ✗ (SW AEC만) | 별도(JST/3.5mm)+5V | **20,700원** | 최저가·내장이지만 barge-in 약함 |
| 2 | **ReSpeaker Lite** [107990273] ⭐ | 외장 조립 | 2-mic ~3m | ✓ XU316 (튜닝 필요) | 별도(JST/3.5mm)+5V | **39,900원** | 가성비 최적. 하드웨어 barge-in |
| 3 | ReSpeaker Lite Voice Kit(ESP32S3) [110061601] | 위성기기 | 2-mic ~3m | ✓ XU316 | 별도 | 47,900원 | 자체 ESP32, 위성용 |
| 4 | **PowerConf S330** (Anker) | 완제품 단일 | 4-mic ~3m | ✓ full-duplex | **내장** | 약 116,330~140,000원 | 진짜 한 통, 턴키, 비쌈 |
| 5 | ReSpeaker XVF3800 (베어) [101991441] | 외장 USB | 4-mic **5m** | ✓ XVF3800 | 별도(5W/3.5mm)+5V | 90,500원 | 원거리 강함 |
| 6 | ReSpeaker XVF3800 + Case [114993701] | 외장 완제품형 | 4-mic 5m | ✓ XVF3800 | 별도 | 95,900원 | 케이스 포함(스피커는 별도) |
| 7 | ReSpeaker XVF3800 + XIAO ESP32S3 [114993700] | 외장/위성 | 4-mic 5m | ✓ XVF3800 | 별도 | 97,500원 | + Wi-Fi/BLE |
| 8 | ReSpeaker Flex XVF3800 [100099135] | 외장 모듈 | 4-mic 선형 5m | ✓ XVF3800 | **10W 앰프 내장**(스피커는 별도) | 90,500원 | 큰 스피커 구동용 |
| 9 | Mic Array v3.0 (XVF-3000 베어) [107990053] | 외장 USB | 4-mic 5m | ✓ XVF-3000 | 별도 | 102,400원 | 구형 계열, 비권장 |
| 10 | USB Mic Array (XVF-3000, 케이스) [107990193] | 외장 완제품형 | 4-mic 5m | ✓ XVF-3000 | 별도 | 110,400원 | **EOL(단종)**, 비권장 |
| — | ESP32-S3-BOX-3 / HA Voice PE | 올인원 위성 | 2~mic | ✓ | 내장 | (별도 유통) | 자체 연산+서버 스트리밍, 아키텍처 다름 |

> 참고 스피커(외장 조립 시): Adafruit Mono Enclosed 3W 4Ω [ada-3351] 7,200원 / DFRobot 3W 8Ω [FIT0502] 5,800원 / Seeed Acrylic Enclosure for ReSpeaker Lite [110991984] 9,100원. XU316은 4Ω 5W까지 구동 → **4Ω 3~5W 인클로즈드** 권장.

---

## 카테고리별 정리

### A. 내장형(HAT) — 저가, 그러나 이 프로젝트엔 부적합
- **2-Mics Pi HAT V2.0 (20,700원)**: 코덱(TLV320AIC3104)일 뿐, AEC·빔포밍은 전부 RPi 소프트웨어. barge-in 약함.
- 7" 공식 디스플레이 뒤 장착 시 **마이크가 벽을 향함(STT 저하)** + 디스플레이 전원 점퍼와 GPIO 충돌. 결국 리본으로 빼야 하는데 그러면 "내장"의 장점 소멸.
- **결론: HAT의 유일한 강점(내장)이 무너지고 스피커도 어차피 외장 필요 → 탈락.**

### B. 외장 조립형 — 가성비 (채택)
- **ReSpeaker Lite (39,900원 + 스피커)**: XU316 **하드웨어 AEC** → barge-in 됨. USB UAC2 plug&play(드라이버 불필요). 2-mic ~3m.
- 단, **완제품이 아닌 조립**(보드+스피커+케이스+5V). 실사용 AEC는 **펌웨어/설정 튜닝** 필요(커뮤니티 편차 리포트 있음).

### C. 외장 완제품 단일기기 — 턴키, 고가
- **USB 스피커폰(Anker PowerConf S330 등)**: 마이크+스피커+하드웨어 AEC가 **한 통**. USB-C 하나, 배선 0, 드라이버 0. 실제 RPi 음성비서 사례 확인.
- **요구사항 "단일 기기"를 유일하게 완벽 충족.** 단 가전 외형 + 약 12~14만원.

### D. 외장 고성능 — 원거리(5m)
- **XVF3800 계열(90,500~97,500원)**: 4-mic 5m + 하드웨어 AEC/빔포밍. USB 재생을 reference로 삼아 barge-in. **스피커는 별도**(3.5mm 사용 시 `AUDIO_MGR_SYS_DELAY` 튜닝 권장).
- **Flex XVF3800(90,500원)**: 10W 앰프 내장으로 큰 패시브 스피커 구동.

### E. 올인원 위성기기 — 아키텍처가 다름
- **ESP32-S3-BOX-3 / Home Assistant Voice PE**: 마이크+스피커+웨이크워드+AEC 내장, 자체 ESP32가 서버로 스트리밍. RPi 주변장치가 아니라 "독립 기기+서버" 구조라 현재 설계와 결이 다름.

---

## STT / RPi 성능 메모 (마이크와 별개 이슈)

- Whisper는 RPi에서 **tiny/base까지가 현실적**. 한국어는 tiny 부족 → **base 최소**.
- **RPi4에서 base 한국어는 실시간 언저리~그 이하**(발화 후 수 초 지연). `small`↑는 실시간 불가.
- **단일 기기로 갈 거면 RPi5 강력 권장**(base 실시간 여유). RPi4 유지 시 STT만 분리하는 현재 구조가 품질상 유리.
- 경량 대안: **Vosk 한국어**(스트리밍·경량, 정확도는 Whisper base보다↓).

---

## 최종 결정 & 근거

**ReSpeaker Lite + 소형 스피커로 확정.**

- 요구사항 우선순위가 **가성비**로 정해짐 → S330(~13만) 대비 **~1/3 비용**.
- **하드웨어 barge-in(XU316 AEC)** 확보 — HAT의 소프트웨어 AEC 한계를 넘음.
- USB plug&play로 RPi 통합 단순.
- 감수 사항: 조립형(밀봉 완제품 아님), 2-mic ~3m(원거리 아님), **AEC 설정 튜닝 필요**.

향후 원거리(거실 5m급)나 턴키가 필요해지면 → **XVF3800(9만원대)** 또는 **PowerConf S330(완제품)**으로 격상.

### 확정 BOM (국내, 디바이스마트)

| 부품 | 가격 |
|---|---|
| ReSpeaker Lite (USB 2-Mic, XU316) [107990273] | 39,900원 |
| 소형 인클로즈드 스피커 4Ω 3~5W (예: ada-3351 / FIT0502) | 약 6,000~7,200원 |
| (선택) Acrylic Enclosure for ReSpeaker Lite [110991984] | 9,100원 |
| 5V USB 전원(스피커 앰프용, 보유 시) | ~0 |
| **합계** | **약 47,000 ~ 56,000원** |

---

## 소프트웨어 통합 방향 (다음 단계 밑그림)

프론트엔드가 **폰 브라우저(Web Speech) → RPi 헤드리스 음성 루프**로 바뀐다. 단, 기존 `voice/` 서비스는 재사용.

```
ReSpeaker Lite (USB, AEC 처리된 16kHz)
  → RPi: 연속 캡처 → 링 버퍼
      → [상시] openWakeWord ("누구야")   ← CPU 가벼움
          → 감지 시 TTS 재생 중단(ducking) + VAD로 발화 녹음
              → 기존 POST /voice/transcribe (faster-whisper)  ← 재사용
                  → 에이전트 백엔드 /api/message              ← 재사용(현재 mock)
                      → TTS → ReSpeaker Lite 스피커 재생
```

- **재사용 지점**: `voice/`의 `/voice/transcribe`, `backend/`의 `/api/message`는 계약 유지. "입력 프론트"만 교체.
- **신규**: RPi 상시 파이썬 루프(캡처+웨이크워드+VAD+재생), TTS 엔진(온디바이스 한국어: Piper/MeloTTS 등 — day-2 후속 후보).
- barge-in: 웨이크워드 감지 시 재생 스트림 중단/볼륨 다운 로직을 코드로 구현(하드웨어 AEC가 인식 자체는 가능케 함).

---

## 미확인 / 후속 확인 항목

- ReSpeaker Lite **USB 모드에서 AEC/ barge-in 실사용 품질** — 구매 후 실측·펌웨어/설정 튜닝 필요. 참고 레포: corus87/Respeaker-lite-on-raspberry-pi.
- RPi4 vs RPi5 한국어 base STT 지연 실측.
- 온디바이스 한국어 TTS 엔진 선정(Piper/MeloTTS/XTTS).
- 소형 스피커 임피던스/음량 실측(4Ω 권장).

## 출처
- Seeed Wiki: reSpeaker Lite / XVF3800 / 2-Mics Pi HAT / USB Mic Array v2.0
- 디바이스마트(국내가·재고), 다나와/11번가(PowerConf S330 시세)
- openWakeWord (dscripka/openWakeWord), corus87/Respeaker-lite-on-raspberry-pi
- Home Assistant Voice(웨이크워드), CNX Software(XVF3800)
