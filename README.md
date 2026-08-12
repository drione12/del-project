# EverythingClone

voidtools의 [Everything](https://www.voidtools.com/)이 파일을 즉시 검색할 수 있는 이유가 되는 핵심 메커니즘 — **NTFS MFT 전체 열거 + USN Journal 실시간 감시 + 메모리 상주 인덱스** — 를 그대로 구현한 Windows 전용 GUI 프로그램입니다.

## 동작 원리

1. **초기 인덱싱** (`ntfs_index.cpp`): 폴더를 재귀적으로 순회하는 대신, `DeviceIoControl(FSCTL_ENUM_USN_DATA)` 로 NTFS 볼륨의 MFT 레코드를 한 번에 쭉 열거합니다. 각 레코드는 `(FRN, 부모 FRN, 파일명, 속성)` 만 담고 있어 전체 경로가 아니므로, 검색 시 부모 체인을 따라 올라가며 경로를 조립하고 캐싱합니다.
2. **실시간 갱신** (`usn_watcher.cpp`): 초기 인덱싱 후에는 `FSCTL_QUERY_USN_JOURNAL` / `FSCTL_READ_USN_JOURNAL` 로 USN 변경 저널을 계속 tail 하면서 생성·삭제·이름변경만 인덱스에 반영합니다. 재스캔이 전혀 없습니다.
3. **검색** (`ntfs_index.cpp: Search`): 메모리에 있는 레코드를 선형 스캔하며 대소문자 무시 부분 문자열/와일드카드(`*`, `?`) 매칭. 수백만 건이라도 전부 RAM에 있는 짧은 문자열이라 이 방식으로도 충분히 빠릅니다 — 실제 Everything도 "똑똑한 알고리즘"보다는 이 접근 자체가 빠름의 원천입니다.
4. **UI** (`main.cpp`): Win32 GUI 창. 검색창에 입력할 때마다 즉시 재검색하고, 결과는 가상 리스트뷰(`LVS_OWNERDATA`)로 표시 — 수천 건이 나와도 그때그때 필요한 행만 그려서 버벅이지 않습니다. 결과를 더블클릭하면 탐색기 기본 동작으로 열립니다.

## 빌드

### 방법 1: GitHub Actions (로컬에 아무것도 설치 안 해도 됨)

이 저장소를 push 하면 `.github/workflows/build.yml` 이 GitHub의 Windows 클라우드 러너에서 자동으로 빌드하고, 결과 `EverythingClone.exe` 를 워크플로 실행 결과 페이지의 **Artifacts** 에서 다운로드할 수 있습니다. 로컬에 Visual Studio/CMake를 설치할 필요가 없습니다.

### 방법 2: 로컬에서 직접 빌드 (Windows 필요)

이 리눅스 개발 환경에서는 Win32 저수준 API(`winioctl.h`, MFT/USN 관련 `DeviceIoControl`)를 쓰기 때문에 빌드·실행이 불가능합니다. Windows + Visual Studio (또는 Build Tools) 설치 후:

```powershell
cmake -B build
cmake --build build --config Release
```

제너레이터를 따로 지정하지 않으면 CMake가 설치된 Visual Studio 버전을 알아서 찾습니다.

## 실행

- **관리자 권한 필수**입니다 (`\\.\C:` 같은 raw 볼륨 핸들을 열려면 필요). exe를 우클릭 → "관리자 권한으로 실행"하세요.
- 창이 뜨면 상단에 "인덱싱 중..." 표시 후 NTFS로 포맷된 로컬 고정 드라이브를 자동으로 모두 찾아 병렬로 인덱싱합니다. 끝나면 검색창이 활성화됩니다.
- 검색창에 타이핑하면 바로 아래 목록이 갱신됩니다. 결과를 더블클릭하면 파일/폴더가 열립니다.
- 인덱싱이 0건으로 끝나면(관리자 권한 없이 실행한 경우 등) 상단 상태 표시줄에 안내 문구가 뜹니다.

## 한계 / 다음 단계로 할 만한 것

의도적으로 "핵심 엔진 + 기본 GUI"까지만 구현했습니다. 실제 Everything과 동등하려면 더 필요한 것들:

- **고급 쿼리 문법**: `size:>1gb`, `ext:pdf`, `dm:today`, `AND`/`OR`/`NOT` 같은 필터.
- **인덱스 영속화**: 지금은 실행할 때마다 재인덱싱. 종료 시 인덱스를 디스크에 저장해두면 다음 실행이 즉시 뜸 (Everything의 실제 동작).
- **백그라운드 서비스 + IPC**: 별도 프로세스로 상주시키고 GUI/CLI가 붙었다 떨어졌다 하는 구조 (Everything Service + SDK 방식).
