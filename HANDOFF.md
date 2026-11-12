# HANDOFF — my-talking-claw

이 세션에서 이어서 작업하기 위한 인수인계. 마지막 갱신 2026-11-08 (day 11 쿨러 장착·실측).
다음 세션은 **데스크탑 Claude Code가 RPi에 SSH**로 작업한다(`hahahoho@172.30.1.40`, 키 등록됨, 저장소 `~/Documents/my-talking-claw`).

## 한 줄
마이크·스피커 없는 저성능 기기에 사는 자율 에이전트와 **음성으로 대화하는 인터페이스**. day 9에서 **RPi4 단일 기기**가 됐다: ReSpeaker Lite(마이크+스피커) + 7" 화면의 얼굴 + 상주 Python 오디오 루프. 폰은 보조 클라이언트. 다음은 두뇌 계층(day 10).

## 지금 상태 (2026-11-01 기준, 확정된 것)
- **RPi에서 `scripts/rpi-up.sh`로 전부 뜬다.** tmux `claw` 창 8개: tts voice gateway backend frontend **audio kiosk** shell. 바탕화면 아이콘 `my-talking-claw.desktop`. `up`이 화면 끄기(swayidle)를 멈추고 `down`이 되돌린다.
- **오디오는 `audio/`(Python 상주 루프)가 소유한다** — 캡처→웨이크워드→VAD→`/voice/transcribe`→`/api/turns`, SSE `utterance`→`/tts/speak`→PortAudio(ReSpeaker, -12dB). 브라우저는 **`#/kiosk`**(얼굴과 필수 캡션만, 항상 viewer — 2026-11-11부터)로 **그리기만** 한다(backend 이벤트 `device_state`·`speech_started{track}`·`speech_ended`). 디버그 뷰어는 `#/face?role=viewer`. 근거·범위: [day-9.md](day-9.md) "2단계 설계", 얼굴 전체는 [얼굴-설계.md](얼굴-설계.md).
- **웨이크워드는 자리표시자 3개 동시**: `hey jarvis` / `alexa` / `hey mycroft` (openWakeWord 기본 모델). 평소 자리에서 0.8~1.0으로 잡힌다. 한국어 "누구야"는 **미해결**(기성 모델 없음 — Porcupine 커스텀 또는 openWakeWord 학습).
- **STT는 `STT_ENGINE`으로 고른다**(day-11): `whisper`(base beam 1, day-9 결정, 후보 비교표는 day-9.md "STT 모델 결정"; 녹음을 VAD 스팬으로 **잘라서** 보낸다) 또는 `azure`(녹음 중 스트리밍, 말 끝에 텍스트가 와 있다). 기기 루프와 voice 서비스(폰)가 같은 값을 읽는다.
- **AEC 없음.** ReSpeaker Lite USB 펌웨어 2.0.5의 AEC는 실측상 작동 안 함(재생 중 녹음을 STT가 그대로 받아씀). 사용자 결정: **추후 과제, 검증된 기기로 재시도.** 재생 중 웨이크워드(barge-in)는 임계 0.2로 best-effort — 실측 점수 0.24. 소프트웨어 AEC는 루프가 재생 PCM을 쥐고 있어 이제 가능(미착수).
- **열은 해결됐다(day 11, 2026-11-08).** 30mm 팬 5V + 구리 방열판 장착 뒤 전체 스택 유휴 **37~40°C**, STT 4회 연속 최고 **52°C**, 스로틀 비트 없음(전: 72~74 / 84°C). 팬 소음은 마이크 RMS -43 dBFS, "알렉사" 7/7. 온도 연동 팬·아머 케이스 불필요. 수치: [day-11.md](day-11.md).
- 테스트: backend 63 / agent-gateway 19 / voice 7 / tts 65 / **audio 30** / frontend `tsc` 통과.
- **RPi 동기화는 공개 저장소 pull.** 편집·커밋·push는 데스크탑(또는 클라우드 세션)에서만 → RPi는 `git pull --ff-only origin main`(인증 없이, 저장소가 공개). **RPi는 GitHub 자격증명을 갖지 않는다**(2026-11-12 `gh auth logout` + `~/.gitconfig`의 gh 자격증명 도우미 제거 — 뇌가 읽을 수 있는 비밀을 하나 줄였다, [뇌-상호작용-로드맵.md](뇌-상호작용-로드맵.md) 3절). RPi에서 push는 실패한다(의도). 저장소가 비공개가 되면 데스크탑이 SSH로 RPi에 직접 밀어 넣는다(git bundle 또는 `rpi` remote). GitHub 서버 쪽 토큰 철회(Authorized OAuth Apps → GitHub CLI)는 사용자가 나중에 — 철회하면 데스크탑 `gh` 로그인도 풀린다.
- **RPi CLI 로그인이 만료된다.** 11-08에 일주일 방치 뒤 "OAuth session expired and could not be refreshed" — 두뇌가 그 문장을 그대로 읽었다. 관리자가 RPi에서 `claude` → `/login`. 장기 토큰(`claude setup-token`) 전환은 day-11 미룬 개선.

### 턴 한 번의 실측 (day-9 14:10, 80°C — day-11에서 45°C·SDK 상주로 ~14.5s, 분해는 day-11.md)
말 끝 → 엔드포인트 0.7s → STT 5.9s → 포스트 → 첫 소리 13.5s(= `claude -p` 스폰 **6.5s** + 에이전트 2.7s + 첫 문장 TTS 2.8s) → 6문장 24초 재생. **말 끝에서 첫 소리까지 ~20s.** 분해와 줄일 곳은 day-9.md "여섯 번째".

> **2026-11-12 뇌 권한**([day-13.md](day-13.md)): 뇌는 기기 전체를 읽고, 집(`~/claw-home`) 안은 `.claude/settings.json`만 빼고 전부 고친다 — 자기 운영 규칙(`CLAUDE.md`)·`host.json`·새 시스템 지도 `system.md`까지. 못 하는 변경은 `requests.md`로 관리자에게. 금지 대신 기록: 게이트웨이가 집을 git으로 커밋(`git -C ~/claw-home log -p`, 되돌리기는 `git revert`). 원칙은 "불필요한 금지는 만들지 않는다"([뇌-상호작용-로드맵.md](뇌-상호작용-로드맵.md) 3-1).

## day 10 상태 (2026-11-01 저녁, 데스크탑에서 구현·실기 완료, RPi 미전환)
- **설계**: [brain-memo.txt](brain-memo.txt)(사용자) → [day-10.md](day-10.md). 두뇌 계층 = **뇌의 집**(Claude Code 프로젝트 디렉터리, 관리자 설정: CLAUDE.md·settings.json·memory.md·schedule/) + 코드는 호스트와 호출 규약만. 단단한 층(권한·모델·스케줄 검증, 호스트 집행)과 무른 층(문서, 모델이 따르는 만큼).
- **구현**: `agent-gateway/app/sdk_agent.py`(Agent SDK 상주 세션, resume, 시각 스탬프, interrupt), `scheduler.py`(`schedule/*.json` → `/api/push`), `AGENT_BRAIN=sdk|cli`(기본 `cli`), 템플릿 `brain-home.example/`. 테스트 31개.
- **데스크탑 실기 통과**: "2분 후에 불러줘" → 파일 → 정각에 push, "기억해" → memory.md, 재시작 뒤 resume, 끼어들기 interrupt 뒤 세션 유지. 스폰 0, 첫 줄까지 1.7~3.5s.
- **집은 둘로 갈라져 있다**(사용자 결정: 음성 사용자 영역을 좁히지 않는다): `CLAUDE.md`=관리자 운영 규칙, `persona.md`·`memory.md`=두뇌가 음성 요청으로 고친다(`@import`). `host.json`=기다림 안내. CLAUDE.md·import는 **세션 시작 때 한 번** 읽힌다(실측) — 관리자가 손으로 고치면 게이트웨이 재시작.
- **문장 단위 선출력 + 기다림 안내 구현**(day-10.md): 첫 문장 2.7s, 도구 턴은 "찾아볼게" 안내가 4.6초 먼저.
- **RPi 전환 완료**: SDK aarch64 설치, `~/claw-home` 신뢰됨, `rpi-up.sh`가 sdk 두뇌로 뜬다. 상주 CLI RSS 240MB, 유휴 CPU 0.5%, 게이트웨이 구간 1.5~3.4s(시원할 때).
- **쿨러 주문됨**(30mm SZH-AT002 ×2 + 구리 방열판). 도착 전엔 RPi를 끄고 데스크탑+폰으로 진행(모니터 번인 우려 — 대책은 나중에).
- 데스크탑 두뇌의 집: `D:\projects\claw-home`(신뢰 수락됨). 기동: `agent-gateway`에서 `AGENT_BRAIN=sdk BRAIN_HOME=D:\projects\claw-home AGENT_PAIRING_CODE=000000 DEVICE_PUSH_URL=http://127.0.0.1:8000/api/push DEVICE_PUSH_TOKEN=<백엔드 PUSH_TOKEN> uv run uvicorn app.server:app --port 3000`. **`CLAUDE_BIN`은 비운다**(npm 스텁 함정, README).

> **2026-11-08 방향 논의**: STT·TTS는 Azure Speech 종량제로, 뇌는 RPi에 남기고 뇌에게 기기(예약·외장기기·얼굴 카드)를 준다. 트랙·범위·의존성은 [뇌중심-로드맵.md](뇌중심-로드맵.md). 우선순위는 미정(사용자 재판단).
>
> **트랙 A·B 실측 완료**(2026-11-08 밤, [scripts/azure-probe/](scripts/azure-probe/README.md), 수치는 [day-11.md](day-11.md) "Azure 음성 API 프로브"): 스트리밍 STT **말 끝→텍스트 ≈ 0 s**(지금 6.8 s), TTS 첫 문장 **0.1~0.3 s**(지금 2.5~3 s), ko-KR viseme 매핑표 확보, SDK가 RPi에 그대로 설치됨. 리소스 Speech F0 `koreacentral`, 키는 `scripts/azure-probe/.env`(gitignore)와 RPi 같은 경로. RPi 2.4 GHz Wi-Fi가 지속 트래픽에서 무너져(손실 40%) **5 GHz로 전환했고**(`KT_GiGA_5G_hahahoho`, 2G autoconnect off) 재측정에서 RPi도 데스크탑과 같은 수치(TTS 42/42, 부하 중 손실 0%). **트랙 A·B 구현·실기 완료**(day-11 "트랙 B 구현"·"트랙 A 구현"·"음성 턴 실측 — Azure"): `rpi-up.sh`의 **기본값이 azure·azure**(2026-11-08 밤 결정); 로컬로 되돌리려면 `STT_ENGINE=whisper TTS_ENGINE=piper scripts/rpi-up.sh`. STT는 루프가 쥐는 `transcriber` 심(웨이크워드에 열고 녹음 중 스트리밍), voice 서비스도 같은 env를 따라 폰이 함께 전환. **RPi 실기(23:21): 말 끝→첫 소리 ~3.3 s**(STT 0.09 + 두뇌 첫 델타 1.63 + TTS ≈0.3; day-11 오후 14.5 s). 재기동 뒤 첫 턴만 SDK 연결로 +4 s. 2026-11-08 밤 현재 RPi 스택은 azure·azure로 떠 있다.

## 다음 할 일 (우선순위)
1. ~~데스크탑 전체 스택 + 폰 실사용~~ — 끝(day-10.md). 체감 괜찮음, IP 읽기만 어색 → 규약으로 해결.
2. ~~쿨링~~ — 끝(day-11). ~~STT 6.8s·첫 문장 TTS~~ — Azure로 끝(day-11 밤): **말 끝→첫 소리 ~3.3s**. 남은 덩어리는 두뇌 첫 델타 1.6~2.3s와 trailing silence 0.7s. 기본값 전환·게이트웨이 세션 워밍업은 day-11 미룬 개선.
3. ~~RPi 두뇌 전환~~ — 끝. ~~문장 단위 선출력~~ — 끝.
4. ~~모니터 번인 대책~~ — **이벤트 idle 구현**(2026-11-11, [day-12.md](day-12.md) "구현"): 깜빡임·시선·20~60 s 드리프트·10 min 자리 이동, 쉴 때 루프가 잔다. `#/kiosk` Chromium **6.8%**(전 9.2% 정지 얼굴, 모션 켜면 48%). `--force-prefers-reduced-motion`은 뺐다. **RPi Chromium은 GPU(V3D, ANGLE/GLES)를 쓰고 있다** — day-9의 "GPU 합성 없음"은 틀린 추정. 남은 것: 밤 디밍·화면 끄기(시간대 모드), 가끔의 행동 — [얼굴-설계.md](얼굴-설계.md) 9절.
5. **한국어 웨이크워드 "누구야"** — (A) Porcupine: 계정+AccessKey, 콘솔에서 몇 분, 바이너리 비공개. (B) openWakeWord 커스텀: Piper ko로 합성 데이터, GPU 1~2h. `audio/app/wake_word.py` 인터페이스 하나 교체.
6. ~~Chromium 유휴 CPU~~ — `--force-prefers-reduced-motion`으로 44%→7~16% (day-9 쿨링 절). `face_view.tsx` `idle_frame_interval_ms` 또는 유휴 시 정지. STT에 2.3s를 더한다(day-9 측정).
7. ~~STT "측정"→"제조/특정" 오인식~~ — Azure가 1건 교정, 1건은 Azure도 "제조"(day-11 프로브). 11-01 녹음은 마이크 포화(−0 dBFS)였다. 트랙 A로 흡수.
8. **소프트웨어 AEC** — AEC를 다시 볼 때. webrtc-audio-processing 등, `capture.py`에 reference 주입.

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
- GitHub `origin` (`wonhotoss/my-talking-claw`, **공개**). RPi는 clone이 있고 `~/Documents/my-talking-claw`, pull 전용(자격증명 없음). 동기화 절차는 위 "RPi 동기화".
- RPi 작업 디렉터리: `~/respeaker-test`(day-9 측정 wav·스크린샷), `~/respeaker-fw`(후보 펌웨어 3개, 미플래시), `~/claw-recordings`(루프가 남기는 발화 녹음), `~/claw-home`(에이전트 빈 workdir).

## 서브트랙: clova-track/ (대기)
클로바 프렌즈를 루팅해 음성 프론트엔드로 재활용하는 대체 하드웨어 트랙 → [clova-track/MERGE.md](clova-track/MERGE.md). **이번 세션에서 건드리지 않았다.** day-9의 재생 sink 인터페이스(`audio/app/player.py`)가 이 트랙의 접점이다.

## 문서 지도
| 문서 | 내용 |
| --- | --- |
| [overview.md](overview.md) · [milestone-1.md](milestone-1.md) · day-1~7.md | 초기 설계·경과. day-6 = Piper 전환, day-7 = 이벤트 스트림 |
| [day-8.md](day-8.md) | 서버 스택을 RPi로. 실측·열·tiny 비교 |
| [day-9.md](day-9.md) | ReSpeaker 실측(AEC 안 됨), STT 모델 결정, 키오스크, **네이티브 오디오 루프 설계·구현·실기 6회** |
| [day-10.md](day-10.md) | 두뇌 계층: 뇌의 집(관리자 설정) + Agent SDK 상주 세션, 스케줄, 문장 단위 선출력 |
| [day-11.md](day-11.md) | 쿨러 장착 Q&A와 실측(온도·팬 소음·웨이크워드), 로그인 만료, Azure STT·TTS 전환과 실기(~3.3 s) |
| [day-12.md](day-12.md) | 얼굴 idle 비용 실측(GPU 확인, 기법별·dev/빌드·브랜치), 이벤트 idle 설계·구현, `#/kiosk` 운용 모드, 이미지 파츠 규격 |
| [day-13.md](day-13.md) | 뇌 권한: 비밀·root·자기 규칙 결정(불필요한 금지는 만들지 않는다), GitHub 토큰 제거, `settings.json`만 절대 금지 + 집의 git 기록, 실측 |
| [얼굴-설계.md](얼굴-설계.md) | **얼굴 설계**: 라우트×역할, SVG 구조와 소유 규칙, mood 5종, 립싱크·자막, 이벤트 idle, 성능 계약, 직접 그린 파츠로 갈 때 |
| [README.md](README.md) | 각 서비스 실행법·env 표·이벤트 계약(day-9 이벤트 3종, `audio/` 절 포함)·검증. **명령은 여기가 원본** |
| [agent-latency-notes.md](agent-latency-notes.md) | 에이전트 홉 분해·기각된 가설·지연 조사 런북 |
| [platform-notes.md](platform-notes.md) | RPi4 vs IdeaPad, Piper 실측, RPi 투영·빌드 블로커 |
| [hardware-notes.md](hardware-notes.md) | 마이크/스피커 비교·확정 BOM·AEC 원리·**쿨링 계획** |
| [음성IO-로드맵.md](음성IO-로드맵.md) | 3단계 로드맵 + 최종 아키텍처 결정 |
| [뇌중심-로드맵.md](뇌중심-로드맵.md) | STT·TTS Azure 전환 결정·실측(트랙 A·B 완료), 뇌는 RPi에 |
| [다음-개선-후보.md](다음-개선-후보.md) | **기계적 개선** 후보·순서: 안정화, 얼굴 파츠, 핫워드, 별도 미디어 스피커 |
| [뇌-상호작용-로드맵.md](뇌-상호작용-로드맵.md) | **뇌↔시스템 계층**: 권한 구멍(Read·WebFetch 전역), D-0 도구 규약, 도구 목록, 시스템→뇌 신호, 가상 케이스 14개 |
| [clova-track/](clova-track/) | 클로바 리버싱·분해 트랙 |
