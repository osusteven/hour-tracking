@echo off
REM Builds a stand-alone HourTracker.exe in the dist folder (Windows).
REM Needs Python from python.org. Run once from this folder: build_exe.bat
py -m pip install --upgrade pyinstaller openpyxl || goto :error
py -m PyInstaller --noconfirm --onefile --windowed --name HourTracker HourTracker.pyw || goto :error
echo.
echo Done: dist\HourTracker.exe
goto :eof
:error
echo Build failed.
exit /b 1
