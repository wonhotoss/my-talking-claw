# face-bench — 얼굴 idle 모션이 RPi에서 얼마나 드는가

얼굴을 "살아 있게" 움직이는 비용을 기법별·설정별로 잰다. 앱 코드는 건드리지 않는다. 결과와 해석은 [day-12.md](../../day-12.md).

## 무엇을 재나

| 항목 | 어떻게 |
| --- | --- |
| Chromium CPU, 프로세스 종류별 (browser·renderer·gpu-process·utility) | `/proc/<pid>/stat`, 띄운 브라우저 pid의 **자손 트리**로 찾는다(GPU·유틸리티 프로세스는 `--user-data-dir`을 안 달고 다닌다) |
| 기계 전체 busy | `/proc/stat` |
| 페이지의 실제 rAF 주기 (디스플레이) | CDP `Runtime.evaluate`, 창이 끝난 뒤(프레임을 요청하므로) |
| 페이지가 스스로 부른 rAF 횟수/s | `requestAnimationFrame`을 감싼 카운터 — 프레임을 요청하지 않는다. 쉴 때 잠드는 얼굴이면 한 자리 수, 계속 도는 루프면 ~60 |
| 스크린샷 | CDP `Page.captureScreenshot` → `out/<run>.png` |
| GPU feature status (= `chrome://gpu`) | CDP `SystemInfo.getInfo` |
| 온도·스로틀 | `vcgencmd` |

모든 % 는 **코어 하나 기준**(RPi4는 4코어 = 400%).

## 변형

- **벤치 페이지** [index.html](index.html) `?mode=` — 같은 idle(깜빡임 2.2~5.8 s, 시선 1.8~6 s·50 ms 도약, 숨쉬기 4.2 s, 흔들림 13 s, 시드 고정)을 여섯 가지로 그린다: `static`(정지) · `svg_attr`(전부 SVG 속성을 JS로 매 프레임) · `svg_hybrid`(숨쉬기·흔들림은 `<svg>`에 CSS, 시선·깜빡임은 바뀔 때만 속성 — `claude/kiosk-face-idle`의 방식) · `layers_css`(부위별 HTML 레이어, 움직임은 전부 CSS transform) · `layers_img`(같은 레이어를 한 번 래스터한 PNG로) · `canvas`(2D 캔버스 매 프레임).
- **이벤트 모드**: `events_img`(이미지 레이어, 깜빡임·시선·20 s 드리프트, rAF 없음) · `events_svg`(SVG 하나, 이벤트 동안만 rAF — main이 이 방식으로 구현됐다) · `breathe10_img`(+숨쉬기 10 fps).
- **실제 앱**: main(`#/face?role=viewer`, `#/kiosk` — 2026-11-11부터 이벤트 idle)과 브랜치(`#/kiosk`) × vite dev 서버 / 프로덕션 빌드(`vite preview`) × reduced-motion 유무.
- **GPU**: 기본(래퍼가 `--enable-gpu-rasterization`을 붙인다) / `--disable-gpu`(SwiftShader).

## 실행 (RPi에서, 스택은 내려 둔 채)

```bash
cd ~/Documents/my-talking-claw
```

```bash
uv run scripts/face-bench/bench.py --backend --runs bench_static_gpu bench_layers_css_gpu main_prod_rm
```

`--runs all`이면 전부. 브랜치 변형은 `~/face-idle-branch`에 worktree와 `npm ci`·`npx vite build`가 있어야 한다. 측정 중엔 swayidle을 멈추고 끝나면 `rpi-up.sh down`과 같은 줄로 되살린다. 결과는 `out/run-*.jsonl`(gitignore).

## 함정 (실측)

- Chromium 자식 프로세스는 argv를 공백으로 합친 한 문자열로 바꿔 쓴다 — cmdline을 NUL로 쪼개 플래그를 찾으면 안 맞는다.
- Debian `/usr/bin/chromium`은 셸 래퍼고 `--enable-gpu-rasterization` 등을 스스로 붙인다(`rasterization: enabled_force`의 출처).
- DevTools 원격 포트는 기본이 아닌 `--user-data-dir`에서만 열린다(Chromium 136+). 비브라우저 클라이언트는 `--remote-allow-origins`가 필요하다.
