# 🐟 Marine Fish Detection and Classification GUI

A desktop application for automated marine fish detection, classification, tracking, and analysis from underwater video.

![Platform](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Python](https://img.shields.io/badge/python-managed%20by%20Pixi-orange)

---

## Overview

Marine Fish Detection and Classification GUI provides an accessible graphical interface for processing underwater video using AI-driven fish detection and classification.

The application allows researchers and marine scientists to incorporate AI tools within their underwater marine analysis without requiring programming experience.

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

> **Requirements:** Windows or Linux (Ubuntu 24.04) operating system, an internet connection, and approximately **5 GB of free disk space**.

### Windows Installation 
#### 1. Install Git and Pixi

Open **PowerShell**:

Press `Win`, type `PowerShell`, and press Enter.

Copy and run:

```powershell
winget install --id Git.Git -e --source winget
powershell -ExecutionPolicy ByPass -c "irm -useb https://pixi.sh/install.ps1 | iex"
```

When both installations finish, **close PowerShell and open it again**.

#### 2. Install the Application

Copy and run:

```powershell
cd "$HOME\Desktop"
git lfs install
git clone https://github.com/Brad420169/Marine-Fish-Detection-and-Classification-GUI.git
cd Marine-Fish-Detection-and-Classification-GUI
git lfs pull
pixi install
```

> **Note:** The first installation may take several minutes while the application environment and model weights are downloaded.

#### 3. Launch

Open the **Marine-Fish-Detection-and-Classification-GUI** folder on your Desktop and double-click:

**`launch_gui.bat`**

That's it. Future launches only require double-clicking `launch_gui.bat`.

> **Optional:** Right-click `launch_gui.bat` → **Send to → Desktop (create shortcut)** to create a desktop launcher.

---

### Linux (Ubuntu) Installation

#### 1. Install Git and Pixi
Open **Terminal**:
Press `Ctrl+Alt+T` or search for **Terminal** in your applications.
Copy and run:
```bash
sudo apt update && sudo apt install -y git git-lfs
curl -fsSL https://pixi.sh/install.sh | bash
```
When both installations finish, **close the terminal and open it again**.

#### 2. Install the Application
Copy and run:
```bash
cd ~/Desktop
git lfs install
git clone https://github.com/Brad420169/Marine-Fish-Detection-and-Classification-GUI.git
cd Marine-Fish-Detection-and-Classification-GUI
git lfs pull
pixi install
```
> **Note:** The first installation may take several minutes while the application environment and model weights are downloaded.
> 
#### 3. Launch
Open a terminal inside the **Marine-Fish-Detection-and-Classification-GUI** folder and run:
```bash
pixi run start
```
> **Optional:** If the repository includes a `launch_gui.sh` script, make it executable and run it instead:
> ```bash
> chmod +x launch_gui.sh
> bash launch_gui.sh
> ```
> You can then double-click it in your file manager (**Run as Program**) for future launches.
>
> ---

## Using the Application

### 1. Create a Project

When the application opens:

1. Enter a project name.
2. Select where you want detection results to be stored.
3. Click **Create**.

Your detection runs will be organised under this project.

### 2. Select a Video

Select a video using the **Video Input** section or drag and drop a video directly into the application.

Supported formats:

- `.mp4`
- `.avi`
- `.mov`
- `.mkv`

### 3. Select a Model

Choose a marine fish detection model from the **Model Input** section.

Detection models are included with the application.

Additional compatible YOLO `.pt` model weights can be added using **Add model weights**.

### 4. Configure Detection Settings

The following parameters can be adjusted before running detection:

| Setting | Description |
|---|---|
| **Detection confidence** | Minimum confidence required for a detection to be accepted |
| **Overlap sensitivity** | Controls how overlapping detections are handled |
| **Flag for review below** | Accepted detections below this confidence are flagged for manual review |

Values can be changed using either the slider or the numeric field.

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
- Unique tracks
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

---

## Troubleshooting

### `git` is not recognised

Close PowerShell and open it again.

If the problem continues, restart Windows and try again.

### `pixi` is not recognised

Close PowerShell and open it again.

Then check:

```powershell
pixi --version
```

### Model files did not download

Open PowerShell in the application folder and run:

```powershell
git lfs install
git lfs pull
```

### The application does not launch

Open PowerShell in the application folder and run:

```powershell
pixi install
pixi run GUI
```

Any startup errors will then be displayed in PowerShell.

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

To launch the application from PowerShell:

```powershell
pixi run GUI
```

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
