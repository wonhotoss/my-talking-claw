# azure-probe — STT·TTS를 Azure로 옮기기 전의 측정

[뇌중심-로드맵.md](../../뇌중심-로드맵.md) 트랙 A·B가 "실측으로 확인할 것"으로 남긴 항목을 어댑터 코드보다 먼저 재는 독립 프로젝트. `voice/`·`tts/`·`audio/`는 건드리지 않는다.

## 준비 (한 번)

1. Azure Portal에서 **Speech** 리소스 생성 — 가격 계층 **F0**(무료: STT 5시간/월, TTS 50만 자/월), 리전 **Korea Central**(`koreacentral`).
2. 키·리전을 env로. **집(`claw-home`)에는 두지 않는다**(뇌가 Read로 읽는 곳). 운영 때는 `rpi-up.sh`의 env에 들어간다.

```bash
export AZURE_SPEECH_KEY=...
export AZURE_SPEECH_REGION=koreacentral
```

3. 의존성.

```bash
cd scripts/azure-probe && uv sync
```

RPi(aarch64 Debian)에서 Speech SDK는 `libssl`·`libasound2`를 요구한다. `uv sync` 뒤 `uv run python -c "import azure.cognitiveservices.speech"`가 첫 확인 항목.

## 프로브 3개

| 프로브 | 묻는 것 | 실행 |
| --- | --- | --- |
| `stt_file` | ReSpeaker 녹음에 대한 **정확도**(HANDOFF "측정→제조" 오인식 포함). `--whisper-url`을 주면 voice 서비스 결과를 옆 칸에 | `uv run python -m probe.stt_file ~/claw-recordings --language ko-KR --whisper-url http://127.0.0.1:8100` |
| `stt_stream` | **A-2의 실제 이득**: 실시간 속도로 밀어 넣고 마지막 프레임 뒤 최종 텍스트까지 몇 초(`tail`). 연결 pre-open 효과, 꼬리가 partial로만 남는지 | `uv run python -m probe.stt_stream ~/claw-recordings/<one>.wav --language ko-KR --repeat 3` 그리고 `--preconnect`, `--pace burst` |
| `tts` | 문장 길이별 **왕복**, 보이스 비교, **ko-KR viseme ID 매핑표**(모음별 분리 문장의 시간 점유), 16k vs 24k | `uv run python -m probe.tts --voices ko-KR-SunHiNeural ko-KR-InJoonNeural ko-KR-HyunsuMultilingualNeural --preconnect` |

출력은 `out/`에 JSON(과 TTS는 WAV). `out/`은 저장소에 넣지 않는다 — 수치는 day 로그에 표로 옮긴다. `out/tts/<voice>/*.json`은 이후 `tts/app/engines/azure.py` 단위 테스트의 **픽스처**가 된다(네트워크 없는 테스트).

## 읽는 법

- `stt_stream`의 **`tail`**이 지금 STT 6~7 s를 대체하는 숫자다. 로드맵 기대치 0.3~0.7 s.
- `start`(start_continuous_recognition 소요)가 크면 루프는 웨이크워드 시점에 세션을 열어야 한다(차임 0.35 s 안에 가리기).
- `partial-only tail!`이 찍히면 마지막 구간이 `recognized`로 오지 않은 것이다 → 어댑터가 마지막 partial을 폴백으로 잡아야 한다(`analysis.gather_transcript`가 그 규칙). 11-08 실측: 이 현상은 **닫은 직후 `stop_continuous_recognition()`을 불렀을 때만** 났다. 올바른 끝내기는 `stream.close()` → `canceled(EndOfStream)`/`session_stopped` 대기(결과가 먼저 온다) → 정리용 stop. `session end +N`이 그 대기 시간이다.
- TTS 행에 `Timeout while synthesizing … RTF N (threshold 2)`가 연달아 찍히면 서비스가 아니라 **회선**을 의심한다(11-08: RPi 2.4 GHz Wi-Fi가 지속 트래픽에서 손실 10~40%; 같은 키로 데스크탑은 42/42 성공). 공유기까지 한 홉 `ping`부터.
- `tts` 모음 표: 행마다 점유율이 큰 ID 중 **행 사이에 바뀌는 것**이 그 모음의 ID. 자음 ID(19·20·13·14)는 모든 행에 깔린다. `ㅁㅂㅍ` 행은 21이 커야 한다.
- 첫 문장 왕복 0.3~0.5 s면 청크 스트리밍 TTS는 미뤄도 된다(문장 단위 선출력이 이미 있다).

## 테스트 (네트워크·키 불필요)

```bash
cd scripts/azure-probe && uv run pytest
```

순수 분석(`probe/analysis.py`: viseme 시작→구간 변환, 전사 합치기)과 PCM 보조만 검증한다. 이 두 함수는 어댑터가 그대로 가져갈 로직의 원형이다.
