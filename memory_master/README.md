# Memory Master

Windows 전용 시스템 대시보드 + 강제 삭제/정리 도구. 이 저장소의 `src/`(Everything 클론,
C++/Win32)와는 완전히 분리된 별도의 Python/PyQt5 앱입니다 — 서로 코드를 공유하지 않고,
빌드/CI도 따로 돕니다.

## 왜 별도 앱인가

목업 디자인(둥근 카드, 원형 게이지, 그라디언트)을 제대로 구현하려면 커스텀 그리기가
많이 필요한데, Qt(QPainter + QSS)가 raw Win32 GDI보다 훨씬 적합하고 검증된 방법입니다.
자세한 배경/설계 결정은 이 세션에서 작성한 계획 문서를 참고하세요 (요약: RAM/CPU/디스크/
네트워크 대시보드, 강제 삭제 파이프라인, 중복 파일/사진 찾기, 시작 프로그램 관리, 프로세스
화이트/블랙리스트 — 5개 사이드바 아이콘에 하나씩 대응).

## 현재 상태

아직 개발 초기 단계입니다. 지금까지 구현된 것:

- `core/path_guard.py` — 핵심 시스템 경로(윈도우 폴더, Program Files, 사용자 프로필 전체
  등)를 강제 삭제로부터 보호하는 로직. 실제 파일시스템을 건드리지 않는 순수 로직이라
  아무 OS에서나 테스트 가능합니다.
- `core/critical_processes.py` — 강제 삭제/블랙리스트 감시가 절대 건드리면 안 되는
  프로세스 목록(자기 자신, PID 0/4, lsass.exe 등 핵심 시스템 프로세스).
- `core/privileges.py` — `MOVEFILE_DELAY_UNTIL_REBOOT`(재부팅 시 삭제 예약)에 필요한
  `SeRestorePrivilege`를 활성화하는 ctypes 코드. `src/privileges.cpp`의 `EnablePrivilege`
  패턴을 그대로 옮긴 것. Windows 전용이라 이 개발 환경(Linux)에서는 실행/테스트 불가능—
  `src/`의 나머지 Win32 코드와 마찬가지로 GitHub Actions에서만 검증됩니다.

## 개발

```powershell
pip install -r requirements.txt
pytest tests -v
```

`tests/`는 순수 로직만 테스트합니다(파일시스템도, 실제 Windows API도 건드리지 않음) —
`ctypes.windll`을 쓰는 코드나 PyQt5 위젯은 Windows에서 GitHub Actions로만 검증됩니다.
