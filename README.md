# 🐟 Marine Fish Detection and Classification GUI

A desktop application for automated marine fish detection, classification, tracking, and analysis from underwater video.

![Platform](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Python](https://img.shields.io/badge/python-managed%20by%20Pixi-orange)

---

## Overview

The Marine Fish Detection and Classification GUI provides an accessible graphical interface for processing underwater video using AI-driven fish detection and classification.

The application lets researchers and marine scientists incorporate AI tools into underwater marine analysis without requiring programming experience.

### Features

- Automated fish detection and species classification
- Multi-object tracking
- Max-N abundance estimation by species
- Annotated video output
- Species-level detection summaries
- Low-confidence detection review
- Detection summary histograms and charts
- CSV result exports
- Project-based organisation of detection runs
- Support for additional compatible YOLO model weights (future support for additional AI models)

---

## Quick Start
> **Requirements:** Windows or Linux (Ubuntu 24.04), and approximately **5 GB of free disk space**.

### 1. Install Git and Pixi

**Windows** — open **PowerShell** (`Win` → type `PowerShell` → Enter):
```powershell
winget install --id Git.Git -e --source winget
powershell -ExecutionPolicy ByPass -c "irm -useb https://pixi.sh/install.ps1 | iex"
```

**Linux (Ubuntu)** — open **Terminal** (`Ctrl+Alt+T`):
```bash
sudo apt update && sudo apt install -y git git-lfs
curl -fsSL https://pixi.sh/install.sh | bash
```

When installation finishes, **close and reopen** PowerShell/Terminal.

### 2. Install the Application

**Windows** (PowerShell):
```powershell
cd "$HOME\Desktop"
git lfs install
git clone https://github.com/Brad420169/Marine-Fish-Detection-and-Classification-GUI.git
cd Marine-Fish-Detection-and-Classification-GUI
git lfs pull
pixi install
```

**Linux** (Terminal):
```bash
cd ~/Desktop
git lfs install
git clone https://github.com/Brad420169/Marine-Fish-Detection-and-Classification-GUI.git
cd Marine-Fish-Detection-and-Classification-GUI
git lfs pull
pixi install
chmod +x launch_gui.sh
```
### 3. Create a Desktop Shortcut

**Windows** — open the **Marine-Fish-Detection-and-Classification-GUI** folder on your Desktop, right-click **`launch_gui.bat`** → **Send to → Desktop (create shortcut)**.

**Linux** — from the same terminal (still inside the project folder), copy and paste the entire block below, then press Enter:

```bash
PROJECT_DIR="$(pwd)"
ICON_PNG="$PROJECT_DIR/assets/icon.png"

DESKTOP_FILE="$HOME/.local/share/applications/marine-fish-gui.desktop"
cat > "$DESKTOP_FILE" << EOF
[Desktop Entry]
Type=Application
Name=Marine Fish Detection GUI
Comment=Launch the Marine Fish Detection and Classification GUI
Exec=bash -c "cd '$PROJECT_DIR' && ./launch_gui.sh"
Icon=$ICON_PNG
Terminal=true
Categories=Utility;
EOF

chmod +x "$DESKTOP_FILE"
cp "$DESKTOP_FILE" "$HOME/Desktop/"
chmod +x "$HOME/Desktop/marine-fish-gui.desktop"
gio set "$HOME/Desktop/marine-fish-gui.desktop" metadata::trusted true 2>/dev/null || true

gtk-update-icon-cache "$HOME/.local/share/icons" 2>/dev/null
nautilus -q
```

You'll see a new icon called **"Marine Fish Detection GUI"** on your Desktop.

### 4. Launch

Double-click the **Marine Fish Detection GUI** icon on your Desktop.

- **Windows:** launches immediately.
- **Linux:** the first time, you may be asked if you trust the shortcut — click **"Trust and Launch"**. If double-clicking does nothing, right-click the icon and choose **"Allow Launching"**, then try again.

That's it — every future launch is just this same double-click.
## Using the Application

### 1. Create a Project

When the application opens:

1. Enter a project name.
2. Select where you want detection results to be stored.
3. Click **Create**.

Your detection runs will be organised under this project.

### 2. Select a Video

Select a video using the **Video Input** section or drag and drop a video directly into the application.

### 3. Select a Model

Choose a marine fish detection model from the **Model Input** section.

The application includes default detection models.

Additional YOLO `.pt` model weights can be added using **Add model weights**.

### 4. Configure Detection Settings

The following parameters can be adjusted before running detection:

| Setting | Description |
|---|---|
| **Detection confidence** | Minimum confidence required for a detection to be accepted |
| **Overlap sensitivity** | Controls how overlapping detection boxes are handled (Higher values can capture closely bundled fish more accurately, at the cost of occasional double/triple detections)|
| **Flag for review below** | Accepted detections below this confidence are flagged for manual review |

Change values using either the slider or the numeric field.

Hover over a parameter name in the application for additional information.

The default values provide a reasonable starting point.

### 5. Run Detection

Click **▶ Run Detection**.

While the video is being processed, the application displays:

- Detection progress
- Frames processed
- Processing speed
- Elapsed time
- Estimated time remaining
- Processing device

The **Results** page opens automatically when processing is complete.

---

## Results

The Results page provides a summary of the completed detection run and access to its generated outputs.

### Annotated Video

The annotated video contains detected fish with:

- Bounding boxes
- Species classifications
- Confidence scores
- Frame numbers
- Video timestamps

The video can be opened directly from the Results page.

### Detection Summary

The application generates:

**`track_summary.csv`**

This contains species-level information including:

- Species
- Max-N
- Max-N timestamp
- First and last observation
- Visible duration
- Unique tracks (generated by AI tracker BotSort)
- Total detections
- Mean detection confidence

### Low-Confidence Review

The application generates:

**`low_confidence_review.csv`**

This contains accepted detections below the selected review threshold so uncertain detections can be identified for manual inspection.

### Max-N Example Frames

Example frames are saved for reported Max-N observations, with the relevant detections highlighted.

These can be used to visually inspect the fish contributing to the Max-N estimate.

### Summary Charts

The Results page includes charts showing:

- Peak abundance (Max-N)
- Total detections
- Visible duration
- Mean detection confidence

Results can also be exported as a summary figure.

### Past Runs

Previous detection runs can be reopened from the **Past Runs** section without processing the video again.

---

## Updating

To update the application, open PowerShell in the application folder and run:

```powershell
git pull
git lfs pull
pixi install
```

Then launch normally using:

**`launch_gui.bat`**

Or the Linux desktop shortcut

---

## Troubleshooting

 
### `git` is not recognised / not found
**Windows & Linux** Close and reopen the terminal. If the problem continues, confirm it is installed with `git --version`.
 
### `pixi` is not recognised / not found
Close and reopen PowerShell/Terminal, then check:
```
pixi --version
```
**Linux:** if it still isn't found, `pixi` may not be on your `PATH` for non-login shells. Run `which pixi` to find its location, then use that full path when running `pixi` commands (or add it to `~/.bashrc`).
 
### Model files did not download
Open PowerShell/Terminal in the application folder and run:
```
git lfs install
git lfs pull
```
 
### The application does not launch
Open PowerShell/Terminal in the application folder and run:
```
pixi install
pixi run GUI
```
Any startup errors will then be displayed in the terminal.
 
**Linux only:** if double-clicking the Desktop shortcut does nothing but running `./launch_gui.sh` from a terminal works, this is a known Linux desktop-file quirk, not a bug in the app. Right-click the shortcut → **Allow Launching**, and make sure it's marked executable:
```bash
chmod +x ~/Desktop/marine-fish-gui.desktop
```

### Detection is slow

Processing speed depends heavily on the available hardware.

A compatible GPU can significantly improve detection performance. Systems without a compatible GPU can use CPU processing, but processing will generally be slower.

---

## Project Structure

```text
Marine-Fish-Detection-and-Classification-GUI/
│
├── assets/             Application icons and graphics
├── models/             Marine fish detection model weights
├── scripts/            Application source code
│
├── launch_gui.bat      Windows launcher
├── pixi.toml           Environment configuration
├── pixi.lock           Locked dependency environment
├── .gitattributes      Git LFS configuration
├── .gitignore          Local/generated file exclusions
├── LICENSE             Software license
└── README.md
```

Application environments and user-created project information are generated locally and are not stored in the repository.

Detection outputs are stored in the location selected by the user when creating a project.

---

## Development

The application is written in Python and uses:

- PyQt6
- Ultralytics YOLO
- PyTorch
- OpenCV
- Matplotlib
- NumPy

The development and runtime environment is managed using Pixi.

### Planned Features

Future development may include:

- Support for additional object detection architectures such as RF-DETR
- Expanded model management
- Improved detection review and correction tools
- Additional result visualisation and analysis options

---

## Model and Dataset Attribution

The included marine fish detection models were trained using the **Kona, Hawaii Dataset**, published by ReefOSHawaii on Roboflow Universe.

**Dataset citation:**

> ReefOSHawaii. (2022). *Kona, Hawaii Dataset*. Roboflow Universe.

[Kona, Hawaii Dataset — Roboflow Universe](https://universe.roboflow.com/reefoshawaii/kona-hawaii)

The dataset is licensed under the **Creative Commons Attribution 4.0 International (CC BY 4.0) License**.

[Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/)

The model weights included in this repository were trained by the author of this project using the Kona, Hawaii Dataset. They are not original model weights supplied by ReefOSHawaii or Roboflow.

---

## License

The application source code is licensed under the **MIT License**. See [`LICENSE`](LICENSE) for details.

The included model weights were trained using the Kona, Hawaii Dataset described above. The underlying training dataset is provided by ReefOSHawaii under the **Creative Commons Attribution 4.0 International (CC BY 4.0) License**.
