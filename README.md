PCB INSPECTOR
Automated optical inspection (AOI) of printed circuit boards by comparison with a reference board (Golden Board)

Undergraduate Final Project (TCC) – UTFPR (Federal University of Technology – Paraná, Brazil)
Author: <your full name>


1. DESCRIPTION

PCB Inspector compares the image of a board captured by a USB camera with the image of a defect-free reference board (Golden Board) and reports, for each registered region of interest (ROI), whether it is OK or NOK (not OK). The defects considered are missing components and terminal holes closed with solder.

How it works:
  - The capture is aligned to the golden image by feature matching (ORB + RANSAC homography).
  - Placement check: captures with no board ("Placa Ausente" - board absent) or with the board partly out of frame ("Placa Fora de Posição" - board out of position) are discarded and not logged.
  - Each ROI is evaluated according to its inspection type:
      component - color difference in Lab color space (fraction of pixels with ΔE > 36; NOK above 0.30);
      hole      - terminal hole: a/b color distance inside the hole (NOK above 10);
      metal     - reflective metal parts (e.g., USB connector): edge recall (NOK below 0.80).
  - The result is shown on screen (green/red boxes and virtual OK/NOK LEDs) and automatically logged to a CSV file, together with the raw and annotated images of each board.

Boards used for development and validation: HW-373 charger module (TP4056, USB-C) and, as a generalization test, an ESP32 DevKit.

Note: the user interface is in English; some status messages and the log folder for discarded captures ("descartadas") are in Portuguese.


2. REPOSITORY CONTENTS

  pcb_inspector.py          Main program - Windows version
  pcb_inspector_mac.py      Main program - macOS version (same algorithm and same thresholds)
  requirements.txt          Required Python libraries
  teste_camera_mac.py       Camera diagnostics for macOS (lists the cameras and saves a photo from each one)
  teste_1080_vs_4k.py       Capture test at 1920x1080 and 3840x2160
  analise_1080_vs_4k.py     Analysis of the 1080p vs 4K test
  Golden Boards Images/     Reference images (Testes.png = HW-373; ESP32.png = ESP32 DevKit)
  rois/                     Regions of interest for each golden image (.json files with the same name as the image)
  README.txt                This file in Portuguese

Folders created during use (not included in the repository):
  logs/<golden>_<date>_<time>/   test CSV, raw image (_raw.png) and annotated image of each board
  logs/.../descartadas/          captures discarded by the placement check


3. REQUIREMENTS

  - Python 3.10 or later (developed with Python 3.14)
  - Libraries: opencv-python, numpy, Pillow (versions in requirements.txt)
  - Tkinter (included with the python.org installer; with Homebrew: brew install python-tk)
  - USB camera with 1920x1080 resolution (used in this project in MJPG format, ~27 fps)
  - Constant lighting (LED ring light) and a fixture (tray) that keeps the board in the camera's field of view


4. INSTALLATION

  1. Download the repository: "Code" button > "Download ZIP" on GitHub (or: git clone <repository URL>) and extract the folder.
  2. In a terminal, go to the folder and install the libraries:
     pip install -r requirements.txt        (on macOS: pip3)


5. RUNNING

Windows:
  python pcb_inspector.py

macOS:
  python3 pcb_inspector_mac.py

  If the wrong camera opens (FaceTime, iPhone), set the index of the USB camera:
  PCB_CAMERA_INDEX=1 python3 pcb_inspector_mac.py

  To find the index: python3 teste_camera_mac.py


6. USAGE

  1. "Select Golden Board": capture the reference board with the camera, open an image file, or load a saved setup (golden + ROIs).
  2. "Setup ROIs": draw the regions on the golden image, name each one, and choose its inspection type (component, hole or metal). Use Zoom for small regions; the arrow keys move the selected region and Ctrl+arrows (Cmd+arrows on Mac) resize it. ROIs are saved to rois/<golden name>.json. Use unique region names.
  3. The live camera starts automatically. Place the board on the tray and press Space (or "Capture").
  4. The result stays on screen for 2 s, then the camera resumes for the next board. The history table lists every inspection; double-click a row to open the annotated image.
  5. Results are saved to logs/<golden>_<date>_<time>/ (CSV with date, time, part count, status, number of errors and NOK regions).


7. CONFIGURATION

Parameters are constants at the top of pcb_inspector.py / pcb_inspector_mac.py. Values used in this project:

  CAMERA_INDEX                 1 on Windows; 0 on macOS (or the PCB_CAMERA_INDEX variable)
  CAMERA_WIDTH x CAMERA_HEIGHT 1920 x 1080
  RESULT_DISPLAY_MS            2000 (result display time, in ms)
  ROI_DIFF_RATIO_THRESHOLD     0.30 (threshold for component ROIs)
  HOLE_CHROMA_THRESHOLD        10   (threshold for hole ROIs)
  METAL_EDGE_RECALL_MIN        0.80 (threshold for metal ROIs)
  PLACEMENT_MIN_INLIERS        100  (below this: board absent)
  PLACEMENT_SCALE_RANGE        0.8 to 1.25 (outside this range, or an ROI out of frame: board out of position)

The thresholds were set and frozen before validation. Changing them changes the validated behavior.


8. DIFFERENCES BETWEEN THE WINDOWS AND macOS VERSIONS

The inspection core is identical. Only the operating-system-dependent parts differ:

  Item                          Windows                       macOS
  Camera access                 Media Foundation (CAP_MSMF)   AVFoundation (CAP_AVFOUNDATION)
  Camera index                  fixed (1)                     0 or the PCB_CAMERA_INDEX variable
  Open image from history       os.startfile                  "open" command
  Fonts                         Segoe UI / Arial              Helvetica Neue / macOS Arial Bold
  ROI setup shortcuts           Ctrl                          Ctrl or Cmd


9. NOTES

  - macOS: on the first run, the system asks for camera permission for the application that runs Python (Terminal, VS Code, etc.). If no camera opens, enable it in System Settings > Privacy & Security > Camera, quit it with Cmd+Q and open it again.
  - Camera exposure: in this project, exposure and gain were fixed in the camera driver (exposure -6, gain 32). The program does not change these values. On another computer, lock them before inspecting (on macOS, for example, with the CameraController utility), because auto exposure changes the brightness between captures.
  - ROI files store the path of the golden image. If the project is copied to another folder or computer, the program looks for the image by name in "Golden Boards Images/".
  - Windows: the program sets OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS=0 itself, which reduces camera start-up from ~17 s to ~0.8 s.


10. RESULTS (project summary)

  - Validation: 40 HW-373 boards x 5 runs = 200 captures; per-ROI accuracy of 99.93%; 199/200 correct board verdicts.
  - Timing: median cycle of 9 s per board (operator + capture + processing + display); processing of ~0.46 s per capture.
  - Portability: run on macOS with correct results on 11/11 boards.
  - Generalization: ESP32 DevKit inspected with the same code and the same thresholds, only with a new golden image and new ROIs.
