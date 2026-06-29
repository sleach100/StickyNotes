# Sticky Notes

A Windows desktop sticky notes app built with Python and PySide6.

## Features

- Fixed-size sticky note card grid
- Single-click a card to select it in the side panel
- View-only note browsing until the Edit button is pressed
- Rich text note bodies with bold, italic, underline, bullets, and color
- Local automatic saving with SQLite
- Search across title, body, and tags
- Metadata: title, tags, color, pinned state, created date, updated date
- Priority reminders that reopen a note in a centered topmost window on a selected interval
- Optional priority reminder start date/time before interval repeats begin
- Priority badges and stronger priority card borders in the grid
- Reminder popups with Dismiss and Delete actions
- System tray support with hidden startup, options, and about dialog
- Trash for recovering deleted notes
- Single-instance behavior so only one copy runs at a time
- Remembered main window size
- One local collection of notes

## Data Location

Notes are saved automatically to:

```text
%APPDATA%\StickyNotes\notes.db
```

## Development

Create a virtual environment and install dependencies:

```powershell
.\setup.ps1
```

Run the app:

```powershell
.\run.ps1
```

## Build an EXE

```powershell
.\build.ps1
```

If PowerShell script execution is disabled, run the build command directly:

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconsole --onefile --name StickyNotes --icon src\sticky_notes\assets\StickyNotes.ico --add-data "src\sticky_notes\assets\StickyNotes.ico;assets" --paths src src\sticky_notes\__main__.py
```

The finished executable will be created at:

```text
dist\StickyNotes.exe
```
