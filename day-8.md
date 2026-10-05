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

## 할 일 (런북 본 측정)
- [ ] **리부팅 → `get_throttled` 0x0 기준선 확보** → 스택 기동 → 10턴 → 다시 확인.
- [ ] 폰에서 `https://172.30.1.40:5173` 접속, 데스크탑 전원 끈 상태로 한 턴 끝까지(소리까지).
- [ ] 측정표를 **각 항목 단독으로 3회 이상, 콜드/웜 구분**해 다시 채운다(위 스모크는 동시 실행 포함).
- [ ] STT 3배 어긋남 원인 한 줄: `beam_size=1` 비교, 사람 발화(폰 녹음) 비교.
- [ ] agent-latency-notes §5-3 "깨끗한 턴"(재시작→첫 턴→같은 질문 3회), `MAX_ARG_STRLEN`(`xargs --show-limits`), `stdin=DEVNULL` 뒤 오버헤드.
- [ ] day-7 수동 확인 1·2·3·4·5·7.
- [ ] (선택) TTS `taskset -c 0`로 1코어 묶은 RTF.

## 조용히 깨질 뻔한 것
- **끊긴 다운로드가 성공으로 보고된다.** `piper.download_voices`는 Content-Length를 안 본다. 받은 `.onnx`는 **63,221,984바이트**인지 확인하고 넘어가라.
- `curl -C -` + HF 리다이렉트 + `--retry`는 재시도 때 **처음부터 다시 받는다**(파일이 21MB → 6MB로 줄어드는 걸 봤다). 큰 파일은 `hf download`.
- `uv`가 관리 Python 3.11을 받은 뒤엔 다른 서비스의 `uv sync`도 3.11을 집는다(voice가 그랬다). 문제는 아니지만 "시스템 3.13"이라고 가정하지 말 것.

- **"Load failed" N-1개 (간헐, 미해결).** 폰 턴 하나에서 첫 문장만 소리가 나고 나머지 문장 수-1개가 "Load failed"로 찍혔다. 서버 로그엔 그 턴의 `/speak`가 **전부 200**. 재시도에서는 정상. 구조적 배경: Vite 7은 프록시가 있어도 **HTTP/2**로 서빙하므로(`createSecureServer`, 응답 헤더 `HTTP/2 200`) 폰의 SSE와 N개 `/speak`가 TLS 연결 하나에 다중화된다 → 그 연결이 끊기면 진행 중인 요청이 **한꺼번에** 실패한다. 이게 "첫 문장만 성공, 나머지 동시 실패"의 모양이다. RPi 안에서 curl 하나로 같은 모양(h2 연결 1개, SSE+5스트림, 락 대기 13초)을 재현하면 **전부 성공** → 서버·프록시·락은 무죄. 정상 동작한 턴의 패킷 캡처(100초)에서도 폰 쪽 **RST 3회, 새 TCP 연결 7개, RPi→폰 재전송 24회**가 보여 링크 자체가 손실성이다. **사용자 판단(2026-10-05): RPi↔폰 손실은 중요하지 않다 — 최종 목표는 RPi 로컬(또는 유선) 마이크+스피커라 폰 경로는 임시다.** 재발하면: ① 캡처(`tcpdump -i wlan0 'tcp port 5173'`)로 FIN/RST 방향 확인, ② 유선, ③ `fetch_spoken_reply`에 네트워크 오류 1회 재시도, ④ 프론트 동시 합성 수 제한(오래 열린 빈 스트림 제거).

## 미룬 개선 (deferred)
- **systemd 유닛** — 현재 `scripts/rpi-up.sh`(tmux). 왜 안 했나: day 9의 오디오 소유자 결정에 따라 프로세스 구성이 바뀐다. 착수 지점: 스크립트의 env 블록을 그대로 `deploy/*.service`로.
- **`speech_queue.ts`의 전제 주석 갱신** — "Piper ~450ms, 발화는 몇 초 간격"은 RPi에서 둘 다 틀렸다. 서버 직렬화로 동작은 맞지만 주석이 반대로 말한다. 왜 안 했나: 프론트는 이번 수정 범위 밖. 착수 지점: `push()`의 주석.
- **`beam_size` env 노출** — 현재 faster-whisper 기본값(5). 왜 안 했나: 측정 전. 착수 지점: `voice/app/stt.py:51`.
- **유선 네트워크** — 하드웨어. 모델 다운로드와 `claude -p` 왕복 모두에 걸린다.
