# Day 8: 서버 스택을 RPi로 (진행 중)

## 목표

데스크탑을 루프에서 완전히 뺀다. 폰 → `https://<rpi-ip>:5173` 하나로 접속해
**말하고 → 받아쓰고 → 에이전트 → Piper → 폰 스피커**가 끝까지 돈다. 결과는 **실측**으로 남긴다.
런북은 [HANDOFF.md](HANDOFF.md) "Day 8 런북".

## 착수 준비 (2026-10-05, RPi 위 Claude Code 세션)

### 기기
| 항목 | 값 |
| --- | --- |
| 보드/OS | RPi4, Debian 13 trixie 64비트, 커널 6.12.47 rpt-rpi-v8, aarch64, PAGE_SIZE 4096 |
| RAM / 디스크 | 3790MB (+swap 2GB) / 29GB 중 18GB 여유 |
| CPU | 4코어, `ondemand`, 1.8GHz 확인(부하 중 `measure_clock arm` = 1800MHz) |
| 기준 온도 | 59.4°C 유휴 → 스택 기동 60.3°C → 테스트 턴 뒤 66.2°C |
| `get_throttled` | **`0xf0000`** — 저전압·주파수 캡·스로틀·소프트 온도제한이 **부팅 이후(81일) 한 번 이상 있었다.** 현재 비트(하위 4비트)는 0. 이력 비트는 리부팅 없이는 안 지워지므로 **완료 기준 "10턴 뒤 0x0"은 측정 전에 리부팅해서 기준선을 깨끗이 해야 판정 가능**하다. |
| 네트워크 | **Wi-Fi 2.4GHz만**(eth0 NO-CARRIER). 신호 -63dBm, ping 지터 5ms~1.5s. 다운로드 50~200KB/s라 설치에 1시간 넘게 걸렸다. **유선 연결이 가장 큰 개선.** |
| 선점 포트 | **`:3000` = 진짜 nullclaw** (`/home/linuxbrew/.linuxbrew/bin/nullclaw gateway`, 127.0.0.1 바인드). 스탠드인은 **3001**로. **→ 2026-10-05 밤 사용자 요청으로 nullclaw를 이 기기에서 완전히 제거**(systemd 사용자 유닛 3개+드롭인, brew 패키지, `~/.nullclaw`, 워치독·stackfix 헬퍼, 디버그·패치 디렉터리). 백업 `~/nullclaw-removed-20261005.tar.gz`(20MB). 3000은 비었지만 런처는 3001을 유지. |

### 설치 — 런북 대비 달라진 것
| 런북 | 실제 | 왜 |
| --- | --- | --- |
| `claude` 로그인 | 이미 돼 있음(claude.ai, CLI 2.1.289 네이티브) | — |
| tts: Docker | **네이티브 Piper** (`tts/.venv`, Python **3.11.17** uv 관리) | Docker 미설치. 시스템 Python 3.13에선 `numpy<2` 핀이 **소스 빌드**를 유발(1.26.4는 cp313 휠 없음, 10분 넘게 컴파일) → Dockerfile과 같은 3.11로. voice도 같은 3.11을 잡았다. |
| piper 음성 다운로드 | `piper.download_voices`가 **11MB에서 끊긴 파일을 "Downloaded"로 보고** → 패치 단계에서 `Wire format was corrupt` | 느린 회선. `hf download rhasspy/piper-voices ko/ko_KR/kss/medium/ko_KR-kss-medium.onnx`(재개·재시도)로 받아 해결. 63,221,984바이트가 정상. |
| whisper 첫 요청 다운로드 | `hf download Systran/faster-whisper-base`로 **미리** 받음(142MB) | 같은 이유. `WhisperModel('base')` 직접 호출은 8분간 0바이트로 멈춰 있었다. |
| 터미널 5개 | **`scripts/rpi-up.sh`** — tmux 세션 `claw` 창 6개(tts voice gateway backend frontend shell). `health`/`down` 서브커맨드 | HANDOFF가 허용한 tmux 방식의 스크립트화. systemd는 여전히 범위 밖. |
| `CLAUDE_WORKDIR` | `~/claw-home` (빈 디렉터리) | README 경고(`--allowedTools`는 샌드박스가 아님) |
| 추가 설치 | `uv` 0.12.23 (astral 설치 스크립트), `tmux` (apt) | — |
| `.gitignore` | `tts/voices/` 추가 | 126MB 모델 파일 |

유닛 테스트 **147개 전부 RPi에서 통과**(backend 59 / agent-gateway 19 / voice 5 / tts 64). `npm run build` 통과(1584 모듈, 9.3s).

### 스모크 실측 (착수 전, n이 작고 일부는 동시 실행 — 본 측정은 아래 "할 일"에서)
| 항목 | 실측 | 데스크탑 | RPi 투영 | 비고 |
| --- | --- | --- | --- | --- |
| Piper `/speak` 콜드 (7.9초 분량 문장) | **7.25s** | 1.2s(기동→첫 speak) | 6~10s | 투영 안 |
| Piper `/speak` 웜 ×3 | **2.9 / 3.2 / 3.1s** → RTF **0.37~0.40** | RTF 0.057 | RTF 0.28~0.46 | 투영 안. 단, 에이전트 턴과 **동시에** 돌았다 |
| STT base int8, 7.9초 Piper 발화 | **12.0 (콜드) / 10.0 / 9.5s** | 0.4~0.8s | 2.8~3.5s | **투영 대비 3배 어긋남.** 후보 원인: `transcribe()`에 `beam_size` 미지정(기본 5), 입력이 TTS 음성(사람 발화와 다름). 정확도: "라즈베리파이"→"나즈베리파이" 1음절 외 전부 정확 |
| STT 모델 로드 | 2.42s | — | — | 2초 무음 워밍업 전사는 48.9s(무음은 병적 입력, 참고용) |
| `claude -p` 단독 (빈 workdir, 콜드) | wall 18.5s / api 10.0s / 오버헤드 8.5s | — | — | Wi-Fi 상태 나쁠 때. cache_read 10.8k |
| 게이트웨이 홉 (턴 1, 2) | duration 3.0s api 2.1s ttft 2.9s / duration 4.5s api 5.2s ttft 4.4s | wall 3.5~7s | 동일 | `[claude_agent]` stderr |
| 턴 전체 (curl `/api/turns` → `turn_ended`) | **8.1s** (직결) / **8.2s** (Vite HTTPS 프록시 경유) | — | 6~11s | 폰 없이 백엔드 기준. STT·TTS 재생 제외. `--resume` 동작 확인(2턴이 1턴 내용을 요약) |
| RAM | 스택 전체 RSS 합 **~956MB**, `used` 1654MB | — | 1.1~1.9GB | 모델 로드 후 |

### 검증한 계약
- 5개 `/health` 전부 OK (backend·gateway·voice·tts 직결 + Vite 프록시 `/tts/health`·`/voice/health`·`/health`).
- SSE가 Vite HTTPS 프록시를 버퍼링 없이 통과(utterance 2건 → turn_ended 수신).
- `POST /api/push` (토큰 `day8-push`) → 202.
- STT 입력으로 쓴 wav는 Piper `/synthesize` 출력(22050Hz mono 16bit)이다. 폰 녹음 포맷은 아직 안 넣었다.

## 첫 수정: TTS 합성 직렬화 (2026-10-05)

**증상.** 폰에서 "말하는 중 → 목소리 만드는 중 → 말하는 중". 다음 문장 합성이 앞 문장 재생보다 늦게 끝난다.

**설계는 병렬이 맞다.** `speech_queue.ts`는 발화 이벤트 도착 즉시 `/tts/speak`를 쏘고(선합성), 머리 항목이 아직 안 끝났을 때만 "목소리 만드는 중"을 띄운다. 그런데 두 전제가 RPi에서 깨졌다.
1. 발화는 "몇 초 간격"이 아니라 **한꺼번에** 온다 — 4문장 턴의 utterance 4개가 90ms 안에 도착(에이전트가 한 덩어리로 주고 백엔드가 문장으로 쪼개므로).
2. Piper는 450ms가 아니라 RTF 0.4다. 그리고 `/speak`가 동기 함수라 스레드풀에서 4개가 **동시에** 돌고, onnxruntime 세션 하나가 이미 4코어를 다 쓰므로 동시 실행은 처리량 이득 없이 CPU만 나눈다.

**실측 (같은 4문장, RPi 단독, 웜).**

| | #1 준비 | #2 | #3 | #4 | 비고 |
| --- | --- | --- | --- | --- | --- |
| 순차 (참고) | 1.8s | 2.9s | 3.4s | 2.9s | 각각 단독 실행 시간 |
| 동시 4개, 수정 전 | **6.2s** | 10.0s | 11.1s | 9.9s | 첫 소리 3.5배 지연, 완료 순서도 뒤섞임, 온도 66→77°C |
| 동시 4개, **락 적용 후** | **2.1s** | 5.0s | 8.4s | 11.2s | 도착 순 FIFO. #1(4.75s 오디오) 재생이 끝나는 6.8s 전에 #2가 5.0s에 준비돼 이후 끊김 없음 |

**수정.** `tts/app/server.py`에 `threading.Lock` 하나 — `/speak`·`/synthesize`의 엔진 호출을 감싼다. 프론트는 도착 순서대로 fetch하므로 서버 FIFO = 발화 순서. 테스트 `test_speak_requests_are_synthesized_one_at_a_time` 추가(65개 통과). 프론트 변경 없음.

**남는 것.** Wi-Fi. `/speak` 응답은 base64 WAV라 문장당 280~500KB이고 폰은 이걸 약한 2.4GHz 링크로 받는다. 락은 서버 쪽 지연만 고친다. 폰에서 다시 겹침이 보이면 다음 용의자는 전송이다.

## 본 측정 (2026-10-06 00:07~00:30, 리부팅 뒤 깨끗한 기준선)

리부팅 8분 뒤 기준선: `get_throttled` **0x0**, 63.3°C 유휴, used 553MB. `scripts/rpi-up.sh`로 기동 → 30초 안에 5개 `/health` OK. 아래는 **각 항목 단독**(다른 부하 없음), 콜드/웜 구분. 폰 없이 RPi 안에서 curl/스크립트로 쟀다.

| 항목 | 콜드 | 웜 (3회) | 투영 | 판정 |
| --- | --- | --- | --- | --- |
| TTS 콜드 스타트 (기동→첫 `/speak`, 5.4초 문장) | 기동→health 30s 이내, 첫 `/speak` **8.66s** | — | 6~10s | 투영 안 |
| Piper `/speak` 웜, 5.5~6.0초 문장 | — | **2.48 / 2.32 / 2.61s** → RTF **0.41~0.43** | RTF 0.28~0.46 | 투영 안 |
| Piper 1코어 (`taskset -c 0`, 별도 인스턴스 :8202, 선택 항목) | 8.02s | 3.75 / 3.74 / 3.49s → RTF **0.64~0.65** | 실시간 이하 | **통과.** 4코어 대비 1.55배. day 9 얼굴 예산용 |
| STT base int8, 10.3초 wav, `/voice/transcribe` | **19.8s** (모델 로드 2.0s 포함) | **14.1 / 14.3 / 13.65s** → RTF **1.35** | 2.8~3.5s (6~7초 발화) | **3배 어긋남 확정** |
| STT 6.5초 클립, in-process 비교 | — | beam 5: 10.33 / 10.33 / 10.45s (RTF 1.6) · **beam 1: 7.68 / 7.95 / 8.16s (RTF 1.2)** · beam 1 + `cpu_threads=4`: 7.51 / 7.68 / 7.68s | 2.8~3.5s | `beam_size=1`은 25% 단축, 그래도 2.3배 |
| `claude -p` 단독 (빈 workdir, 같은 질문) | wall 6.44s / api 1.77s / duration 3.48s / **오버헤드 2.95s** | wall 5.92 / 8.17 / 8.81s, **오버헤드 3.06 / 2.67 / 2.57s** | 데스크탑 오버헤드 2.3~2.6s | **`stdin=DEVNULL` 뒤 오버헤드 2.6~3.1s**(day-7 미룬 #9 닫힘). 스모크의 8.5s는 Wi-Fi |
| 게이트웨이 홉, 깨끗한 턴 (재시작→첫 턴→같은 질문 ×3) | duration **2.58s** ttft 2.47s | duration 2.75 / 3.38 / 3.68s, ttft 2.62 / 3.28 / 3.55s | wall 3.5~7s | 정상 범위(latency-notes 미룬 1번 닫힘). 턴 5~10: duration 3.1~5.9s |
| 턴 전체 (`POST /api/turns` → `turn_ended`, 폰 없음) | 첫 턴 **5.56s**(첫 utterance 4.17s) | 6.41 / 7.27 / 7.77s · 턴 5~10: 8.9 / 12.5 / 8.7 / 13.1 / 8.8 / 9.1s | 6~11s | 투영 안. 첫 utterance까지 4.2~10.0s |
| RAM | 스택 RSS 합 **1045MB**, used 1452MB(기동 직후) → **1504MB**(10턴 뒤) | | 1.1~1.9GB | 투영 안 |
| 열/스로틀 | STT 중 **80~82°C** → `get_throttled` **0x80000**(소프트 온도제한 이력, STT 4회째에 이미) | 에이전트 턴 10회: 63~66°C, 비트 추가 없음. 세션 끝 71°C | 팬리스면 여기서 갈린다 | **완료 기준 "10턴 뒤 0x0" 실패.** 범인은 에이전트 턴이 아니라 STT |

### 원인 한 줄 / 읽는 법
- **STT 3배는 설정이 아니라 연산량.** 워커가 3코어(top 283%, 22 스레드)를 1.8GHz 그대로 쓴다(STT 중 `measure_clock arm` 1800MHz, 캡 없음). 그래도 RTF 1.2~1.6. `beam_size=1`은 25%, `cpu_threads=4`는 0%. **투영(RTF ~0.5)이 틀렸다.** 실시간 아래로 내리려면 `tiny`로 내리거나 기기를 바꿔야 한다. 정확도: TTS 음성 입력에선 오인식이 많다("받아쓰기"→"바닷스기", "에이전트"→"레이전토", "실측"→"실축"). 사람 발화 비교는 폰 녹음이 필요해 미측정.
- **열.** `temp_limit=0`, `temp_soft_limit=0`(펌웨어 기본, RPi4는 80°C에서 소프트 제한), 팬·PWM 없음. STT 한 요청(14s)으로 74→81°C. 클럭은 안 떨어졌지만 이력 비트는 박힌다. 실사용은 턴마다 STT가 한 번이므로 **매 턴 80°C를 찍는다.** 방열(히트싱크/팬)이 day 9 전 하드웨어 항목.
- **게이트웨이 `api_ms`는 세션 누적이다.** `--resume` 세션에서 1.7→31.6s로 단조 증가(10턴). `cost_usd`도 누적(0.004→0.176). 홉 분해엔 `duration_ms`·`ttft_ms`만 쓸 것.
- 턴 전체 − 게이트웨이 duration ≈ 3s = 프로세스 스폰. `claude -p` 단독 오버헤드와 일치한다.
- 같은 질문을 4번 하면 3번째부터 에이전트가 "혹시 제 대답이 잘 안 들리셨나요?"라고 되묻는다(utterance 1→2개, turn_ended 1.5s 지연). 깨끗한 턴 측정에선 질문을 바꾸는 게 낫다.
- `MAX_ARG_STRLEN`: PAGE_SIZE 4096, `xargs --show-limits` "Size of command buffer we are actually using: 131072" → 이론값 131,072 그대로(latency-notes 미룬 3번 닫힘). argv 전체 한계 2,088,316.
- 같은 문장도 Piper 출력 길이가 5.4~6.0s로 흔들린다(noise_w). RTF는 길이로 나눠서 비교.
- `pkill -f "port 8202"`는 그 문자열을 담은 **자기 셸**도 죽인다. 측정 스크립트에서 pid를 들고 `kill`로.

### 남은 것 (폰·사람이 필요)
- [ ] 폰에서 `https://172.30.1.40:5173`, 데스크탑 끈 상태로 한 턴 끝까지(소리까지).
- [ ] STT 사람 발화(폰 녹음 포맷) 비교 — 위 TTS 입력 결과와 정확도·시간.
- [ ] day-7 수동 확인 1·2·3·4·5·7.

## 조용히 깨질 뻔한 것
- **끊긴 다운로드가 성공으로 보고된다.** `piper.download_voices`는 Content-Length를 안 본다. 받은 `.onnx`는 **63,221,984바이트**인지 확인하고 넘어가라.
- `curl -C -` + HF 리다이렉트 + `--retry`는 재시도 때 **처음부터 다시 받는다**(파일이 21MB → 6MB로 줄어드는 걸 봤다). 큰 파일은 `hf download`.
- `uv`가 관리 Python 3.11을 받은 뒤엔 다른 서비스의 `uv sync`도 3.11을 집는다(voice가 그랬다). 문제는 아니지만 "시스템 3.13"이라고 가정하지 말 것.

- **"Load failed" N-1개 (간헐, 미해결).** 폰 턴 하나에서 첫 문장만 소리가 나고 나머지 문장 수-1개가 "Load failed"로 찍혔다. 서버 로그엔 그 턴의 `/speak`가 **전부 200**. 재시도에서는 정상. 구조적 배경: Vite 7은 프록시가 있어도 **HTTP/2**로 서빙하므로(`createSecureServer`, 응답 헤더 `HTTP/2 200`) 폰의 SSE와 N개 `/speak`가 TLS 연결 하나에 다중화된다 → 그 연결이 끊기면 진행 중인 요청이 **한꺼번에** 실패한다. 이게 "첫 문장만 성공, 나머지 동시 실패"의 모양이다. RPi 안에서 curl 하나로 같은 모양(h2 연결 1개, SSE+5스트림, 락 대기 13초)을 재현하면 **전부 성공** → 서버·프록시·락은 무죄. 정상 동작한 턴의 패킷 캡처(100초)에서도 폰 쪽 **RST 3회, 새 TCP 연결 7개, RPi→폰 재전송 24회**가 보여 링크 자체가 손실성이다. **사용자 판단(2026-10-05): RPi↔폰 손실은 중요하지 않다 — 최종 목표는 RPi 로컬(또는 유선) 마이크+스피커라 폰 경로는 임시다.** 재발하면: ① 캡처(`tcpdump -i wlan0 'tcp port 5173'`)로 FIN/RST 방향 확인, ② 유선, ③ `fetch_spoken_reply`에 네트워크 오류 1회 재시도, ④ 프론트 동시 합성 수 제한(오래 열린 빈 스트림 제거).

## 미룬 개선 (deferred)
- **systemd 유닛** — 현재 `scripts/rpi-up.sh`(tmux). 왜 안 했나: day 9의 오디오 소유자 결정에 따라 프로세스 구성이 바뀐다. 착수 지점: 스크립트의 env 블록을 그대로 `deploy/*.service`로.
- **`speech_queue.ts`의 전제 주석 갱신** — "Piper ~450ms, 발화는 몇 초 간격"은 RPi에서 둘 다 틀렸다. 서버 직렬화로 동작은 맞지만 주석이 반대로 말한다. 왜 안 했나: 프론트는 이번 수정 범위 밖. 착수 지점: `push()`의 주석.
- **`beam_size` env 노출** — 현재 faster-whisper 기본값(5). 측정됨: `beam_size=1`이 25% 빠르고 이 입력에선 정확도 차이 없음(둘 다 같은 오인식). 왜 안 했나: 25%로는 실시간에 못 미치고, 모델 교체(`tiny`)와 같이 결정할 일. 착수 지점: `voice/app/stt.py:51`.
- **방열** — 팬·히트싱크 없음. STT마다 80°C. 왜 안 했나: 하드웨어. 착수 지점: 히트싱크 붙인 뒤 같은 `stt.sh` 3회로 온도 곡선 재측정.
- **whisper `tiny` 비교** — base가 RTF 1.2~1.6. 왜 안 했나: 정확도 손실을 사람 발화로 봐야 하는데 폰 녹음이 없다. 착수 지점: `WHISPER_MODEL=tiny`로 voice 재기동, 같은 wav.
- **유선 네트워크** — 하드웨어. 모델 다운로드와 `claude -p` 왕복 모두에 걸린다.
