Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "WordFunc.nsh"
!include "FileFunc.nsh"
Var PreviousDir
Var LegacyDir
Var ExistingVersion
Var UpgradeText
Name "GeoTrace ${VERSION}"
OutFile "${OUTPUT}"
InstallDir "$PROGRAMFILES64\GeoTrace"
InstallDirRegKey HKLM "Software\GeoTrace" "InstallDir"
RequestExecutionLevel admin
SetCompressor /SOLID lzma
CRCCheck force
AllowSkipFiles off
!ifndef PAYLOAD_KB
!define PAYLOAD_KB 0
!endif
BrandingText "TJS TECH × TJS HORIZON"
VIProductVersion "${VERSION}.${BUILD}"
VIAddVersionKey "ProductName" "GeoTrace"
VIAddVersionKey "CompanyName" "TJS PRIME · TJS MERIDIAN"
VIAddVersionKey "LegalCopyright" "TJS PRIME · TJS MERIDIAN"
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
!define MUI_WELCOMEPAGE_TEXT "Оцифровка карт и работа с участками.$\r$\n$\r$\nПолигоны и линии, несколько листов, оформление и выпуск карт.$\r$\n$\r$\n$UpgradeText$\r$\nСборка ${BUILD}"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_FUNCTION LaunchGeoTrace
!define MUI_FINISHPAGE_RUN_TEXT "Открыть GeoTrace"
!define MUI_FINISHPAGE_RUN_NOTCHECKED
!define MUI_FINISHPAGE_TEXT "GeoTrace установлен.$\r$\n$\r$\nРуководство доступно в меню «О программе». Ваши карты и проекты не изменены."
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Russian"
Function .onInit
  SetRegView 64
  SetShellVarContext all
  ReadRegStr $PreviousDir HKLM "Software\GeoTrace" "InstallDir"
  ReadRegStr $ExistingVersion HKLM "Software\GeoTrace" "Version"
  ReadRegStr $LegacyDir HKCU "Software\GeoTrace" "InstallDir"
  ${If} $PreviousDir != ""
    GetFullPathName $PreviousDir "$PreviousDir\."
  ${EndIf}
  ${If} $LegacyDir != ""
    GetFullPathName $LegacyDir "$LegacyDir\."
    ${GetRoot} "$LegacyDir" $0
    ${If} $LegacyDir == "$0\"
    ${OrIf} $LegacyDir == "$WINDIR"
    ${OrIf} $LegacyDir == "$PROGRAMFILES64"
      StrCpy $LegacyDir ""
    ${EndIf}
  ${EndIf}
  StrCpy $UpgradeText ""
  ${If} $PreviousDir != ""
    ${GetParameters} $R0
    ClearErrors
    ${GetOptions} "$R0" "/D=" $R1
    ${If} ${Errors}
      StrCpy $INSTDIR $PreviousDir
    ${EndIf}
    StrCpy $UpgradeText "Предыдущая версия будет обновлена. Карты, проекты и настройки сохранятся."
  ${ElseIf} $LegacyDir != ""
    StrCpy $UpgradeText "Предыдущая установка будет перенесена в Program Files. Проекты и настройки сохранятся."
  ${EndIf}
  ${If} $ExistingVersion != ""
    ${VersionCompare} "$ExistingVersion" "${VERSION}" $0
    ${If} $0 == 1
      MessageBox MB_OK|MB_ICONINFORMATION "Уже установлена более новая версия GeoTrace." /SD IDOK
      SetErrorLevel 2
      Abort
    ${EndIf}
  ${EndIf}
FunctionEnd

Function .onVerifyInstDir
  ${GetRoot} "$INSTDIR" $0
  ${If} $INSTDIR == "$0\"
  ${OrIf} $INSTDIR == "$PROGRAMFILES64"
  ${OrIf} $INSTDIR == "$WINDIR"
    Abort
  ${EndIf}
FunctionEnd

Function CheckClosed
  Exch $0
  IfFileExists "$0\GeoTrace.exe" 0 closed
  retry:
  System::Call 'kernel32::CreateFileW(w "$0\GeoTrace.exe", i 0x40000000, i 0, p 0, i 3, i 0, p 0) p.r1'
  ${If} $1 == -1
    MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "Закройте GeoTrace перед обновлением и нажмите «Повторить»." /SD IDCANCEL IDRETRY retry
    SetErrorLevel 2
    Abort
  ${EndIf}
  System::Call 'kernel32::CloseHandle(p r1)'
  closed:
  Pop $0
FunctionEnd

Function LaunchGeoTrace
  ; Ask the existing desktop shell to launch the application with the user's
  ; ordinary token, rather than inheriting the installer's elevated token.
  ExecShell "open" "$WINDIR\explorer.exe" '"$INSTDIR\GeoTrace.exe"'
FunctionEnd

Section "GeoTrace" MainSection
  AddSize ${PAYLOAD_KB}
  GetFullPathName $INSTDIR "$INSTDIR\."
  IfFileExists "$INSTDIR\_internal\*.*" 0 target_owned
  IfFileExists "$INSTDIR\GeoTrace.exe" target_owned
  MessageBox MB_OK|MB_ICONSTOP "Выберите отдельную папку для GeoTrace: в этой папке есть файлы другой программы." /SD IDOK
  SetErrorLevel 2
  Abort
  target_owned:
  SetRegView 64
  SetShellVarContext all
  Push "$INSTDIR"
  Call CheckClosed
  ${If} $LegacyDir != ""
    Push "$LegacyDir"
    Call CheckClosed
  ${EndIf}
  ; Unpack before touching the installed payload. Keep the old executable and
  ; libraries until copying succeeds, so a failed update can roll back.
  InitPluginsDir
  SetOutPath "$PLUGINSDIR\payload"
  ClearErrors
  File /r "${APPDIR}\*.*"
  IfErrors unpack_failed
  IfFileExists "$INSTDIR\GeoTrace.previous.exe" backup_exists
  IfFileExists "$INSTDIR\_internal.previous\*.*" backup_exists
  CreateDirectory "$INSTDIR"
  ClearErrors
  IfFileExists "$INSTDIR\GeoTrace.exe" 0 +2
    Rename "$INSTDIR\GeoTrace.exe" "$INSTDIR\GeoTrace.previous.exe"
  IfErrors rename_failed
  IfFileExists "$INSTDIR\_internal\*.*" 0 +2
    Rename "$INSTDIR\_internal" "$INSTDIR\_internal.previous"
  IfErrors rename_failed
  ClearErrors
  CopyFiles /SILENT "$PLUGINSDIR\payload\*.*" "$INSTDIR"
  IfErrors copy_failed
  Delete "$INSTDIR\GeoTrace.previous.exe"
  RMDir /r "$INSTDIR\_internal.previous"
  SetOutPath "$INSTDIR"
  Goto payload_ready
  copy_failed:
    Delete "$INSTDIR\GeoTrace.exe"
    RMDir /r "$INSTDIR\_internal"
    IfFileExists "$INSTDIR\GeoTrace.previous.exe" 0 +2
      Rename "$INSTDIR\GeoTrace.previous.exe" "$INSTDIR\GeoTrace.exe"
    IfFileExists "$INSTDIR\_internal.previous\*.*" 0 +2
      Rename "$INSTDIR\_internal.previous" "$INSTDIR\_internal"
    MessageBox MB_OK|MB_ICONSTOP "Не удалось обновить файлы. Прежняя версия восстановлена." /SD IDOK
    SetErrorLevel 2
    Abort
  rename_failed:
    IfFileExists "$INSTDIR\GeoTrace.previous.exe" 0 +2
      Rename "$INSTDIR\GeoTrace.previous.exe" "$INSTDIR\GeoTrace.exe"
    MessageBox MB_OK|MB_ICONSTOP "Не удалось подготовить обновление. Закройте GeoTrace и повторите установку." /SD IDOK
    SetErrorLevel 2
    Abort
  unpack_failed:
    MessageBox MB_OK|MB_ICONSTOP "Не удалось подготовить файлы. Предыдущая установка не изменена." /SD IDOK
    SetErrorLevel 2
    Abort
  backup_exists:
    MessageBox MB_OK|MB_ICONSTOP "Обнаружена копия незавершённого обновления. Завершите восстановление прежней установки." /SD IDOK
    SetErrorLevel 2
    Abort
  payload_ready:
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$SMPROGRAMS\GeoTrace"
  CreateShortcut "$SMPROGRAMS\GeoTrace\GeoTrace.lnk" "$INSTDIR\GeoTrace.exe"
  CreateShortcut "$SMPROGRAMS\GeoTrace\Удалить GeoTrace.lnk" "$INSTDIR\Uninstall.exe"
  ${If} $LegacyDir != ""
  ${AndIf} $LegacyDir != $INSTDIR
    ; Never execute an old, user-writable uninstaller with elevated privileges.
    Delete "$LegacyDir\GeoTrace.exe"
    Delete "$LegacyDir\Uninstall.exe"
    RMDir /r "$LegacyDir\_internal"
    RMDir "$LegacyDir"
    SetShellVarContext current
    Delete "$SMPROGRAMS\GeoTrace\GeoTrace.lnk"
    Delete "$SMPROGRAMS\GeoTrace\Удалить GeoTrace.lnk"
    RMDir "$SMPROGRAMS\GeoTrace"
    SetShellVarContext all
    DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\GeoTrace"
    DeleteRegValue HKCU "Software\GeoTrace" "InstallDir"
    DeleteRegKey /ifempty HKCU "Software\GeoTrace"
  ${EndIf}
  WriteRegStr HKLM "Software\GeoTrace" "InstallDir" "$INSTDIR"
  WriteRegStr HKLM "Software\GeoTrace" "Version" "${VERSION}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\GeoTrace" "DisplayName" "GeoTrace ${VERSION}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\GeoTrace" "DisplayVersion" "${VERSION} build ${BUILD}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\GeoTrace" "Publisher" "TJS PRIME · TJS MERIDIAN"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\GeoTrace" "DisplayIcon" "$INSTDIR\GeoTrace.exe"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\GeoTrace" "UninstallString" '"$INSTDIR\Uninstall.exe"'
SectionEnd
Section "Uninstall"
  SetRegView 64
  SetShellVarContext all
  Delete "$SMPROGRAMS\GeoTrace\GeoTrace.lnk"
  Delete "$SMPROGRAMS\GeoTrace\Удалить GeoTrace.lnk"
  RMDir "$SMPROGRAMS\GeoTrace"
  Delete "$INSTDIR\GeoTrace.exe"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir /r "$INSTDIR\_internal"
  RMDir "$INSTDIR"
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\GeoTrace"
  DeleteRegKey HKLM "Software\GeoTrace"
SectionEnd
