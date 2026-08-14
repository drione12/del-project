cmake_minimum_required(VERSION 3.20)
project(EverythingClone CXX)

if(NOT WIN32)
    message(FATAL_ERROR
        "This project reads the NTFS MFT and USN journal via Win32 APIs "
        "(winioctl.h) and can only be built on Windows.")
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

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
