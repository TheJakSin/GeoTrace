Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"
Name "GeoTrace ${VERSION}"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\GeoTrace"
InstallDirRegKey HKCU "Software\GeoTrace" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
BrandingText "TJS TECH × TJS HORIZON"
VIProductVersion "1.0.0.${BUILD}"
VIAddVersionKey "ProductName" "GeoTrace"
VIAddVersionKey "CompanyName" "TJS PRIME · TJS MERIDIAN"
VIAddVersionKey "FileDescription" "GeoTrace Setup"
VIAddVersionKey "FileVersion" "${VERSION} build ${BUILD}"
!define MUI_ICON "${ASSETS}\GeoTrace.ico"
!define MUI_UNICON "${ASSETS}\GeoTrace.ico"
!define MUI_WELCOMEFINISHPAGE_BITMAP "${ASSETS}\wizard.bmp"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_BITMAP "${ASSETS}\header.bmp"
!define MUI_BGCOLOR "FFFFFF"
!define MUI_TEXTCOLOR "303944"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "GeoTrace ${VERSION}"
!define MUI_WELCOMEPAGE_TEXT "Оцифровка карт и работа с участками.$\r$\n$\r$\nПолигоны и линии, несколько листов, оформление и выпуск карт.$\r$\n$\r$\nСборка ${BUILD}$\r$\nTJS PRIME · TJS MERIDIAN$\r$\nTJS TECH × TJS HORIZON"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\GeoTrace.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Открыть GeoTrace"
!define MUI_FINISHPAGE_RUN_NOTCHECKED
!define MUI_FINISHPAGE_TEXT "GeoTrace установлен.$\r$\n$\r$\nРуководство доступно в меню «О программе». Ваши карты и проекты не изменены."
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Russian"
Section "GeoTrace" MainSection
  SetOutPath "$INSTDIR"
  File /r "${APPDIR}\*.*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$SMPROGRAMS\GeoTrace"
  CreateShortcut "$SMPROGRAMS\GeoTrace\GeoTrace.lnk" "$INSTDIR\GeoTrace.exe"
  CreateShortcut "$SMPROGRAMS\GeoTrace\Удалить GeoTrace.lnk" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\GeoTrace" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\GeoTrace" "DisplayName" "GeoTrace ${VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\GeoTrace" "DisplayVersion" "${VERSION} build ${BUILD}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\GeoTrace" "Publisher" "TJS PRIME · TJS MERIDIAN"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\GeoTrace" "DisplayIcon" "$INSTDIR\GeoTrace.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\GeoTrace" "UninstallString" '$"$INSTDIR\Uninstall.exe$"'
SectionEnd
Section "Uninstall"
  Delete "$SMPROGRAMS\GeoTrace\GeoTrace.lnk"
  Delete "$SMPROGRAMS\GeoTrace\Удалить GeoTrace.lnk"
  RMDir "$SMPROGRAMS\GeoTrace"
  Delete "$INSTDIR\GeoTrace.exe"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir /r "$INSTDIR\_internal"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\GeoTrace"
  DeleteRegKey HKCU "Software\GeoTrace"
SectionEnd
