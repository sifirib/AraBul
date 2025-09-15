# AraBul

To generate the desired version as an .exe file, go to the root directory and run the following command:

```bash
python3 -m PyInstaller --onefile --noconsole --icon=appdata/assets/icon.ico --distpath . <version>.py
```

Replace <version>.py with the version you want to build, like v1_12.py or main.py.

# Supported Platforms
GNU/Linux, Windows, MacOS
Tested in: Ubuntu 24.04, Windows 10
