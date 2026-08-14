# Memory Master

Windows 전용 시스템 대시보드 + 강제 삭제/정리 도구. 이 저장소의 `src/`(Everything 클론,
C++/Win32)와는 완전히 분리된 별도의 Python/PyQt5 앱입니다 — 서로 코드를 공유하지 않고,
빌드/CI도 따로 돕니다.

## 왜 별도 앱인가

목업 디자인(둥근 카드, 원형 게이지, 그라디언트)을 제대로 구현하려면 커스텀 그리기가
많이 필요한데, Qt(QPainter + QSS)가 raw Win32 GDI보다 훨씬 적합하고 검증된 방법입니다.

## 기능 (사이드바 4개 아이콘)

- **대시보드** — RAM 원형 게이지, CPU/디스크/네트워크 현황, RAM/CPU/Swap 추이 및 항목별
  비교 타일, RAM+Swap 웨이브 차트, 메모리 할당(사용 중/대기/압축/여유) 바, Quick Actions
  (RAM 최적화, 캐시 정리, 프로세스 상세), 상위 프로세스 목록.
- **정리** — 중복 파일 찾기(MD5), 중복/유사 이미지 찾기(퍼셉추얼 해시 + OpenCV ORB로
  잘린/부분 일치까지 탐지, 폴더 여러 개 지정 가능), 검토 다이얼로그(미리보기 확대/축소,
  우선 유지 폴더 자동 선택), 파일/폴더를 끌어다 놓으면 강제 삭제 파이프라인으로 바로
  연결되는 드롭 영역. 파일/이미지 검색과 강제 삭제 실행 중에는 중지 버튼으로 취소할 수
  있습니다.
- **설정** — 다크/라이트 테마, 항상 위에 표시, 창 투명도, 격리 폴더 위치.
- **시작 프로그램** — 레지스트리 Run 키 + 작업 스케줄러 항목(아이콘 표시), 부팅
  영향도(낮음/보통/높음, 색으로 구분) 추정, 레지스트리 항목 추가/제거.

모든 강제 삭제는 하나의 공통 파이프라인(`core/force_delete.py`)을 거칩니다: 보호 경로
차단 → 사용 중인 프로세스 확인(관리자 권한 없이는 소유권 변경 시도 안 함) → 확인
다이얼로그로 종료될 프로세스 목록 표시 → 실행(진행률/취소 가능, 필요 시 재부팅 후 삭제
예약, 선택적 3-패스 보안 삭제).

## 개발

```powershell
pip install -r requirements.txt
pytest tests -v
```

`tests/`는 순수 로직 + 실제 파일시스템 I/O(임시 파일 기준)를 테스트합니다 — 실제 Windows
API(`ctypes.windll`, `winreg`)를 쓰는 코드는 이 코드가 실행되는 개발 환경(Linux)에서는
GitHub Actions(windows-latest)로만 검증됩니다. PyQt5 위젯은 `QT_QPA_PLATFORM=offscreen`
로 헤드리스 환경에서도 직접 구성/렌더링해 검증합니다 (`scripts/smoke_check.py`).

## 빌드

GitHub Actions에서 워크플로를 수동 실행(`workflow_dispatch`)하면 PyInstaller로
단일 실행 파일을 패키징해 아티팩트로 올립니다 (`.github/workflows/build-memory-master.yml`
의 `package` 잡). 로컬에서 직접 만들려면:

```powershell
pip install pyinstaller
pyinstaller --noconfirm --onefile --windowed --name MemoryMaster --add-data "resources;resources" main.py
```
