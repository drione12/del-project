#pragma once

// Shared between app.rc (compiled by the resource compiler, which only
// understands #define - not the C++ enum these command IDs used to live in
// as MenuCommand inside main.cpp) and main.cpp itself, so both agree on the
// exact same numeric command/resource IDs.

#define IDM_FILE_OPEN 2001
#define IDM_FILE_OPENPATH 2002
#define IDM_FILE_COPYPATH 2003
#define IDM_FILE_COPYFULLNAME 2004
#define IDM_FILE_PROPERTIES 2005
#define IDM_FILE_DELETE 2006
#define IDM_FILE_REFRESH 2007
#define IDM_FILE_CLOSE 2008
#define IDM_EDIT_COPY 2009
#define IDM_SEARCH_MATCHCASE 2010
#define IDM_SEARCH_MATCHWHOLEWORD 2011
#define IDM_SEARCH_MATCHPATH 2012
#define IDM_SEARCH_REGEX 2013
#define IDM_VIEW_WINSIZE_SMALL 2014
#define IDM_VIEW_WINSIZE_MEDIUM 2015
#define IDM_VIEW_WINSIZE_LARGE 2016
#define IDM_VIEW_WINSIZE_MAXIMIZE 2017
#define IDM_VIEW_FONTCOLOR 2018
#define IDM_TOOLS_OPTIONS 2019
#define IDM_TRAY_SHOW 2020
#define IDM_TRAY_EXIT 2021

// New in the keyboard-shortcuts round.
#define IDM_EDIT_SELECTALL 2022
#define IDM_EDIT_CUT 2023
#define IDM_FILE_RENAME 2024

#define IDR_ACCELERATORS 1
