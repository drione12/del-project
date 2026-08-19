cmake_minimum_required(VERSION 3.20)
project(EverythingClone CXX)

if(NOT WIN32)
    message(FATAL_ERROR
        "This project reads the NTFS MFT and USN journal via Win32 APIs "
        "(winioctl.h) and can only be built on Windows.")
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Statically link the MSVC C++ runtime (/MT instead of the default /MD) so
# neither binary below depends on an externally-loaded MSVCP140.dll/
# VCRUNTIME140*.dll at all. This matters specifically for EverythingCore -
# when it's loaded via ctypes into Memory Master's process, PyQt5's own
# bundled Qt5 binaries have *already* loaded their own (older, different
# MSVC-toolset) copy of MSVCP140.dll by the time ctypes.CDLL runs; Windows'
# loader tracks DLLs by name and reuses whatever's already mapped in the
# process regardless of search path, so EverythingCore.dll's own runtime
# calls were silently resolving into that mismatched-ABI copy instead of a
# compatible one - a real access-violation crash (0xc0000005 inside
# MSVCP140.dll, confirmed via Windows Event Viewer on a real crash) the
# instant EC_BuildIndex's std::wstring/std::vector/std::thread-heavy code
# ran, not a bug in this project's own code at all. /MT removes the
# externally-loaded-runtime dependency entirely - EverythingCore.dll now
# carries its own copy of everything it needs. Applied to both targets
# (not just EverythingCore) so EverythingClone.exe also stays a genuinely
# standalone binary with no VC++ Redistributable expectation either way.
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")

add_executable(EverythingClone WIN32
    src/main.cpp
    src/ntfs_index.cpp
    src/usn_watcher.cpp
    src/privileges.cpp
    src/volume_utils.cpp
    src/text_match.cpp
    src/query.cpp
    src/mft_record.cpp
    src/settings.cpp
    src/app.rc
)

target_link_libraries(EverythingClone PRIVATE advapi32 comctl32 shell32 ole32 comdlg32 uuid)

# MSVC's linker auto-generates and embeds its own default manifest; without
# disabling that, it collides with the manifest app.rc embeds explicitly
# (CVTRES fatal error CVT1100: duplicate resource, type:MANIFEST).
target_link_options(EverythingClone PRIVATE "/MANIFEST:NO")

# Without these, TEXT()-based macros (e.g. SE_BACKUP_NAME) resolve to the
# narrow (char*) charset while every API call in this project uses the
# explicit wide (...W) entry points - keep them consistent.
target_compile_definitions(EverythingClone PRIVATE UNICODE _UNICODE)

# Source files are UTF-8. Without /utf-8, MSVC decodes them using the
# active ANSI code page instead, so every non-ASCII (Korean) string
# literal comes out mojibake despite compiling with no errors.
target_compile_options(EverythingClone PRIVATE /utf-8)

# EverythingCore.dll: the same indexing/search engine as EverythingClone.exe
# above (every source file it uses except main.cpp itself, which is the
# .exe's own WinMain/window/menu/UI code - nothing else in this project
# touches a window handle, see src/core_api.h) built as a DLL instead of an
# .exe, with src/core_api.cpp's flat C API as its entry points. This is
# what memory_master/core/fast_search.py loads via ctypes so Memory Master
# gets EverythingClone's engine running in its own process directly,
# instead of spawning and window-embedding a separate EverythingClone.exe
# (the old approach - see memory_master/README.md).
add_library(EverythingCore SHARED
    src/core_api.cpp
    src/ntfs_index.cpp
    src/usn_watcher.cpp
    src/privileges.cpp
    src/volume_utils.cpp
    src/text_match.cpp
    src/query.cpp
    src/mft_record.cpp
    src/settings.cpp
)

# advapi32: privileges.cpp's AdjustTokenPrivileges/LookupPrivilegeValueW.
# shell32: settings.cpp's SHGetFolderPathW. Deliberately not comctl32/ole32/
# comdlg32/uuid - those are main.cpp's own GUI controls/shell context menu/
# OLE drag-drop, none of which this DLL exposes.
target_link_libraries(EverythingCore PRIVATE advapi32 shell32)
target_compile_definitions(EverythingCore PRIVATE UNICODE _UNICODE EVERYTHINGCORE_EXPORTS)
target_compile_options(EverythingCore PRIVATE /utf-8)
