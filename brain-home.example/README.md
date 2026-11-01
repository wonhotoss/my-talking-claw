# brain-home.example — 두뇌의 집 템플릿

day-10 설계(day-10.md): 두뇌 계층은 코드가 아니라 **관리자의 설정**이다. 이 디렉터리를
복사해 두뇌의 집으로 쓰고, 게이트웨이에 `AGENT_BRAIN=sdk BRAIN_HOME=<복사한 곳>`을 준다.
그 뒤로는 관리자가 그 디렉터리에서 `claude`를 열어 "말투를 바꿔", "가족 정보를 적어" 하면 된다.

```bash
cp -r brain-home.example ~/claw-home      # RPi: ~/claw-home이 이미 빈 workdir로 쓰이고 있었다
cd ~/claw-home && claude                  # 한 번 열어 "이 폴더를 신뢰합니까?"에 예 → 바로 종료해도 된다
```

**신뢰 수락은 필수다.** 신뢰되지 않은 폴더에서는 Claude Code가 `.claude/settings.json`의 `allow` 규칙을
**조용히 무시**한다(day-10 실기: "Ignoring 5 permissions.allow entries … this workspace has not been
trusted"). 그러면 두뇌가 `memory.md`도 `schedule/`도 못 쓰고 턴마다 사과만 한다. 게이트웨이는 기동
때 `~/.claude.json`의 `projects[<집>].hasTrustDialogAccepted`를 확인하고 없으면 **기동을 거부**한다.

| 파일 | 누가 고치나 | 언제 반영되나 |
| --- | --- | --- |
| `CLAUDE.md` | 관리자 | 다음 턴부터(대화에 맥락으로 들어간다) |
| `system-prompt.md` | 관리자 | **새 세션부터** — 시스템 프롬프트는 세션 첫 요청에 기록된다. 바꿨으면 `session.json`을 지워 새 세션으로 |
| `.claude/settings.json` | 관리자 | 게이트웨이 재시작 |
| `memory.md` | 두뇌(Edit), 관리자 | 다음 턴부터 |
| `schedule/*.json` | 두뇌(Write), 관리자 | 스케줄러 다음 폴링(기본 5초) |
| `session.json` | 게이트웨이 | 런타임. 지우면 새 세션 |

## 단단한 층과 무른 층
- **단단한 층(호스트가 집행, 모델이 무시 못 함)**: `.claude/settings.json`의 모델·권한 모드·허용/금지 도구. `deny`에 적힌 도구는 정의 자체가 제거되고, `dontAsk`는 승인 요청을 거부로 바꾼다. `Edit(memory.md)`·`Edit(schedule/*)`만 쓰기가 허용된다.
- **무른 층(문서, 모델이 따르는 만큼)**: `CLAUDE.md`의 말투·검열·가족 정보·파일 규약. 짧고 구체적일수록 잘 지킨다. 어긋나는 것이 보이면 그때 훅이나 규칙(단단한 층)으로 올린다.
- 어긋남은 **보인다**: 예약은 `schedule/`, 기억은 `memory.md`, 도구 사용은 게이트웨이 로그의 `notice`.

## 세션을 미리 가르쳐 넘기기
이 디렉터리에서 `claude`를 대화형으로 열어 가르친 뒤, 그 세션 id를 `session.json`에
`{"session_id": "<uuid>"}`로 적으면 게이트웨이가 그 세션을 이어받는다(`/status`나
`claude --resume` 목록에서 id를 본다).
