@echo off
rem Разовая подготовка: ставит то, что нужно программе для работы.
cd /d "%~dp0"
echo Устанавливаю необходимое...
python -m pip install -r requirements.txt
echo.
echo Готово. Теперь запускайте файл "Запустить.bat".
pause
