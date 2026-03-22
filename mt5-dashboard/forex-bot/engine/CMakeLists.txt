cmake_minimum_required(VERSION 3.16)
project(forex_engine CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Release build flags
if(MSVC)
    set(CMAKE_CXX_FLAGS_RELEASE "/O2 /GL")
else()
    set(CMAKE_CXX_FLAGS_RELEASE "-O3 -march=native")
endif()

add_library(forex_engine SHARED
    indicators.cpp
    signals.cpp
)

target_compile_options(forex_engine PRIVATE
    $<$<CXX_COMPILER_ID:MSVC>:/W4>
    $<$<NOT:$<CXX_COMPILER_ID:MSVC>>:-Wall -Wextra>
)

# Windows: no lib prefix, .dll suffix
if(WIN32)
    set_target_properties(forex_engine PROPERTIES
        PREFIX ""
        OUTPUT_NAME "forex_engine"
        SUFFIX ".dll"
    )
else()
    set_target_properties(forex_engine PROPERTIES
        OUTPUT_NAME "forex_engine"
    )
endif()

# Install to project root for easy Python loading
install(TARGETS forex_engine DESTINATION ${CMAKE_SOURCE_DIR}/..)

# ── Build instructions printed at configure time ──────────────────────────
message(STATUS "")
message(STATUS "  Build:   cmake --build . --config Release")
message(STATUS "  Output:  forex_engine.dll (Win) | libforex_engine.so (Linux)")
message(STATUS "")