# HANDOFF — my-talking-claw

이 세션에서 이어서 작업하기 위한 인수인계. 마지막 갱신 2026-11-01 (day 9 진행 중).
다음 세션은 **데스크탑 Claude Code가 RPi에 SSH**로 작업한다(`hahahoho@172.30.1.40`, 키 등록됨, 저장소 `~/Documents/my-talking-claw`).

## 한 줄
마이크·스피커 없는 저성능 기기에 사는 자율 에이전트와 **음성으로 대화하는 인터페이스**. day 9에서 **RPi4 단일 기기**가 됐다: ReSpeaker Lite(마이크+스피커) + 7" 화면의 얼굴 + 상주 Python 오디오 루프. 폰은 보조 클라이언트. 다음은 두뇌 계층(day 10).

## 지금 상태 (2026-11-01 기준, 확정된 것)
- **RPi에서 `scripts/rpi-up.sh`로 전부 뜬다.** tmux `claw` 창 8개: tts voice gateway backend frontend **audio kiosk** shell. 바탕화면 아이콘 `my-talking-claw.desktop`. `up`이 화면 끄기(swayidle)를 멈추고 `down`이 되돌린다.
- **오디오는 `audio/`(Python 상주 루프)가 소유한다** — 캡처→웨이크워드→VAD→`/voice/transcribe`→`/api/turns`, SSE `utterance`→`/tts/speak`→PortAudio(ReSpeaker, -12dB). 브라우저는 `#/face?role=viewer`로 **그리기만** 한다(backend 이벤트 `device_state`·`speech_started{track}`·`speech_ended`). 근거·범위: [day-9.md](day-9.md) "2단계 설계".
- **웨이크워드는 자리표시자 3개 동시**: `hey jarvis` / `alexa` / `hey mycroft` (openWakeWord 기본 모델). 평소 자리에서 0.8~1.0으로 잡힌다. 한국어 "누구야"는 **미해결**(기성 모델 없음 — Porcupine 커스텀 또는 openWakeWord 학습).
- **STT는 base beam 1** (day-9 결정, 후보 5개 비교표는 day-9.md "STT 모델 결정"). 녹음은 VAD 스팬으로 **잘라서** 보낸다(무음 폭주 86s→10s).
- **AEC 없음.** ReSpeaker Lite USB 펌웨어 2.0.5의 AEC는 실측상 작동 안 함(재생 중 녹음을 STT가 그대로 받아씀). 사용자 결정: **추후 과제, 검증된 기기로 재시도.** 재생 중 웨이크워드(barge-in)는 임계 0.2로 best-effort — 실측 점수 0.24. 소프트웨어 AEC는 루프가 재생 PCM을 쥐고 있어 이제 가능(미착수).
- **열이 모든 수치를 오염시킨다.** 팬 없음. 상주 부하(루프 40% + Chromium 2×17%)로 유휴 76~80°C, STT 뒤 84°C 스로틀. `claude -p` 스폰이 2.6s(시원)→6.5s(지금). **쿨링(hardware-notes "쿨링" 1단계)이 다음 측정 전 선행 과제.**
- 테스트: backend 63 / agent-gateway 19 / voice 7 / tts 65 / **audio 30** / frontend `tsc` 통과.
- **커밋 안 됨.** 이 세션의 변경 전부 데스크탑 작업 트리에 미커밋이고, RPi 작업 트리엔 같은 내용이 **로컬 수정으로 복사**돼 있다. 절차: 데스크탑에서 커밋·푸시 → RPi에서 `git checkout -- . && git pull`.

### 턴 한 번의 실측 (14:10, 80°C, 툴 없는 질문)
말 끝 → 엔드포인트 0.7s → STT 5.9s → 포스트 → 첫 소리 13.5s(= `claude -p` 스폰 **6.5s** + 에이전트 2.7s + 첫 문장 TTS 2.8s) → 6문장 24초 재생. **말 끝에서 첫 소리까지 ~20s.** 분해와 줄일 곳은 day-9.md "여섯 번째".

## 다음 할 일 (우선순위)
1. **쿨링** — 팬 + 방열판(hardware-notes). 달고 나서 day-9의 턴 측정을 다시 한다. 그 전의 속도 수치는 전부 스로틀 상태 값이다.
2. **day 10: 두뇌 계층.** `claude -p` 스폰이 턴 지연의 절반이다 → 상주 세션(Agent SDK)이 첫 후보. 기억·시간·도구 정책·발화 규율·계약 재검토는 아래 "Day 10 개요".
3. **한국어 웨이크워드 "누구야"** — (A) Porcupine: 계정+AccessKey, 콘솔에서 몇 분, 바이너리 비공개. (B) openWakeWord 커스텀: Piper ko로 합성 데이터, GPU 1~2h. `audio/app/wake_word.py` 인터페이스 하나 교체.
4. **Chromium 유휴 CPU** — 얼굴 rAF 루프가 2×17%. `face_view.tsx` `idle_frame_interval_ms` 또는 유휴 시 정지. STT에 2.3s를 더한다(day-9 측정).
5. **STT "측정"→"제조/특정"** 오인식 2회 — `~/claw-recordings/`에 녹음이 있다. beam 5로 재전사해 비교.
6. **소프트웨어 AEC** — AEC를 다시 볼 때. webrtc-audio-processing 등, `capture.py`에 reference 주입.

## Day 10 개요 — LLM 레이어 (*claw 대체)

**전제(사용자 결정):** *claw류의 핵심 가치는 채널(telegram 등) 통합인데 우리는 채널이 음성 하나뿐이라 그 부분이 무의미하다. 대신 **더 얇은 계층**을 정의한다. 구현체일 수도, 지침(프롬프트·CLAUDE.md·운영 규칙)일 수도 있다. 첫 번째는 Claude Code 기반.

**출발점은 이미 있다.** `agent-gateway/app/claude_agent.py`가 사실상 그 얇은 계층의 초안이다: 페르소나(`CLAUDE_SYSTEM_PROMPT`), 세션 연속(`--resume`), 아웃바운드 채널(`DEVICE_PUSH_*`), 스트리밍 번역(`translate_line`). 부족한 것이 곧 day 10의 목록이다:
- **지연** — 턴당 프로세스 스폰 2.6s(시원)~6.5s(지금). 상주 세션(Agent SDK)으로 가면 사라진다. day 9 실측으로 이 항목이 1순위가 됐다.
- **기억** — `--resume`은 게이트웨이 프로세스 수명 안에서만 산다. 세션 id 영속화냐, 별도 메모리(파일/CLAUDE.md)냐.
- **시간** — `claude -p`는 턴 사이에 죽어 타이머를 못 든다. OS `at`/cron 위임, 상주 루프, Agent SDK 중 어디에.
- **도구 정책** — `--allowedTools`는 승인 목록일 뿐. `--disallowedTools`와 `CLAUDE_WORKDIR`(에이전트의 "집").
- **발화 규율** — 첫 문장을 짧게(첫 소리 지연에 직결), 숫자·영어 읽기(Piper 약점).
- **계약** — nullclaw 웹훅 계약(`/pair`, `/webhook`)을 유지할 이유가 남는가.

산출물은 코드일 필요가 없다. **"이 계층이 제공해야 하는 것" 목록과 각 항목의 선택 + 근거**가 day-10.md에 있으면 된다.

## 함정 (실측된 것만, day 9 추가분)
- **원격 셸에서 `pkill -f <문자열>`은 그 문자열이 제 명령줄에 있으면 자기 ssh 세션을 죽인다**(두 번 당함). `pkill -x`로.
- **PortAudio 기본 출력은 3.5mm 잭**이다. `AUDIO_OUTPUT_DEVICE=ReSpeaker`로 이름 지정(rpi-up.sh가 한다).
- USB DAC는 스트림 시작 직후 ~100ms를 삼킨다. 짧은 알림음은 앞에 무음을 둔다.
- openWakeWord `Model(wakeword_models=...)`는 **넘긴 리스트를 제자리에서 경로로 바꾼다.** 복사본을 넘길 것.
- Chromium 새 프로필은 GNOME 키링 대화상자(`--password-store=basic`)와 번역 팝업(프로필 Preferences로만 꺼짐)을 띄운다.
- whisper에 무음 위주 클립을 주면 폴백 재디코딩으로 50~90초가 걸린다. 반드시 잘라서.
- 이전 함정(day 8까지)은 day-8.md·README에 그대로.

## 저장소 / RPi
- GitHub `origin` (`wonhotoss/my-talking-claw`). RPi는 clone이 있고 `~/Documents/my-talking-claw`. 동기화 절차는 위 "커밋 안 됨".
- RPi 작업 디렉터리: `~/respeaker-test`(day-9 측정 wav·스크린샷), `~/respeaker-fw`(후보 펌웨어 3개, 미플래시), `~/claw-recordings`(루프가 남기는 발화 녹음), `~/claw-home`(에이전트 빈 workdir).

## 서브트랙: clova-track/ (대기)
클로바 프렌즈를 루팅해 음성 프론트엔드로 재활용하는 대체 하드웨어 트랙 → [clova-track/MERGE.md](clova-track/MERGE.md). **이번 세션에서 건드리지 않았다.** day-9의 재생 sink 인터페이스(`audio/app/player.py`)가 이 트랙의 접점이다.

## 문서 지도
| 문서 | 내용 |
| --- | --- |
| [overview.md](overview.md) · [milestone-1.md](milestone-1.md) · day-1~7.md | 초기 설계·경과. day-6 = Piper 전환, day-7 = 이벤트 스트림 |
| [day-8.md](day-8.md) | 서버 스택을 RPi로. 실측·열·tiny 비교 |
| [day-9.md](day-9.md) | ReSpeaker 실측(AEC 안 됨), STT 모델 결정, 키오스크, **네이티브 오디오 루프 설계·구현·실기 6회** |
| [README.md](README.md) | 각 서비스 실행법·env 표·이벤트 계약(day-9 이벤트 3종, `audio/` 절 포함)·검증. **명령은 여기가 원본** |
| [agent-latency-notes.md](agent-latency-notes.md) | 에이전트 홉 분해·기각된 가설·지연 조사 런북 |
| [platform-notes.md](platform-notes.md) | RPi4 vs IdeaPad, Piper 실측, RPi 투영·빌드 블로커 |
| [hardware-notes.md](hardware-notes.md) | 마이크/스피커 비교·확정 BOM·AEC 원리·**쿨링 계획** |
| [음성IO-로드맵.md](음성IO-로드맵.md) | 3단계 로드맵 + 최종 아키텍처 결정 |
| [clova-track/](clova-track/) | 클로바 리버싱·분해 트랙 |
