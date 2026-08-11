cmake_minimum_required(VERSION 3.20)
project(EverythingClone CXX)

if(NOT WIN32)
    message(FATAL_ERROR
        "This project reads the NTFS MFT and USN journal via Win32 APIs "
        "(winioctl.h) and can only be built on Windows.")
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(EverythingClone
    src/main.cpp
    src/ntfs_index.cpp
    src/usn_watcher.cpp
    src/privileges.cpp
    src/volume_utils.cpp
    src/text_match.cpp
)

target_link_libraries(EverythingClone PRIVATE advapi32)

# Without these, TEXT()-based macros (e.g. SE_BACKUP_NAME) resolve to the
# narrow (char*) charset while every API call in this project uses the
# explicit wide (...W) entry points - keep them consistent.
target_compile_definitions(EverythingClone PRIVATE UNICODE _UNICODE)
